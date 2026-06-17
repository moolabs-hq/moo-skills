# Scheduling `moo-cloud-bill push` on AWS (ECS Fargate)

The `push` job should run **on AWS**, not on your laptop. Running it on a laptop
means the daily push only fires when that machine is on, awake, and authenticated —
and laptop SSO tokens expire. On AWS the task uses an **IAM role**, so credentials
never expire and the job runs whether or not anyone is logged in.

This runbook sets up a daily **ECS Fargate scheduled task** driven by **EventBridge
Scheduler**:

```
EventBridge Scheduler (cron, daily)
        └─ ecs:RunTask ─→ Fargate task (your image)
                              ├─ config:  MCB_* env vars (task definition)
                              ├─ secret:  MOOLABS_API_KEY  ← Secrets Manager
                              └─ AWS auth: task role (S3 read) — no SSO, no keys
```

> **You run every command below yourself.** Nothing here is automated for you — each
> step creates a resource in *your* account, so you stay in control of what is created
> and can stop at any point. Where an existing resource can be reused, that is called
> out. Prefer reuse; only create what you don't already have.

You'll do this once. After it's running, the only ongoing artifact is the daily task.

---

## Automated path (recommended)

The steps below are also wrapped in a guided script that runs the AWS CLI for you —
it checks prerequisites, prints a plan, **reuses anything that already exists**, and
**asks before every single create** (answer `q` to stop at any point):

```bash
# from the cloud-bill-cli directory:
bash scripts/aws-fargate-setup.sh --dry-run   # print every command, change nothing
bash scripts/aws-fargate-setup.sh             # run it, confirming each step
```

It reads your bucket/prefix/report/acute-base from `moo-cloud-bill configure`, your
API key from the `init` credentials file, and offers your default VPC's subnets +
security group. `install.sh` offers to run it for you too. The manual steps below are
the reference for what that script does (and the path if you prefer to run each command
yourself).

---

## 0. Prerequisites

- `aws` CLI v2, authenticated to the account whose CUR you ingest, with permission to
  create the resources below (ECR, IAM, ECS, Secrets Manager, EventBridge Scheduler).
- `docker` (to build the image).
- You've already run `moo-cloud-bill configure` once so you know your **bucket /
  prefix / report name / acute base**. (The Fargate task does not need the local
  config file — it reads these from env. It does need the export to exist.)
- An existing **VPC** with subnets that have outbound internet (NAT gateway or public
  subnet + `assignPublicIp=ENABLED`) so the task can reach S3 and Acute, and a
  **security group** allowing egress.

Fill these in and keep them handy:

```bash
export AWS_REGION=us-east-1                 # CUR / Data Exports live in us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export CUR_BUCKET=my-cur-bucket             # from `configure`
export CUR_PREFIX=cur2                       # from `configure`
export REPORT_NAME=moolabs-cur2             # from `configure`
export ACUTE_BASE=https://acute.dev.moolabs.com   # from `configure`
export REPORTING_CURRENCY=USD
export SUBNETS=subnet-aaaa,subnet-bbbb      # your VPC subnets (egress to internet)
export SECURITY_GROUP=sg-cccc               # egress allowed
export ECR_REPO=moo-cloud-bill
export CLUSTER=moo-cloud-bill               # an ECS cluster name (create below or reuse)
```

---

## 1. Store the Moolabs API key in Secrets Manager

The key is generated in the Moolabs UI. Store it once; the task reads it at runtime
(it is **never** baked into the image or the task definition in plaintext).

```bash
# Reuse: if you already have it, run `aws secretsmanager describe-secret --secret-id moo-cloud-bill/api-key` and skip.
aws secretsmanager create-secret \
  --name moo-cloud-bill/api-key \
  --description "Moolabs API key for moo-cloud-bill push" \
  --secret-string "mlk_xxxxxxxxxxxxxxxx" \
  --region "$AWS_REGION"

export SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id moo-cloud-bill/api-key --region "$AWS_REGION" \
  --query ARN --output text)
```

---

## 2. Build the image and push it to ECR

