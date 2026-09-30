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
# that the installer would pass to aws-fargate-setup.sh. Each argv entry is
# wrapped in <> so a value containing a space is still visible as ONE entry.
# shellcheck disable=SC2329
bash() {
  printf 'CALL'
  while [[ $# -gt 0 ]]; do
    printf ' <%s>' "$1"
    shift
  done
  printf '\n'
}

# shellcheck disable=SC2034
SKIP_LOGGING=0

# A tag must reach the setup script as TWO argv entries: the flag, then the
# pair. The setup script renders the per-service shapes; the installer only
# forwards.
# shellcheck disable=SC2034
SETUP_TAGS=("Product-Area=devops" "Environment=integration")
immediate="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "test-profile")"
printf '%s\n' "$immediate" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--tag> <Product-Area=devops> <--tag> <Environment=integration>' \
  || fail "immediate run did not forward both --tag pairs in order"

# A value holding a space and a comma must stay ONE argv entry. This is why the
# installer keeps an array and never re-joins the tags into a single string.
# shellcheck disable=SC2034
SETUP_TAGS=("Owner=Platform Team, EU")
spaced="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
printf '%s\n' "$spaced" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--tag> <Owner=Platform Team, EU>$' \
  || fail "a tag value containing a space and a comma was split or mangled"

# --skip-logging and --tag must compose, not displace each other.
# shellcheck disable=SC2034
SKIP_LOGGING=1
# shellcheck disable=SC2034
SETUP_TAGS=("Environment=integration")
combined="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
printf '%s\n' "$combined" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--skip-logging> <--tag> <Environment=integration>' \
  || fail "--skip-logging and --tag did not compose"

# The dry-run path, and the real run that follows it, must both carry the tags.
dry_then_real="$(printf 'd\ny\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
printf '%s\n' "$dry_then_real" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--dry-run> <--skip-logging> <--tag> <Environment=integration>' \
  || fail "dry-run did not forward --tag after --dry-run"
printf '%s\n' "$dry_then_real" \
  | grep -q 'CALL .*aws-fargate-setup.sh> <--skip-logging> <--tag> <Environment=integration>' \
  || fail "real run after dry-run did not preserve --tag"

# The printed "run it later" commands must be copy-pasteable, so a value with a
# space has to come back quoted.
# shellcheck disable=SC2034
SETUP_TAGS=("Owner=Platform Team")
deferred="$(printf 'n\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
printf '%s\n' "$deferred" | grep -q -- "--tag Owner=Platform\\\\ Team" \
  || fail "deferred command did not quote a tag value containing a space"

# No tags: nothing about tags may appear on any path.
# shellcheck disable=SC2034
SETUP_TAGS=()
# shellcheck disable=SC2034
SKIP_LOGGING=0
default_run="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
if printf '%s\n' "$default_run" | grep -q -- '--tag'; then
  fail "a run with no tags must not pass --tag"
fi

printf 'PASS: install.sh forwards --tag through every guided setup path\n'
