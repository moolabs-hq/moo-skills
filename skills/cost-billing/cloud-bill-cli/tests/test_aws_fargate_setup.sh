#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/../scripts/aws-fargate-setup.sh"

# Sourcing is intentional: the script's guarded main lets these tests exercise
# the real verification control flow while replacing only the AWS boundary.
# shellcheck disable=SC1090,SC1091
source "$SCRIPT"

# ShellCheck cannot see these variables being consumed inside the sourced file.
# shellcheck disable=SC2034
ASSUME_YES=1
# shellcheck disable=SC2034
DRY_RUN=0
# shellcheck disable=SC2034
AWS_REGION="us-east-1"
# shellcheck disable=SC2034
CLUSTER="moo-cloud-bill"
# shellcheck disable=SC2034
SUBNETS="subnet-test"
# shellcheck disable=SC2034
SECURITY_GROUP="sg-test"
# shellcheck disable=SC2034
VERIFY_MAX_ATTEMPTS=3
# shellcheck disable=SC2034
VERIFY_RETRY_DELAY_SECONDS=0

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
RUN_COUNT_FILE="$TEST_DIR/run-count"
SECRET_UPDATE_MARKER="$TEST_DIR/secret-updated"
LOGS_CALL_MARKER="$TEST_DIR/logs-called"
TASK_DEF_FILE="$TEST_DIR/task-definition.json"
SCENARIO=""

reset_run_count() { printf '0\n' > "$RUN_COUNT_FILE"; }
run_count() { tr -d '[:space:]' < "$RUN_COUNT_FILE"; }
sleep() { :; }

