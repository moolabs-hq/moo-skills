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
REGION_EXPLICIT=0   # set to 1 by --region below; tells load_cli_config not to override it
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
    --region) AWS_REGION="$2"; REGION_EXPLICIT=1; shift 2 ;;
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
  # BUCKET_REGION is where the CUR bucket/export actually lives — AWS's CUR 2.0
  # Data Exports API only exists in us-east-1, so `configure` always creates the
  # bucket there and CFG_REGION is always "us-east-1" in practice. AWS_REGION is
  # a SEPARATE concept: where the compute (VPC, ECS cluster, IAM roles, ECR,
  # EventBridge schedule) runs — a customer can run their Fargate task out of
  # eu-west-1 while still reading a CUR bucket that has to sit in us-east-1.
  # Only fall back to CFG_REGION for AWS_REGION when the operator didn't pass
  # --region themselves — otherwise this silently overwrote and broke the flag.
  BUCKET_REGION="${CFG_REGION:-us-east-1}"
  [[ $REGION_EXPLICIT -eq 0 && -n "${CFG_REGION:-}" ]] && AWS_REGION="${CFG_REGION}"

  local prompt_needed=0
  for v in CUR_BUCKET CUR_PREFIX REPORT_NAME ACUTE_BASE; do [[ -z "${!v}" ]] && prompt_needed=1; done
  if [[ $prompt_needed -eq 1 ]]; then
    note "Some config wasn't found (run 'moo-cloud-bill configure' first to avoid typing it):"
    [[ -z "$CUR_BUCKET" ]]  && read -r -p "  CUR S3 bucket: " CUR_BUCKET
    [[ -z "$CUR_PREFIX" ]]  && read -r -p "  CUR S3 prefix (e.g. cur2): " CUR_PREFIX
    [[ -z "$REPORT_NAME" ]] && read -r -p "  CUR export name (e.g. moolabs-cur2): " REPORT_NAME
    [[ -z "$ACUTE_BASE" ]]  && read -r -p "  Acute base URL (e.g. https://acute.prod.moolabs.com): " ACUTE_BASE
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

# Checks whether a subnet is actually internet-routable and warns if not,
# rather than silently creating one that won't work. Resolves the subnet's
# EFFECTIVE route table — its explicit association if any, else the VPC's
# main table, which is what a freshly created subnet always starts on — and
# looks for a 0.0.0.0/0 route to an Internet Gateway. Deliberately read-only:
# creating an IGW + route table changes VPC-level routing (blast radius
# beyond this one subnet), which is out of scope for a billing-ingest
# installer that otherwise only ever touches its own moo-cloud-bill-* resources.
warn_if_subnet_not_routable() {  # $1 = subnet id, $2 = vpc id
  local subnet="$1" vpc="$2" rtb has_igw_route
  rtb="$(aws ec2 describe-route-tables --filters Name=association.subnet-id,Values="$subnet" \
         --query 'RouteTables[0].RouteTableId' --output text --region "$AWS_REGION" 2>/dev/null)"
  if [[ -z "$rtb" || "$rtb" == "None" ]]; then
    rtb="$(aws ec2 describe-route-tables --filters Name=vpc-id,Values="$vpc" Name=association.main,Values=true \
           --query 'RouteTables[0].RouteTableId' --output text --region "$AWS_REGION" 2>/dev/null)"
  fi
  [[ -z "$rtb" || "$rtb" == "None" ]] && return 0   # couldn't resolve one — nothing more to say
  has_igw_route="$(aws ec2 describe-route-tables --route-table-ids "$rtb" \
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0' && starts_with(GatewayId, 'igw-')].GatewayId" \
    --output text --region "$AWS_REGION" 2>/dev/null)"
  if [[ -z "$has_igw_route" ]]; then
    note "! $subnet has no route to an internet gateway (route table $rtb has no 0.0.0.0/0 → igw-* route)."
    note "  The Fargate task needs outbound internet to pull its image and reach S3/Acute — it will fail without one."
    note "  To fix: attach an internet gateway to $vpc and route 0.0.0.0/0 to it from $rtb, e.g.:"
    note "    aws ec2 create-internet-gateway --region $AWS_REGION"
    note "    aws ec2 attach-internet-gateway --vpc-id $vpc --internet-gateway-id <igw-id> --region $AWS_REGION"
    note "    aws ec2 create-route --route-table-id $rtb --destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id> --region $AWS_REGION"
  fi
}

