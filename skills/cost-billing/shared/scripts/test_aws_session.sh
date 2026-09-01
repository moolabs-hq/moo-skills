#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/aws-session.sh"

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
LOGIN_MARKER="$TEST_DIR/sso-login"
SCENARIO=""

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

aws() {
  local service="${1:-}" operation="${2:-}"
  if [[ "$service $operation" == "sts get-caller-identity" ]]; then
    case "$SCENARIO" in
      valid)
        [[ " $* " == *" --profile moolabs-prod "* ]] || return 64
        printf '968715863884\n'
        return 0
        ;;
      valid_default)
        [[ " $* " != *" --profile "* ]] || return 65
        printf '123456789012\n'
        return 0
        ;;
      expired)
        return 255
        ;;
      malformed)
        printf 'not-an-account-id\n'
        return 0
        ;;
    esac
  fi
  if [[ "$service $operation" == "sso login" ]]; then
    : > "$LOGIN_MARKER"
    return 0
  fi
  return 1
}

SCENARIO="valid"
valid_output="$TEST_DIR/valid-output"
ensure_aws_profile_session "moolabs-prod" </dev/null >"$valid_output" 2>&1 \
  || fail "valid session should be reused"
grep -q "Already authenticated.*968715863884" "$valid_output" \
  || fail "valid session did not report reuse"
if grep -q "Run 'aws sso login" "$valid_output"; then
  fail "valid session was prompted to log in again"
fi
[[ ! -e "$LOGIN_MARKER" ]] || fail "valid session called aws sso login"

SCENARIO="valid_default"
default_output="$TEST_DIR/default-output"
ensure_aws_profile_session "" </dev/null >"$default_output" 2>&1 \
  || fail "valid default credential chain should be reused"
grep -q "Already authenticated.*123456789012" "$default_output" \
  || fail "default credential chain did not report reuse"

SCENARIO="expired"
expired_output="$TEST_DIR/expired-output"
printf 'y\n' | ensure_aws_profile_session "moolabs-prod" >"$expired_output" 2>&1 \
  || fail "expired session should offer and run SSO login"
grep -q "Run 'aws sso login --profile moolabs-prod' now" "$expired_output" \
  || fail "expired session did not show the login prompt"
[[ -e "$LOGIN_MARKER" ]] || fail "expired session did not call aws sso login"

rm -f "$LOGIN_MARKER"
SCENARIO="expired"
declined_output="$TEST_DIR/declined-output"
printf 'n\n' | ensure_aws_profile_session "moolabs-prod" >"$declined_output" 2>&1 \
  || fail "declining SSO login should continue with a warning"
[[ ! -e "$LOGIN_MARKER" ]] || fail "declined login still called aws sso login"
grep -q "Skipping SSO login" "$declined_output" \
  || fail "declined login did not explain the skipped refresh"

SCENARIO="malformed"
malformed_output="$TEST_DIR/malformed-output"
printf 'y\n' | ensure_aws_profile_session "moolabs-prod" >"$malformed_output" 2>&1 \
  || fail "malformed STS identity should fall back to SSO login"
[[ -e "$LOGIN_MARKER" ]] || fail "malformed STS identity was treated as authenticated"

printf 'PASS: active AWS sessions are reused and only invalid sessions offer SSO login\n'
