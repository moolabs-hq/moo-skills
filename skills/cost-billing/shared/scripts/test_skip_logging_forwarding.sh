#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALL="$HERE/../install.sh"
CLI_DIR="$HERE/../../cloud-bill-cli"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# Exercise the real guided-setup function without executing the installer's
# top-level flow. Its closing brace is the first one at column zero after the
# declaration; nested blocks are indented.
install_function="$(awk '
  /^_run_aws_fargate_setup\(\) \{/ { capture=1 }
  capture { print }
  capture && /^}/ { exit }
' "$INSTALL")"
[[ -n "$install_function" ]] || fail "could not extract _run_aws_fargate_setup"
# shellcheck disable=SC2294
eval "$install_function"

# Replace only the child-shell boundary. The captured text shows the exact argv
# that the installer would pass to aws-fargate-setup.sh.
bash() {
  printf 'CALL'
  while [[ $# -gt 0 ]]; do
    printf ' <%s>' "$1"
    shift
  done
  printf '\n'
}

# shellcheck disable=SC2034
SKIP_LOGGING=1
immediate="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "test-profile")"
printf '%s\n' "$immediate" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--skip-logging>' \
  || fail "immediate run did not forward --skip-logging"

dry_then_real="$(printf 'd\ny\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
call_count="$(printf '%s\n' "$dry_then_real" \
  | awk '{ count += gsub(/CALL </, "") } END { print count + 0 }')"
[[ "$call_count" -eq 2 ]] || fail "dry-run then real run did not launch twice"
printf '%s\n' "$dry_then_real" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--dry-run> <--skip-logging>' \
  || fail "dry-run did not forward --skip-logging after --dry-run"
printf '%s\n' "$dry_then_real" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--skip-logging>' \
  || fail "real run after dry-run did not preserve --skip-logging"

deferred="$(printf 'n\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
printf '%s\n' "$deferred" | grep -q 'aws-fargate-setup.sh.*--dry-run --skip-logging' \
  || fail "deferred dry-run command omitted --skip-logging"
printf '%s\n' "$deferred" | grep -q 'aws-fargate-setup.sh.*--skip-logging' \
  || fail "deferred real-run command omitted --skip-logging"

# shellcheck disable=SC2034
SKIP_LOGGING=0
default_run="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
if printf '%s\n' "$default_run" | grep -q -- '--skip-logging'; then
  fail "default run unexpectedly enabled skip-logging mode"
fi

printf 'PASS: install.sh forwards --skip-logging through every guided setup path\n'