# Creates a subnet in this VPC — used when none exist to choose from, or the
# operator picks "create one" out of the list. Only creates the subnet itself
# (see warn_if_subnet_not_routable above for why this doesn't also wire up
# internet routing) — CIDR and AZ are the operator's call since a wrong guess
# here is an AWS-side error (InvalidSubnet.Range/.Conflict), not something
# worth trying to compute ourselves.
create_subnet() {  # $1 = vpc id; sets SUBNETS
  local vpc="$1" vpc_cidr
  vpc_cidr="$(aws ec2 describe-vpcs --vpc-ids "$vpc" --query 'Vpcs[0].CidrBlock' --output text --region "$AWS_REGION" 2>/dev/null)"

  local -a azs
  local i=0 az
  # A flat `[].field` query with --output text prints one tab-separated line,
  # same shape as the SUBNETS CSV elsewhere in this file — word-split it.
  for az in $(aws ec2 describe-availability-zones --region "$AWS_REGION" --filters Name=state,Values=available \
              --query 'AvailabilityZones[].ZoneName' --output text 2>/dev/null); do
    i=$((i + 1)); azs[$i]="$az"
  done
  if [[ $i -eq 0 ]]; then
    note "! Could not list availability zones in $AWS_REGION — aborting."
    exit 1
  fi
  note "Availability zones in $AWS_REGION:"
  local n; for n in "${!azs[@]}"; do note "  $n) ${azs[$n]}"; done

  confirm "Create a subnet in $vpc (CIDR block ${vpc_cidr:-unknown}) for the Fargate task?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || return 0

  local az_choice cidr
  read -r -p "  Availability zone (number, default 1): " az_choice
  [[ -z "$az_choice" || ! "$az_choice" =~ ^[0-9]+$ || -z "${azs[$az_choice]:-}" ]] && az_choice=1
  read -r -p "  Subnet CIDR (must fit inside ${vpc_cidr:-the VPC range}, e.g. 10.0.100.0/24): " cidr
  if [[ -z "$cidr" ]]; then
    note "! No CIDR entered — aborting."
    exit 1
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    run aws ec2 create-subnet --vpc-id "$vpc" --cidr-block "$cidr" --availability-zone "${azs[$az_choice]}" --region "$AWS_REGION" >/dev/null
    SUBNETS="subnet-DRYRUN"
    return 0
  fi
  local subnet_id
  subnet_id="$(run aws ec2 create-subnet --vpc-id "$vpc" --cidr-block "$cidr" --availability-zone "${azs[$az_choice]}" \
               --region "$AWS_REGION" --query Subnet.SubnetId --output text)"
  note "Created subnet $subnet_id (${azs[$az_choice]}, $cidr)."
  warn_if_subnet_not_routable "$subnet_id" "$vpc"
  SUBNETS="$subnet_id"
}

