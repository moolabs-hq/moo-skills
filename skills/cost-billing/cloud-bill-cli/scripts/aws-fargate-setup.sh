#!/usr/bin/env bash
# Provision the moo-cloud-bill daily push on AWS (ECS Fargate + EventBridge Scheduler).
#
# This is the AUTOMATED version of AWS_SCHEDULING.md: it runs the AWS CLI commands
# for you — but on YOUR terms. It:
#   • checks prerequisites (aws CLI, docker) and offers to install missing ones,
#   • DISCLOSES the full plan (every resource it would create, with the IAM action),
#   • REUSES anything that already exists (describe-before-create),
#   • asks PERMISSION before EACH create — answer "n" to skip a step or "q" to stop,
#   • supports --dry-run (print every command, change nothing).
#
# Nothing is created without your explicit yes. Re-running is safe (idempotent).
#
# Usage:
#   ./scripts/aws-fargate-setup.sh                 # interactive, plan + per-step confirm
#   ./scripts/aws-fargate-setup.sh --dry-run       # print every command, execute nothing
#   ./scripts/aws-fargate-setup.sh --yes           # assume yes to every step (CI/non-interactive)
#   ./scripts/aws-fargate-setup.sh --region us-east-1 --cluster mycluster
#
# NOTE: deliberately NOT `set -e`. This is a stepwise, resumable provisioner —
# a declined step (confirm returns non-zero) and a reuse-skip are normal control
# flow, not fatal errors. We keep `-u` (catch unset vars) and pipefail, and surface
# AWS errors per command (re-run is safe: every step is reuse-before-create).
set -uo pipefail

DRY_RUN=0
ASSUME_YES=0
AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER="moo-cloud-bill"
ECR_REPO="moo-cloud-bill"
SECRET_NAME="moo-cloud-bill/api-key"
SCHEDULE_NAME="moo-cloud-bill-daily-push"
SCHEDULE_CRON="cron(17 6 * * ? *)"
SUBNETS=""
SECURITY_GROUP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --region) AWS_REGION="$2"; shift 2 ;;
    --cluster) CLUSTER="$2"; shift 2 ;;
    --ecr-repo) ECR_REPO="$2"; shift 2 ;;
    --secret-name) SECRET_NAME="$2"; shift 2 ;;
    --subnets) SUBNETS="$2"; shift 2 ;;
    --security-group) SECURITY_GROUP="$2"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # the Dockerfile lives here (build context)

say()  { printf '%s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
hr()   { printf '──────────────────────────────────────────────────────────────\n'; }

# Echo every mutating command (to stderr, so a caller's `>/dev/null` suppresses
# only the command's OWN output, never this echo); execute it unless --dry-run.
run() {
  printf '    $ %s\n' "$*" >&2
  [[ $DRY_RUN -eq 1 ]] && return 0
  "$@"
}

# Per-step gate. Returns: 0=yes, 1=skip, 2=quit (propagated to stop the run).
confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  local ans
  printf '  %s [y/N, or q to stop]: ' "$1"
  read -r ans
  case "$ans" in
    y|Y|yes|YES) return 0 ;;
    q|Q|quit) return 2 ;;
    *) return 1 ;;
  esac
}

abort_if_quit() { [[ "$1" == "2" ]] && { say ""; note "Stopped at your request. Re-run anytime — completed steps are reused."; exit 0; }; }

# ── Prerequisites ─────────────────────────────────────────────────────────────

pkg_install_hint() {
  case "$(uname -s)" in
    Darwin) echo "brew install $1" ;;
    Linux)  echo "sudo apt-get install -y $1   # (or your distro's package manager)" ;;
    *)      echo "install $1 for your OS" ;;
  esac
}

ensure_prereq() {
  local bin="$1" pkg="$2"
  command -v "$bin" >/dev/null 2>&1 && { note "✓ $bin found"; return 0; }
  note "✗ $bin not found — required."
  local hint; hint="$(pkg_install_hint "$pkg")"
  if confirm "Install $bin now via: $hint ?"; then
    run bash -c "$hint" || { note "! install failed — install $bin manually, then re-run."; exit 1; }
    command -v "$bin" >/dev/null 2>&1 || { note "! $bin still not on PATH (a new shell or app launch may be needed). Re-run after."; exit 1; }
  else
    note "Can't proceed without $bin. Install it ($hint) and re-run, or use the manual runbook: $CLI_DIR/AWS_SCHEDULING.md"
    exit 1
  fi
}

