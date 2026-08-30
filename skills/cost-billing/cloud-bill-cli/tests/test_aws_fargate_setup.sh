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
SCENARIO=""

reset_run_count() { printf '0\n' > "$RUN_COUNT_FILE"; }
run_count() { tr -d '[:space:]' < "$RUN_COUNT_FILE"; }
sleep() { :; }

# Invoked indirectly by the sourced setup functions.
# shellcheck disable=SC2329
aws() {
  local service="${1:-}" operation="${2:-}"
  if [[ "$service $operation" == "ecs run-task" ]]; then
    local count
    count="$(run_count)"
    count=$((count + 1))
    printf '%s\n' "$count" > "$RUN_COUNT_FILE"
    printf 'arn:aws:ecs:us-east-1:123456789012:task/moo-cloud-bill/task%s\n' "$count"
    return 0
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
if (main >"$TEST_DIR/main-output" 2>&1); then
  fail "main must fail when verification fails"
fi
[[ ! -e "$schedule_marker" ]] || fail "main created a schedule after failed verification"

printf 'PASS: Fargate verification retries only safe pre-start failures and blocks application failures\n'