# Lists the VPC's subnets (id, AZ, CIDR) and lets the operator pick one or more
# by number, rather than silently lumping every subnet in the VPC together.
# Offers to create one (see create_subnet above) if none exist, or if the
# operator asks for it. Under --yes (non-interactive), there's no one to ask,
# so it keeps the old behavior of selecting all of them.
choose_subnets() {  # $1 = vpc id; sets SUBNETS
  local vpc="$1" rows
  rows="$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$vpc" \
          --query 'Subnets[].[SubnetId,AvailabilityZone,CidrBlock]' --output text --region "$AWS_REGION" 2>/dev/null)"
  local -a ids
  local i=0 sid az cidr
  if [[ -n "$rows" ]]; then
    note "Subnets in $vpc:"
    while IFS=$'\t' read -r sid az cidr; do
      [[ -z "$sid" ]] && continue
      i=$((i + 1)); ids[$i]="$sid"
      note "  $i) $sid  ($az, $cidr)"
    done <<<"$rows"
  fi

  if [[ $i -eq 0 ]]; then
    note "No subnets found in $vpc."
    create_subnet "$vpc"
    return 0
  fi

  if [[ $ASSUME_YES -eq 1 ]]; then
    local n out=""; for n in "${!ids[@]}"; do out="$out${ids[$n]},"; done
    SUBNETS="${out%,}"
    local s; for s in ${SUBNETS//,/ }; do warn_if_subnet_not_routable "$s" "$vpc"; done
    return 0
  fi

  note "  c) create a new one"
  read -r -p "  Choose subnet(s) — comma-separated numbers, 'a' for all, or 'c' to create one: " sel
  if [[ "$sel" == "c" || "$sel" == "C" ]]; then
    create_subnet "$vpc"
    return 0
  fi
  local chosen="" part
  if [[ -z "$sel" || "$sel" == "a" || "$sel" == "A" ]]; then
    local n; for n in "${!ids[@]}"; do chosen="$chosen${ids[$n]},"; done
  else
    for part in ${sel//,/ }; do
      [[ "$part" =~ ^[0-9]+$ && -n "${ids[$part]:-}" ]] && chosen="$chosen${ids[$part]},"
    done
  fi
  SUBNETS="${chosen%,}"
  # Existing subnets can be just as unroutable as a freshly created one —
  # especially in a non-default VPC, where nothing wires up an IGW route
  # automatically. create_subnet already warns on its own path; this covers
  # the "picked from the list" path too.
  local s; for s in ${SUBNETS//,/ }; do warn_if_subnet_not_routable "$s" "$vpc"; done
}

# Creates a security group scoped to this VPC — used when none exist to choose
# from, or the operator picks "create one" out of the list. Reuses one from a
# prior run instead of failing on AWS's per-VPC name-uniqueness constraint.
# AWS gives every new security group an allow-all outbound rule and no inbound
# rules by default — exactly what a Fargate task pulling from ECR/S3/Acute over
# the internet needs; nothing to add.
create_security_group() {  # $1 = vpc id; sets SECURITY_GROUP
  local vpc="$1" name="moo-cloud-bill-sg" existing
  existing="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$vpc" Name=group-name,Values="$name" \
              --query 'SecurityGroups[0].GroupId' --output text --region "$AWS_REGION" 2>/dev/null)"
  if [[ -n "$existing" && "$existing" != "None" ]]; then
    note "✓ security group '$name' already exists ($existing) — reusing."
    SECURITY_GROUP="$existing"
    return 0
  fi
  confirm "Create security group '$name' in $vpc (outbound only — AWS's default for a new SG)?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || return 0
  if [[ $DRY_RUN -eq 1 ]]; then
    run aws ec2 create-security-group --group-name "$name" \
      --description "moo-cloud-bill Fargate task (outbound only)" --vpc-id "$vpc" --region "$AWS_REGION" >/dev/null
    SECURITY_GROUP="sg-DRYRUN"
    return 0
  fi
  run aws ec2 create-security-group --group-name "$name" \
    --description "moo-cloud-bill Fargate task (outbound only)" --vpc-id "$vpc" --region "$AWS_REGION" >/dev/null
  SECURITY_GROUP="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$vpc" Name=group-name,Values="$name" \
                     --query 'SecurityGroups[0].GroupId' --output text --region "$AWS_REGION" 2>/dev/null)"
  note "Created security group '$name' ($SECURITY_GROUP)."
}