# Invoked indirectly by the sourced setup functions.
# shellcheck disable=SC2329
aws() {
  local service="${1:-}" operation="${2:-}"
  if [[ "$service" == "logs" ]]; then
    : > "$LOGS_CALL_MARKER"
    [[ "$SCENARIO" == "logged_taskdef" || "$SCENARIO" == "logs_denied_taskdef" ]] || {
      printf 'CloudWatch Logs must not be called in scenario %s\n' "$SCENARIO" >&2
      return 1
    }
    if [[ "$SCENARIO" == "logs_denied_taskdef" ]]; then
      return 42
    fi
    case "$operation" in
      describe-log-groups)
        printf '/ecs/moo-cloud-bill\n'
        return 0 ;;
      put-retention-policy)
        return 0 ;;
    esac
  fi

  if [[ "$service $operation" == "secretsmanager describe-secret" ]]; then
    case "$SCENARIO" in
      secret_exists_update|secret_update_failure)
        printf 'arn:aws:secretsmanager:us-east-1:123456789012:secret:moo-cloud-bill/api-key-test\n'
        return 0 ;;
    esac
  fi

  if [[ "$service $operation" == "secretsmanager put-secret-value" ]]; then
    local previous="" supplied_key=""
    shift 2
    while [[ $# -gt 0 ]]; do
      if [[ "$previous" == "--secret-string" ]]; then supplied_key="$1"; break; fi
      previous="$1"
      shift
    done
    [[ "$supplied_key" == "$API_KEY" ]] || {
      printf 'put-secret-value did not receive the current API key\n' >&2
      return 1
    }
    [[ "$SCENARIO" == "secret_update_failure" ]] && return 42
    : > "$SECRET_UPDATE_MARKER"
    return 0
  fi

  if [[ "$service $operation" == "ecs run-task" ]]; then
    local count
    count="$(run_count)"
    count=$((count + 1))
    printf '%s\n' "$count" > "$RUN_COUNT_FILE"
    printf 'arn:aws:ecs:us-east-1:123456789012:task/moo-cloud-bill/task%s\n' "$count"
    return 0
  fi

  if [[ "$service $operation" == "ecs describe-clusters" ]]; then
    case "$SCENARIO" in
      skip_logging_taskdef|logged_taskdef|logs_denied_taskdef)
        printf 'ACTIVE\n'
        return 0 ;;
    esac
  fi

  if [[ "$service $operation" == "ecs register-task-definition" ]]; then
    local previous=""
    shift 2
    while [[ $# -gt 0 ]]; do
      if [[ "$previous" == "--cli-input-json" ]]; then
        printf '%s\n' "$1" > "$TASK_DEF_FILE"
        return 0
      fi
      previous="$1"
      shift
    done
    printf 'register-task-definition did not receive --cli-input-json\n' >&2
    return 1
  fi

  if [[ "$service $operation" == "ecs wait" ]]; then
    return 0
  fi

  if [[ "$service $operation" == "ecs describe-tasks" ]]; then
    local task_arn="" previous=""
    shift 2
    while [[ $# -gt 0 ]]; do
      if [[ "$previous" == "--tasks" ]]; then task_arn="$1"; break; fi
      previous="$1"
      shift
    done
    case "$SCENARIO:$task_arn" in
      retry_then_success:*task1)
        printf 'None\tTaskFailedToStart\tCannotPullContainerError: ECR timeout\n' ;;
      retry_then_success:*task2|success:*)
        printf '0\tEssentialContainerExited\tEssential container in task exited\n' ;;
      exhaust_retries:*)
        printf 'None\tTaskFailedToStart\tCannotPullContainerError: ECR timeout\n' ;;
      application_failure:*)
        printf '1\tEssentialContainerExited\tEssential container in task exited\n' ;;
      *)
        printf 'unexpected fake AWS call for %s:%s\n' "$SCENARIO" "$task_arn" >&2
        return 1 ;;
    esac
    return 0
  fi

  printf 'unexpected fake AWS command: %s %s\n' "$service" "$operation" >&2
  return 1
}

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# A rerun must offer to propagate the API key captured by `init` into the
# existing Fargate secret. The real key must never appear in terminal output.
# shellcheck disable=SC2034
ACCOUNT_ID="123456789012"
# shellcheck disable=SC2034
SECRET_NAME="moo-cloud-bill/api-key"
# shellcheck disable=SC2034
API_KEY="test-rotated-api-key-never-print"
SCENARIO="secret_exists_update"
rm -f "$SECRET_UPDATE_MARKER"
secret_output="$TEST_DIR/secret-output"
step_secret >"$secret_output" 2>&1 || fail "existing secret update should succeed"
[[ -e "$SECRET_UPDATE_MARKER" ]] || fail "existing secret was silently reused instead of updated"
grep -q "put-secret-value.*\\*\\*\\*\\*hidden\\*\\*\\*\\*" "$secret_output" \
  || fail "secret update command was not shown safely masked"
if grep -q "$API_KEY" "$secret_output"; then
  fail "secret update leaked the API key to terminal output"
fi

SCENARIO="secret_update_failure"
secret_failure_output="$TEST_DIR/secret-failure-output"
if step_secret >"$secret_failure_output" 2>&1; then
  fail "failed secret update must stop setup"
fi
grep -q "cannot silently run with a stale API key" "$secret_failure_output" \
  || fail "secret update failure did not explain the stale-key risk"

# Restricted-IAM mode must avoid every CloudWatch Logs API call and omit the
# awslogs driver from the registered task definition. The default path must
# continue to verify/reuse the log group and retain the existing configuration.
# shellcheck disable=SC2034
IMAGE="123456789012.dkr.ecr.us-east-1.amazonaws.com/moo-cloud-bill:latest"
# shellcheck disable=SC2034
EXEC_ROLE_ARN="arn:aws:iam::123456789012:role/mooCloudBillExecRole"
# shellcheck disable=SC2034
TASK_ROLE_ARN="arn:aws:iam::123456789012:role/mooCloudBillTaskRole"
# shellcheck disable=SC2034
CUR_BUCKET="test-cur-bucket"
# shellcheck disable=SC2034
CUR_PREFIX="cur2"
# shellcheck disable=SC2034
REPORT_NAME="moolabs-cur2"
# shellcheck disable=SC2034
BUCKET_REGION="us-east-1"
# shellcheck disable=SC2034
ACUTE_BASE="https://acute.moolabs.com"
# shellcheck disable=SC2034
REPORTING_CURRENCY="USD"
# shellcheck disable=SC2034
SECRET_ARN="arn:aws:secretsmanager:us-east-1:123456789012:secret:moo-cloud-bill/api-key-test"