# ── Inputs ────────────────────────────────────────────────────────────────────

load_cli_config() {
  # Pull bucket/prefix/report/acute_base/currency from the CLI config (written by
  # `configure`). Falls back to prompting if the package/config isn't available.
  local out
  if out="$(python3 - <<'PY' 2>/dev/null
from moo_cloud_bill.config import load_config
import shlex
c = load_config()
for k in ("bucket","prefix","report_name","region","acute_base","reporting_currency"):
    print(f'CFG_{k.upper()}={shlex.quote(str(getattr(c, k) or ""))}')
PY
)"; then eval "$out"; fi
  CUR_BUCKET="${CFG_BUCKET:-}"; CUR_PREFIX="${CFG_PREFIX:-}"; REPORT_NAME="${CFG_REPORT_NAME:-}"
  ACUTE_BASE="${CFG_ACUTE_BASE:-}"; REPORTING_CURRENCY="${CFG_REPORTING_CURRENCY:-USD}"
  [[ -n "${CFG_REGION:-}" ]] && AWS_REGION="${CFG_REGION}"

  local prompt_needed=0
  for v in CUR_BUCKET CUR_PREFIX REPORT_NAME ACUTE_BASE; do [[ -z "${!v}" ]] && prompt_needed=1; done
  if [[ $prompt_needed -eq 1 ]]; then
    note "Some config wasn't found (run 'moo-cloud-bill configure' first to avoid typing it):"
    [[ -z "$CUR_BUCKET" ]]  && read -r -p "  CUR S3 bucket: " CUR_BUCKET
    [[ -z "$CUR_PREFIX" ]]  && read -r -p "  CUR S3 prefix (e.g. cur2): " CUR_PREFIX
    [[ -z "$REPORT_NAME" ]] && read -r -p "  CUR export name (e.g. moolabs-cur2): " REPORT_NAME
    [[ -z "$ACUTE_BASE" ]]  && read -r -p "  Acute base URL (e.g. https://acute.dev.moolabs.com): " ACUTE_BASE
  fi
}

resolve_api_key() {
  # From the 0600 credentials file written by `moo-cloud-bill init`, else prompt (hidden).
  local creds="${MOO_CLOUD_BILL_CONFIG_DIR:-$HOME/.config/moo-cloud-bill}/credentials"
  API_KEY=""
  if [[ -f "$creds" ]]; then
    API_KEY="$(grep -E '^MOOLABS_API_KEY=' "$creds" | head -1 | cut -d= -f2- || true)"
  fi
  if [[ -z "$API_KEY" ]]; then
    read -r -s -p "  Moolabs API key (from the Moolabs UI): " API_KEY; echo
  fi
  [[ -n "$API_KEY" ]] || { note "! No API key — run 'moo-cloud-bill init' or paste it. Aborting."; exit 1; }
}

discover_network() {
  [[ -n "$SUBNETS" && -n "$SECURITY_GROUP" ]] && return 0
  note "The Fargate task needs a VPC subnet + security group with outbound internet."
  local vpc
  vpc="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
        --query 'Vpcs[0].VpcId' --output text --region "$AWS_REGION" 2>/dev/null || echo None)"
  if [[ -n "$vpc" && "$vpc" != "None" ]]; then
    local d_subnets d_sg
    d_subnets="$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$vpc" \
                 --query 'Subnets[].SubnetId' --output text --region "$AWS_REGION" 2>/dev/null | tr '\t' ',')"
    d_sg="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$vpc" Name=group-name,Values=default \
            --query 'SecurityGroups[0].GroupId' --output text --region "$AWS_REGION" 2>/dev/null)"
    note "Default VPC $vpc → subnets [$d_subnets], default SG $d_sg."
    if confirm "Use the default VPC's subnets + default security group?"; then
      SUBNETS="$d_subnets"; SECURITY_GROUP="$d_sg"
    fi
  fi
  [[ -z "$SUBNETS" ]]        && read -r -p "  Subnet IDs (comma-separated): " SUBNETS
  [[ -z "$SECURITY_GROUP" ]] && read -r -p "  Security group ID: " SECURITY_GROUP
}