# Lists the VPC's security groups (id, name, description) and lets the operator
# pick one by number — mirrors choose_subnets(). Offers to create one (see
# create_security_group above) if none exist, or if the operator asks for it.
# Under --yes (non-interactive), prefers a group actually named "default" if
# one is in the list, else the first one, else creates one.
choose_security_group() {  # $1 = vpc id; sets SECURITY_GROUP
  local vpc="$1" rows
  rows="$(aws ec2 describe-security-groups --filters Name=vpc-id,Values="$vpc" \
          --query 'SecurityGroups[].[GroupId,GroupName,Description]' --output text --region "$AWS_REGION" 2>/dev/null)"
  local -a ids names
  local i=0 gid gname gdesc
  if [[ -n "$rows" ]]; then
    note "Security groups in $vpc:"
    while IFS=$'\t' read -r gid gname gdesc; do
      [[ -z "$gid" ]] && continue
      i=$((i + 1)); ids[$i]="$gid"; names[$i]="$gname"
      note "  $i) $gid  ($gname — $gdesc)"
    done <<<"$rows"
  fi

  if [[ $i -eq 0 ]]; then
    note "No security groups found in $vpc."
    create_security_group "$vpc"
    return 0
  fi

  if [[ $ASSUME_YES -eq 1 ]]; then
    local n picked=""
    for n in "${!ids[@]}"; do [[ "${names[$n]}" == "default" ]] && picked="${ids[$n]}"; done
    if [[ -n "$picked" ]]; then SECURITY_GROUP="$picked"; else SECURITY_GROUP="${ids[1]}"; fi
    return 0
  fi

  note "  c) create a new one"
  read -r -p "  Choose a security group (number), or 'c' to create one: " sel
  if [[ "$sel" == "c" || "$sel" == "C" ]]; then
    create_security_group "$vpc"
  elif [[ "$sel" =~ ^[0-9]+$ && -n "${ids[$sel]:-}" ]]; then
    SECURITY_GROUP="${ids[$sel]}"
  fi
}

# Creates this region's default VPC — a subnet per AZ, an internet gateway,
# and the 0.0.0.0/0 route, all wired atomically by AWS in one call. This is
# the escape hatch for "no VPC at all", which is the NORMAL state for any
# AWS account created after Dec 2021 (AWS stopped auto-creating default VPCs
# then) — not an edge case. Deliberately does NOT support creating a
# non-default VPC: that drags CIDR planning + IGW + route-table wiring back
# into this script by hand, the exact blast-radius tradeoff
# warn_if_subnet_not_routable above already decided against. Errors with
# DefaultVpcAlreadyExists if one exists, which can't happen here since
# choose_vpc only calls this when its VPC list came back empty.
create_default_vpc() {  # sets VPC_ID
  confirm "No VPC found in $AWS_REGION. Create this region's default VPC (subnet per AZ + internet gateway, wired by AWS in one step)?"; local r=$?; abort_if_quit $r
  if [[ $r -ne 0 ]]; then
    note "! No VPC to use — aborting."
    exit 1
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    run aws ec2 create-default-vpc --region "$AWS_REGION" >/dev/null
    VPC_ID="vpc-DRYRUN"
    return 0
  fi
  VPC_ID="$(run aws ec2 create-default-vpc --region "$AWS_REGION" --query Vpc.VpcId --output text)"
  if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
    note "! Failed to create a default VPC — aborting."
    exit 1
  fi
  note "Created default VPC $VPC_ID (subnet per AZ + internet gateway, already routable)."
}

# Lists EVERY VPC in the region (default or not) and lets the operator pick
# one by number — same shape as choose_subnets/choose_security_group above.
# Older code here only ever looked at the DEFAULT VPC, which meant an account
# with no default VPC (the normal case post-Dec-2021, or any non-default VPC
# a customer already uses) fell straight to raw "paste an ID" prompts with no
# way to see what's available or create anything. Offers to create the
# region's default VPC (see create_default_vpc above) when none exist at all.
choose_vpc() {  # sets VPC_ID
  local rows
  rows="$(aws ec2 describe-vpcs \
          --query 'Vpcs[].[VpcId,CidrBlock,IsDefault]' --output text --region "$AWS_REGION" 2>/dev/null)"
  local -a ids defaults
  local i=0 vid cidr isdef tag
  if [[ -n "$rows" ]]; then
    note "VPCs in $AWS_REGION:"
    while IFS=$'\t' read -r vid cidr isdef; do
      [[ -z "$vid" ]] && continue
      i=$((i + 1)); ids[$i]="$vid"; defaults[$i]="$isdef"
      tag=""; [[ "$isdef" == "True" ]] && tag=", default"
      note "  $i) $vid  ($cidr$tag)"
    done <<<"$rows"
  fi

  if [[ $i -eq 0 ]]; then
    note "No VPC found in $AWS_REGION."
    create_default_vpc
    return 0
  fi

  if [[ $ASSUME_YES -eq 1 ]]; then
    local n picked=""
    for n in "${!ids[@]}"; do [[ "${defaults[$n]}" == "True" ]] && picked="${ids[$n]}"; done
    VPC_ID="${picked:-${ids[1]}}"
    return 0
  fi

  read -r -p "  Choose a VPC (number): " sel
  [[ "$sel" =~ ^[0-9]+$ && -n "${ids[$sel]:-}" ]] && VPC_ID="${ids[$sel]}"
}