SCENARIO="skip_logging_taskdef"
# shellcheck disable=SC2034
SKIP_LOGGING=1
rm -f "$LOGS_CALL_MARKER" "$TASK_DEF_FILE"
skip_logging_output="$TEST_DIR/skip-logging-output"
step_cluster_taskdef >"$skip_logging_output" 2>&1 \
  || fail "skip-logging task definition should register successfully"
[[ ! -e "$LOGS_CALL_MARKER" ]] || fail "skip-logging mode called CloudWatch Logs"
python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$TASK_DEF_FILE" \
  || fail "skip-logging task definition is not valid JSON"
if grep -q 'logConfiguration\|awslogs' "$TASK_DEF_FILE"; then
  fail "skip-logging task definition still contains awslogs configuration"
fi
grep -q "stdout/stderr will not be retained" "$skip_logging_output" \
  || fail "skip-logging task registration did not warn about missing logs"

SCENARIO="logged_taskdef"
# shellcheck disable=SC2034
SKIP_LOGGING=0
rm -f "$LOGS_CALL_MARKER" "$TASK_DEF_FILE"
step_cluster_taskdef >"$TEST_DIR/logged-taskdef-output" 2>&1 \
  || fail "default task definition should register successfully"
[[ -e "$LOGS_CALL_MARKER" ]] || fail "default mode did not verify the CloudWatch log group"
python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$TASK_DEF_FILE" \
  || fail "default task definition is not valid JSON"
grep -q '"logConfiguration"' "$TASK_DEF_FILE" \
  || fail "default task definition lost its log configuration"
grep -q '"awslogs-group":"/ecs/moo-cloud-bill"' "$TASK_DEF_FILE" \
  || fail "default task definition lost its CloudWatch log group"

SCENARIO="logs_denied_taskdef"
rm -f "$LOGS_CALL_MARKER" "$TASK_DEF_FILE"
logs_denied_output="$TEST_DIR/logs-denied-output"
if step_cluster_taskdef >"$logs_denied_output" 2>&1; then
  fail "default mode must fail when CloudWatch logging cannot be configured"
fi
[[ -e "$LOGS_CALL_MARKER" ]] || fail "default mode did not attempt to verify CloudWatch Logs"
[[ ! -e "$TASK_DEF_FILE" ]] || fail "default mode registered a task definition after logging failed"
grep -q "Stopping before registering the task definition" "$logs_denied_output" \
  || fail "default logging failure did not explain that task registration was blocked"

SCENARIO="retry_then_success"
reset_run_count
retry_output="$TEST_DIR/retry-output"
step_verify >"$retry_output" 2>&1 || fail "pre-start failure should retry and succeed"
[[ "$(run_count)" == "2" ]] || fail "expected exactly two attempts for transient pull failure"
grep -q "application never started; retrying safely" "$retry_output" \
  || fail "safe retry explanation missing"
grep -q "completed successfully (exit code 0)" "$retry_output" \
  || fail "successful retry result missing"

SCENARIO="exhaust_retries"
reset_run_count
exhaustion_output="$TEST_DIR/exhaustion-output"
if step_verify >"$exhaustion_output" 2>&1; then
  fail "three consecutive pre-start failures must fail verification"
fi
[[ "$(run_count)" == "3" ]] || fail "retry exhaustion must stop after exactly three attempts"
grep -q "Verification failed after 3 attempt(s)" "$exhaustion_output" \
  || fail "retry-exhaustion message missing"