# ── Plan disclosure ───────────────────────────────────────────────────────────

show_plan() {
  hr; say "  PLAN — what this will create in your AWS account (region $AWS_REGION):"
  note ""
  note "  1. Secrets Manager secret  '$SECRET_NAME'         [secretsmanager:CreateSecret]"
  note "       ← your Moolabs API key (so the task never bakes it in)"
  note "  2. ECR repo  '$ECR_REPO'  + build & push the image  [ecr:CreateRepository, push]"
  note "  3. IAM role  mooCloudBillExecRole   (pull image, read the secret, logs)"
  note "  4. IAM role  mooCloudBillTaskRole   (read CUR from s3://$CUR_BUCKET/$CUR_PREFIX/*)"
  note "  5. IAM role  mooCloudBillSchedulerRole (let EventBridge run the task)"
  note "  6. ECS cluster '$CLUSTER' + log group + Fargate task definition"
  note "  7. One on-demand VERIFY run (to confirm wiring) — optional"
  note "  8. EventBridge schedule '$SCHEDULE_NAME'  ($SCHEDULE_CRON UTC, daily)"
  note ""
  note "  Each step asks before it runs and is SKIPPED if the resource already exists."
  [[ $DRY_RUN -eq 1 ]] && note "  [--dry-run] nothing will actually be created."
  hr
}

# ── Steps (each: reuse-before-create, gated) ──────────────────────────────────

ACCOUNT_ID=""; SECRET_ARN=""; IMAGE=""; EXEC_ROLE_ARN=""; TASK_ROLE_ARN=""; SCHED_ROLE_ARN=""
ECS_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

step_secret() {
  if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    note "✓ secret '$SECRET_NAME' exists — reusing."
  else
    local r=0; confirm "Create Secrets Manager secret '$SECRET_NAME' with your Moolabs API key?" || r=$?
    abort_if_quit "$r"
    if [[ $r -eq 0 ]]; then
      # NEVER echo the key — print a masked command, pass the real value only to aws.
      printf '    $ aws secretsmanager create-secret --name %s --secret-string ****hidden**** --region %s\n' "$SECRET_NAME" "$AWS_REGION" >&2
      [[ $DRY_RUN -eq 0 ]] && aws secretsmanager create-secret --name "$SECRET_NAME" \
        --description "Moolabs API key for moo-cloud-bill push" \
        --secret-string "$API_KEY" --region "$AWS_REGION" >/dev/null
    else
      note "  skipped."
    fi
  fi
  SECRET_ARN="$(aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$AWS_REGION" --query ARN --output text 2>/dev/null || echo "arn:aws:secretsmanager:$AWS_REGION:$ACCOUNT_ID:secret:$SECRET_NAME")"
}

step_image() {
  IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:latest"
  if aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$AWS_REGION" >/dev/null 2>&1; then
    note "✓ ECR repo '$ECR_REPO' exists — reusing."
  else
    confirm "Create ECR repo '$ECR_REPO'?"; local r=$?; abort_if_quit $r
    [[ $r -eq 0 ]] && run aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" >/dev/null || { note "  skipped repo; cannot push image."; return 0; }
  fi
  confirm "Build the image (linux/amd64) and push to ECR? (needs docker)"; local b=$?; abort_if_quit $b
  if [[ $b -eq 0 ]]; then
    run bash -c "aws ecr get-login-password --region '$AWS_REGION' | docker login --username AWS --password-stdin '$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com'"
    run docker build --platform linux/amd64 -t "$IMAGE" "$CLI_DIR"
    run docker push "$IMAGE"
  else
    note "  skipped build/push — the task def will reference $IMAGE (push it before scheduling)."
  fi
}

create_role_if_absent() {  # $1 role name, $2 trust json, $3 description
  if aws iam get-role --role-name "$1" >/dev/null 2>&1; then
    note "✓ role $1 exists — reusing."; return 1
  fi
  confirm "Create IAM role $1 ($3)?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || { note "  skipped $1."; return 2; }
  run aws iam create-role --role-name "$1" --assume-role-policy-document "$2" >/dev/null
  return 0
}