```bash
# Reuse: `aws ecr describe-repositories --repository-names "$ECR_REPO"` — skip create if it exists.
aws ecr create-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" >/dev/null

export IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO:latest"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# Build from the cloud-bill-cli directory (where the Dockerfile lives).
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

> Apple Silicon / ARM laptops: add `--platform linux/amd64` to `docker build` (Fargate
> runs amd64 unless you set the task `runtimePlatform` to ARM64).

---

## 3. Create the IAM roles

Three roles, each least-privilege. Reuse an existing role of the same shape if you have
one; otherwise create it.

### 3a. Task **execution** role — lets ECS pull the image, read the secret, write logs

```bash
cat > /tmp/ecs-trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON

aws iam create-role --role-name mooCloudBillExecRole \
  --assume-role-policy-document file:///tmp/ecs-trust.json >/dev/null

aws iam attach-role-policy --role-name mooCloudBillExecRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Allow reading ONLY this one secret.
cat > /tmp/exec-secret.json <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"secretsmanager:GetSecretValue","Resource":"$SECRET_ARN"}]}
JSON
aws iam put-role-policy --role-name mooCloudBillExecRole \
  --policy-name read-moolabs-secret --policy-document file:///tmp/exec-secret.json

export EXEC_ROLE_ARN=$(aws iam get-role --role-name mooCloudBillExecRole --query Role.Arn --output text)
```

### 3b. Task **role** — the app's own permissions: read the CUR from S3

```bash
aws iam create-role --role-name mooCloudBillTaskRole \
  --assume-role-policy-document file:///tmp/ecs-trust.json >/dev/null