discover_network() {
  [[ -n "$SUBNETS" && -n "$SECURITY_GROUP" ]] && return 0
  note "The Fargate task needs a VPC subnet + security group with outbound internet."
  local vpc=""
  if [[ -z "$SUBNETS" || -z "$SECURITY_GROUP" ]]; then
    choose_vpc
    vpc="$VPC_ID"
  fi
  if [[ -n "$vpc" ]]; then
    [[ -z "$SUBNETS" ]] && choose_subnets "$vpc"
    [[ -z "$SECURITY_GROUP" ]] && choose_security_group "$vpc"
  fi
  [[ -z "$SUBNETS" ]]        && read -r -p "  Subnet IDs (comma-separated): " SUBNETS
  [[ -z "$SECURITY_GROUP" ]] && read -r -p "  Security group ID: " SECURITY_GROUP
  # A blank answer here (no VPC chosen, or an empty Enter at the prompt) would
  # otherwise surface much later as an opaque AWS-side error deep in Step 7/8
  # ("subnets can not be empty") instead of a clear failure up front.
  if [[ -z "$SUBNETS" || -z "$SECURITY_GROUP" ]]; then
    note "! Need at least one subnet ID and a security group ID to run the Fargate task — aborting."
    exit 1
  fi
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
  # Trusts BOTH EventBridge Scheduler (new, needs a recent AWS CLI/botocore)
  # and classic EventBridge Rules (events.amazonaws.com, supported by every
  # AWS CLI back to ~2016) so one role works with whichever backend
  # step_schedule() ends up using — see its AWS CLI capability check.
  local trust="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"scheduler.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT_ID\"}}},{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"events.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACCOUNT_ID\"}}}]}"
  create_role_if_absent mooCloudBillSchedulerRole "$trust" "let EventBridge (Scheduler or classic Rules) run the task" || true
  # Sync the trust doc even on reuse — an existing role from before dual-backend
  # support only trusted scheduler.amazonaws.com and would reject classic Rules.
  run aws iam update-assume-role-policy --role-name mooCloudBillSchedulerRole --policy-document "$trust"
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
{"family":"moo-cloud-bill-push","requiresCompatibilities":["FARGATE"],"networkMode":"awsvpc","cpu":"512","memory":"1024","executionRoleArn":"$EXEC_ROLE_ARN","taskRoleArn":"$TASK_ROLE_ARN","containerDefinitions":[{"name":"push","image":"$IMAGE","essential":true,"command":["push"],"environment":[{"name":"MCB_BUCKET","value":"$CUR_BUCKET"},{"name":"MCB_PREFIX","value":"$CUR_PREFIX"},{"name":"MCB_REPORT_NAME","value":"$REPORT_NAME"},{"name":"MCB_REGION","value":"$BUCKET_REGION"},{"name":"MCB_ACUTE_BASE","value":"$ACUTE_BASE"},{"name":"MCB_REPORTING_CURRENCY","value":"$REPORTING_CURRENCY"}],"secrets":[{"name":"MOOLABS_API_KEY","valueFrom":"$SECRET_ARN"}],"logConfiguration":{"logDriver":"awslogs","options":{"awslogs-group":"/ecs/moo-cloud-bill","awslogs-region":"$AWS_REGION","awslogs-stream-prefix":"push"}}}]}
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
  if [[ $DRY_RUN -eq 1 ]]; then
    run aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE \
      --task-definition moo-cloud-bill-push --network-configuration "$netcfg" --region "$AWS_REGION" >/dev/null
    return 0
  fi
  if run aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE \
    --task-definition moo-cloud-bill-push --network-configuration "$netcfg" --region "$AWS_REGION" >/dev/null; then
    note "Started. Watch logs:  aws logs tail /ecs/moo-cloud-bill --follow --region $AWS_REGION"
  else
    note "! run-task failed (see the AWS error above) — fix it before scheduling the daily run."
  fi
}