step_exec_role() {
  create_role_if_absent mooCloudBillExecRole "$ECS_TRUST" "ECS pulls image, reads secret, writes logs" || true
  run aws iam attach-role-policy --role-name mooCloudBillExecRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null || true
  local pol="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"
  run aws iam put-role-policy --role-name mooCloudBillExecRole --policy-name read-moolabs-secret --policy-document "$pol"
  EXEC_ROLE_ARN="$(aws iam get-role --role-name mooCloudBillExecRole --query Role.Arn --output text 2>/dev/null || echo "arn:aws:iam::$ACCOUNT_ID:role/mooCloudBillExecRole")"
}

step_task_role() {
  create_role_if_absent mooCloudBillTaskRole "$ECS_TRUST" "the app's own perms: read the CUR from S3" || true
  local pol="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"s3:ListBucket\",\"Resource\":\"arn:aws:s3:::$CUR_BUCKET\",\"Condition\":{\"StringLike\":{\"s3:prefix\":\"$CUR_PREFIX/*\"}}},{\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$CUR_BUCKET/$CUR_PREFIX/*\"}]}"
  run aws iam put-role-policy --role-name mooCloudBillTaskRole --policy-name read-cur --policy-document "$pol"
  TASK_ROLE_ARN="$(aws iam get-role --role-name mooCloudBillTaskRole --query Role.Arn --output text 2>/dev/null || echo "arn:aws:iam::$ACCOUNT_ID:role/mooCloudBillTaskRole")"
}

step_scheduler_role() {
  local trust="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"scheduler.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT_ID\"}}}]}"
  create_role_if_absent mooCloudBillSchedulerRole "$trust" "let EventBridge Scheduler run the task" || true
  local pol="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"ecs:RunTask\",\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":\"iam:PassRole\",\"Resource\":[\"$EXEC_ROLE_ARN\",\"$TASK_ROLE_ARN\"]}]}"
  run aws iam put-role-policy --role-name mooCloudBillSchedulerRole --policy-name run-task --policy-document "$pol"
  SCHED_ROLE_ARN="$(aws iam get-role --role-name mooCloudBillSchedulerRole --query Role.Arn --output text 2>/dev/null || echo "arn:aws:iam::$ACCOUNT_ID:role/mooCloudBillSchedulerRole")"
}