grep -q "daily schedule will not be created" "$exhaustion_output" \
  || fail "retry exhaustion must block schedule creation"

SCENARIO="application_failure"
reset_run_count
failure_output="$TEST_DIR/failure-output"
if step_verify >"$failure_output" 2>&1; then
  fail "non-zero application exit must fail verification"
fi
[[ "$(run_count)" == "1" ]] || fail "application failure must not be retried"
grep -q "daily schedule will not be created" "$failure_output" \
  || fail "schedule-blocking failure message missing"

SCENARIO="success"
reset_run_count
# shellcheck disable=SC2034
SKIP_LOGGING=1
skip_verify_output="$TEST_DIR/skip-verify-output"
step_verify >"$skip_verify_output" 2>&1 || fail "skip-logging verification should succeed"
grep -q "CloudWatch logging is disabled" "$skip_verify_output" \
  || fail "skip-logging verification did not warn that logs are unavailable"
if grep -q "aws logs tail" "$skip_verify_output"; then
  fail "skip-logging verification printed an unusable CloudWatch Logs command"
fi

SCENARIO="application_failure"
reset_run_count
skip_failure_output="$TEST_DIR/skip-failure-output"
if step_verify >"$skip_failure_output" 2>&1; then
  fail "skip-logging application failure must still fail verification"
fi
[[ "$(run_count)" == "1" ]] || fail "skip-logging application failure must not be retried"
grep -q "diagnose from the ECS stop code/reason" "$skip_failure_output" \
  || fail "skip-logging failure did not provide an available diagnosis path"
if grep -q "aws logs tail" "$skip_failure_output"; then
  fail "skip-logging failure printed an unusable CloudWatch Logs command"
fi

SCENARIO="success"
reset_run_count
# shellcheck disable=SC2034
DRY_RUN=1
dry_output="$TEST_DIR/dry-output"
step_verify >"$dry_output" 2>&1 || fail "dry-run verification should succeed"
[[ "$(run_count)" == "0" ]] || fail "dry-run must not call AWS"
grep -q "would wait for the task" "$dry_output" \
  || fail "dry-run verification plan missing"

schedule_marker="$TEST_DIR/schedule-created"
ensure_prereq() { :; }
ensure_container_cli() { :; }
load_cli_config() { :; }
resolve_api_key() { :; }
discover_network() { :; }
show_plan() { :; }
step_secret() { :; }
step_image() { :; }
step_exec_role() { :; }
step_task_role() { :; }
step_scheduler_role() { :; }
step_cluster_taskdef() { return 0; }
step_verify() { return 1; }
step_schedule() { : > "$schedule_marker"; }
aws() {
  [[ "${1:-} ${2:-}" == "sts get-caller-identity" ]] || return 1
  printf '123456789012\n'
}
# shellcheck disable=SC2034
DRY_RUN=0
# shellcheck disable=SC2034
SKIP_LOGGING=0
if (main >"$TEST_DIR/main-output" 2>&1); then
  fail "main must fail when verification fails"
fi
[[ ! -e "$schedule_marker" ]] || fail "main created a schedule after failed verification"

post_secret_marker="$TEST_DIR/post-secret-step-ran"
step_secret() { return 1; }
step_image() { : > "$post_secret_marker"; }
step_verify() { return 0; }
if (main >"$TEST_DIR/main-secret-failure-output" 2>&1); then
  fail "main must fail when an API-key secret update fails"
fi
[[ ! -e "$post_secret_marker" ]] \
  || fail "main continued to image/task mutations after secret setup failed"
grep -q "Secret setup did not complete" "$TEST_DIR/main-secret-failure-output" \
  || fail "main did not explain that secret failure stopped the setup"

printf 'PASS: Fargate setup rotates secrets, configures optional logging, and verifies safely\n'