# The ECS target JSON is identical between the two backends (only the wrapping
# API call differs), so both step_schedule_via_* functions build it the same way.
schedule_ecs_target_json() {
  cat <<JSON
{"Arn":"arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:cluster/$CLUSTER","RoleArn":"$SCHED_ROLE_ARN","EcsParameters":{"TaskDefinitionArn":"arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:task-definition/moo-cloud-bill-push","LaunchType":"FARGATE","NetworkConfiguration":{"awsvpcConfiguration":{"Subnets":$(subnet_json),"SecurityGroups":["$SECURITY_GROUP"],"AssignPublicIp":"ENABLED"}}}}
JSON
}

# EventBridge Scheduler — the modern, purpose-built API. Needs a CLI/botocore
# new enough to know the `scheduler` service (added ~Nov 2022); gated on that
# by the step_schedule() dispatcher below, which falls back to
# step_schedule_via_events_rule() on an older CLI instead of erroring here.
step_schedule_via_scheduler() {
  if aws scheduler get-schedule --name "$SCHEDULE_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    note "✓ schedule '$SCHEDULE_NAME' exists — reusing (delete it first to change cadence)."; return 0
  fi
  confirm "Create the daily EventBridge schedule '$SCHEDULE_NAME' ($SCHEDULE_CRON UTC)?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || { note "  skipped schedule."; return 0; }
  local target; target="$(schedule_ecs_target_json)"
  if [[ $DRY_RUN -eq 1 ]]; then printf '    $ aws scheduler create-schedule --name %s --schedule-expression "%s" --target <target>\n' "$SCHEDULE_NAME" "$SCHEDULE_CRON";
  else aws scheduler create-schedule --name "$SCHEDULE_NAME" --schedule-expression "$SCHEDULE_CRON" \
    --schedule-expression-timezone UTC --flexible-time-window '{"Mode":"OFF"}' \
    --target "$target" --region "$AWS_REGION" >/dev/null; fi
}

# Classic EventBridge Rules (`aws events put-rule` + `put-targets`) — the same
# cron() syntax, the same ECS-task target shape, and has been in every AWS CLI
# (v1 and v2) since ~2016. Used when `aws scheduler` isn't available, so the
# daily push still gets scheduled instead of the operator being told to
# upgrade and left without a working schedule.
step_schedule_via_events_rule() {
  if aws events describe-rule --name "$SCHEDULE_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    note "✓ EventBridge rule '$SCHEDULE_NAME' exists — reusing (delete it first to change cadence)."; return 0
  fi
  confirm "Create the daily EventBridge rule '$SCHEDULE_NAME' ($SCHEDULE_CRON UTC)?"; local r=$?; abort_if_quit $r
  [[ $r -eq 0 ]] || { note "  skipped schedule."; return 0; }
  local target targets
  target="$(schedule_ecs_target_json)"
  targets="[$(printf '%s' "$target" | sed 's/^{/{"Id":"moo-cloud-bill-push",/')]"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '    $ aws events put-rule --name %s --schedule-expression "%s" --state ENABLED\n' "$SCHEDULE_NAME" "$SCHEDULE_CRON"
    printf '    $ aws events put-targets --rule %s --targets <targets>\n' "$SCHEDULE_NAME"
    return 0
  fi
  run aws events put-rule --name "$SCHEDULE_NAME" --schedule-expression "$SCHEDULE_CRON" \
    --state ENABLED --region "$AWS_REGION" >/dev/null
  run aws events put-targets --rule "$SCHEDULE_NAME" --targets "$targets" --region "$AWS_REGION" >/dev/null
}

step_schedule() {
  if aws scheduler help >/dev/null 2>&1; then
    step_schedule_via_scheduler
  else
    note "  (this AWS CLI predates EventBridge Scheduler — using classic EventBridge Rules instead; same daily cadence, same task.)"
    step_schedule_via_events_rule
  fi
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