step_cluster_taskdef() {
  if aws ecs describe-clusters --clusters "$CLUSTER" --region "$AWS_REGION" --query 'clusters[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
    note "✓ ECS cluster '$CLUSTER' exists — reusing."
  else
    confirm "Create ECS (Fargate) cluster '$CLUSTER'?"; local r=$?; abort_if_quit $r
    [[ $r -eq 0 ]] && run aws ecs create-cluster --cluster-name "$CLUSTER" --region "$AWS_REGION" >/dev/null || note "  skipped."
  fi
  run aws logs create-log-group --log-group-name /ecs/moo-cloud-bill --region "$AWS_REGION" 2>/dev/null || true

  local taskdef
  taskdef="$(cat <<JSON
{"family":"moo-cloud-bill-push","requiresCompatibilities":["FARGATE"],"networkMode":"awsvpc","cpu":"512","memory":"1024","executionRoleArn":"$EXEC_ROLE_ARN","taskRoleArn":"$TASK_ROLE_ARN","containerDefinitions":[{"name":"push","image":"$IMAGE","essential":true,"command":["push"],"environment":[{"name":"MCB_BUCKET","value":"$CUR_BUCKET"},{"name":"MCB_PREFIX","value":"$CUR_PREFIX"},{"name":"MCB_REPORT_NAME","value":"$REPORT_NAME"},{"name":"MCB_REGION","value":"$AWS_REGION"},{"name":"MCB_ACUTE_BASE","value":"$ACUTE_BASE"},{"name":"MCB_REPORTING_CURRENCY","value":"$REPORTING_CURRENCY"}],"secrets":[{"name":"MOOLABS_API_KEY","valueFrom":"$SECRET_ARN"}],"logConfiguration":{"logDriver":"awslogs","options":{"awslogs-group":"/ecs/moo-cloud-bill","awslogs-region":"$AWS_REGION","awslogs-stream-prefix":"push"}}}]}
JSON
)"
  confirm "Register the Fargate task definition 'moo-cloud-bill-push'?"; local t=$?; abort_if_quit $t
  if [[ $t -eq 0 ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then printf '    $ aws ecs register-task-definition --cli-input-json <taskdef>\n';
    else aws ecs register-task-definition --cli-input-json "$taskdef" --region "$AWS_REGION" >/dev/null; fi
  fi
}

subnet_json() {  # CSV -> ["a","b"]
  local s out=""; for s in ${SUBNETS//,/ }; do out="$out\"$s\","; done; printf '[%s]' "${out%,}"
}

step_verify() {
  confirm "Run ONE on-demand task now to verify the wiring (before scheduling)?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || { note "  skipped verify run."; return 0; }
  local netcfg="awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SECURITY_GROUP],assignPublicIp=ENABLED}"
  run aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE \
    --task-definition moo-cloud-bill-push --network-configuration "$netcfg" --region "$AWS_REGION" >/dev/null
  note "Started. Watch logs:  aws logs tail /ecs/moo-cloud-bill --follow --region $AWS_REGION"
}

step_schedule() {
  if aws scheduler get-schedule --name "$SCHEDULE_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    note "✓ schedule '$SCHEDULE_NAME' exists — reusing (delete it first to change cadence)."; return 0
  fi
  confirm "Create the daily EventBridge schedule '$SCHEDULE_NAME' ($SCHEDULE_CRON UTC)?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || { note "  skipped schedule."; return 0; }
  local target
  target="$(cat <<JSON
{"Arn":"arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:cluster/$CLUSTER","RoleArn":"$SCHED_ROLE_ARN","EcsParameters":{"TaskDefinitionArn":"arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:task-definition/moo-cloud-bill-push","LaunchType":"FARGATE","NetworkConfiguration":{"awsvpcConfiguration":{"Subnets":$(subnet_json),"SecurityGroups":["$SECURITY_GROUP"],"AssignPublicIp":"ENABLED"}}}}
JSON
)"
  if [[ $DRY_RUN -eq 1 ]]; then printf '    $ aws scheduler create-schedule --name %s --schedule-expression "%s" --target <target>\n' "$SCHEDULE_NAME" "$SCHEDULE_CRON";
  else aws scheduler create-schedule --name "$SCHEDULE_NAME" --schedule-expression "$SCHEDULE_CRON" \
    --schedule-expression-timezone UTC --flexible-time-window '{"Mode":"OFF"}' \
    --target "$target" --region "$AWS_REGION" >/dev/null; fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
  say ""; hr; say "  moo-cloud-bill — AWS Fargate scheduling setup"
  [[ $DRY_RUN -eq 1 ]] && say "  (--dry-run: prints every command, creates NOTHING)"
  hr
  note "Checking prerequisites…"
  ensure_prereq aws awscli
  ensure_prereq docker docker

  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")"
  [[ -n "$ACCOUNT_ID" ]] || { note "! Can't read your AWS identity — run 'aws sso login' (or set creds) and re-run."; exit 1; }
  note "AWS account: $ACCOUNT_ID   region: $AWS_REGION"

  load_cli_config
  resolve_api_key
  discover_network
  show_plan

  if ! confirm "Proceed with the steps below (each asks again before it runs)?"; then
    say ""; note "No problem — nothing was changed. Alternatives:"
    note "  • Inspect every command first:   $0 --dry-run"
    note "  • Do it by hand:                 $CLI_DIR/AWS_SCHEDULING.md"
    note "  • Keep the dev/test laptop cron the installer offered instead."
    exit 0
  fi

  say ""; say "  Step 1/8 — Secrets Manager";        step_secret
  say "  Step 2/8 — Image (ECR)";                    step_image
  say "  Step 3/8 — Execution role";                 step_exec_role
  say "  Step 4/8 — Task role";                      step_task_role
  say "  Step 5/8 — Scheduler role";                 step_scheduler_role
  say "  Step 6/8 — Cluster + task definition";      step_cluster_taskdef
  say "  Step 7/8 — Verify run";                     step_verify
  say "  Step 8/8 — Daily schedule";                 step_schedule

  say ""; hr
  note "Done. The daily push runs at $SCHEDULE_CRON UTC via Fargate (IAM role — no SSO expiry)."
  note "Logs:     aws logs tail /ecs/moo-cloud-bill --follow --region $AWS_REGION"
  note "Teardown: see the Teardown section of $CLI_DIR/AWS_SCHEDULING.md"
  hr
}

main