cat > /tmp/task-s3.json <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$CUR_BUCKET","Condition":{"StringLike":{"s3:prefix":"$CUR_PREFIX/*"}}},
  {"Effect":"Allow","Action":"s3:GetObject","Resource":"arn:aws:s3:::$CUR_BUCKET/$CUR_PREFIX/*"}
]}
JSON
aws iam put-role-policy --role-name mooCloudBillTaskRole \
  --policy-name read-cur --policy-document file:///tmp/task-s3.json

export TASK_ROLE_ARN=$(aws iam get-role --role-name mooCloudBillTaskRole --query Role.Arn --output text)
```

### 3c. Scheduler **invocation** role — lets EventBridge Scheduler call `ecs:RunTask`

```bash
cat > /tmp/sched-trust.json <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"$ACCOUNT_ID"}}}]}
JSON

aws iam create-role --role-name mooCloudBillSchedulerRole \
  --assume-role-policy-document file:///tmp/sched-trust.json >/dev/null

cat > /tmp/sched-run.json <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":"ecs:RunTask","Resource":"*"},
  {"Effect":"Allow","Action":"iam:PassRole","Resource":["$EXEC_ROLE_ARN","$TASK_ROLE_ARN"]}
]}
JSON
aws iam put-role-policy --role-name mooCloudBillSchedulerRole \
  --policy-name run-task --policy-document file:///tmp/sched-run.json

export SCHED_ROLE_ARN=$(aws iam get-role --role-name mooCloudBillSchedulerRole --query Role.Arn --output text)
```

---

## 4. Cluster + log group + task definition

```bash
# Reuse an existing cluster if you have one; else create a (free) Fargate cluster.
aws ecs create-cluster --cluster-name "$CLUSTER" --region "$AWS_REGION" >/dev/null
aws logs create-log-group --log-group-name /ecs/moo-cloud-bill --region "$AWS_REGION" 2>/dev/null || true

cat > /tmp/taskdef.json <<JSON
{
  "family": "moo-cloud-bill-push",
  "requiresCompatibilities": ["FARGATE"],
  "networkMode": "awsvpc",
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$EXEC_ROLE_ARN",
  "taskRoleArn": "$TASK_ROLE_ARN",
  "containerDefinitions": [{
    "name": "push",
    "image": "$IMAGE",
    "essential": true,
    "command": ["push"],
    "environment": [
      {"name": "MCB_BUCKET", "value": "$CUR_BUCKET"},
      {"name": "MCB_PREFIX", "value": "$CUR_PREFIX"},
      {"name": "MCB_REPORT_NAME", "value": "$REPORT_NAME"},
      {"name": "MCB_REGION", "value": "$AWS_REGION"},
      {"name": "MCB_ACUTE_BASE", "value": "$ACUTE_BASE"},
      {"name": "MCB_REPORTING_CURRENCY", "value": "$REPORTING_CURRENCY"}
    ],
    "secrets": [
      {"name": "MOOLABS_API_KEY", "valueFrom": "$SECRET_ARN"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/moo-cloud-bill",
        "awslogs-region": "$AWS_REGION",
        "awslogs-stream-prefix": "push"
      }
    }
  }]
}
JSON

aws ecs register-task-definition --cli-input-json file:///tmp/taskdef.json --region "$AWS_REGION" >/dev/null
```

> `cpu`/`memory` (512 / 1024) suit a typical CUR. `push` currently reads the CUR
> fully in memory; for a very large monthly CUR bump `memory` (Fargate allows up to
> 30 GB). This is the reason we chose Fargate over Lambda (no 15-min / memory ceiling).

---

## 5. Verify with one on-demand run BEFORE scheduling

Confirm the wiring works before automating it.

```bash
aws ecs run-task \
  --cluster "$CLUSTER" \
  --launch-type FARGATE \
  --task-definition moo-cloud-bill-push \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SECURITY_GROUP],assignPublicIp=ENABLED}" \
  --region "$AWS_REGION"

# Watch the logs (CUR not delivered yet → it logs "not delivered"; with data → per-day POSTs).
aws logs tail /ecs/moo-cloud-bill --follow --region "$AWS_REGION"
```

A clean run exits 0; a failed day exits non-zero and logs the failing day (the schedule
will surface that as a failed invocation).

---

## 6. Create the daily schedule (EventBridge Scheduler)

```bash
cat > /tmp/schedule-target.json <<JSON
{
  "Arn": "arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:cluster/$CLUSTER",
  "RoleArn": "$SCHED_ROLE_ARN",
  "EcsParameters": {
    "TaskDefinitionArn": "arn:aws:ecs:$AWS_REGION:$ACCOUNT_ID:task-definition/moo-cloud-bill-push",
    "LaunchType": "FARGATE",
    "NetworkConfiguration": {
      "awsvpcConfiguration": {
        "Subnets": ["${SUBNETS//,/\",\"}"],
        "SecurityGroups": ["$SECURITY_GROUP"],
        "AssignPublicIp": "ENABLED"
      }
    }
  }
}
JSON

aws scheduler create-schedule \
  --name moo-cloud-bill-daily-push \
  --schedule-expression "cron(17 6 * * ? *)" \
  --schedule-expression-timezone "UTC" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target file:///tmp/schedule-target.json \
  --region "$AWS_REGION"
```

`cron(17 6 * * ? *)` = 06:17 UTC daily — well after midnight so the prior UTC day's CUR
has refreshed. Adjust as you like.

---

## 7. Teardown (when you want to stop)

```bash
aws scheduler delete-schedule --name moo-cloud-bill-daily-push --region "$AWS_REGION"
aws ecs deregister-task-definition --task-definition moo-cloud-bill-push --region "$AWS_REGION" >/dev/null 2>&1 || true
aws iam delete-role-policy --role-name mooCloudBillSchedulerRole --policy-name run-task
aws iam delete-role --role-name mooCloudBillSchedulerRole
aws iam delete-role-policy --role-name mooCloudBillTaskRole --policy-name read-cur
aws iam delete-role --role-name mooCloudBillTaskRole
aws iam delete-role-policy --role-name mooCloudBillExecRole --policy-name read-moolabs-secret
aws iam detach-role-policy --role-name mooCloudBillExecRole --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam delete-role --role-name mooCloudBillExecRole
# Optional: aws secretsmanager delete-secret --secret-id moo-cloud-bill/api-key --recovery-window-in-days 7
# Optional: aws ecr delete-repository --repository-name "$ECR_REPO" --force
```

---

## Notes

- **No SSO expiry:** the task authenticates with `mooCloudBillTaskRole`, so the daily
  run never fails on an expired token (the failure mode of laptop cron).
- **Secret stays a secret:** plaintext only in Secrets Manager; the task definition
  references it by ARN, the image bakes nothing.
- **Idempotent:** re-running a day is safe — Acute supersedes per period — so a missed
  day self-heals on the next run, and a manual `run-task` never double-counts.
- **EKS instead?** If you run EKS, the equivalent is a `CronJob` with an IRSA service
  account bound to a role like `mooCloudBillTaskRole`, and `MOOLABS_API_KEY` from a
  Kubernetes secret. The env/secret/role contract is identical.
