#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALL="$HERE/../install.sh"
CLI_DIR="$HERE/../../cloud-bill-cli"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# Exercise the real guided-setup functions without executing the installer's
# top-level flow. Its closing brace is the first one at column zero after the
# declaration; nested blocks are indented.
install_function="$(awk '
  /^(_collect_setup_tags|_run_aws_fargate_setup)\(\) \{/ { capture=1 }
  capture { print }
  capture && /^}/ { capture=0; if (++done == 2) exit }
' "$INSTALL")"
[[ -n "$install_function" ]] || fail "could not extract the guided-setup functions"
# shellcheck disable=SC2294
eval "$install_function"

# The tag rule lookup is the only AWS call the installer makes here. By default
# it fails, as a denied call does, so the count fallback applies. A test that
# needs a tag policy sets AWS_REQUIRED_TAGS_JSON.
AWS_REQUIRED_TAGS_JSON=""
# shellcheck disable=SC2329
aws() {
  case "${1:-} ${2:-}" in
    "resourcegroupstaggingapi list-required-tags")
      [[ -n "$AWS_REQUIRED_TAGS_JSON" ]] || return 254
      printf '%s\n' "$AWS_REQUIRED_TAGS_JSON" ;;
    "configure get") return 1 ;;
    *) printf 'unexpected AWS call in installer test: %s\n' "$*" >&2; return 1 ;;
  esac
}

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
# The forwarding cases below are about argv shape, not the tag minimum, which
# has its own cases at the end.
# shellcheck disable=SC2034
SETUP_MIN_TAGS=0
# shellcheck disable=SC2034
SETUP_MIN_TAGS_SET=0

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


# ── minimum tag count ────────────────────────────────────────────────────────
# With fewer than SETUP_MIN_TAGS tags, the installer asks for more right after
# the operator chooses to run. The operator can add any number. The dry-run and
# the real run that follows must both get the SAME tags, so it asks only once.
# shellcheck disable=SC2034
SETUP_MIN_TAGS=3
# shellcheck disable=SC2034
SETUP_TAGS=("Environment=prod")
prompted="$(printf 'd\nOwner=platform\nCost-Center=42\nTeam=data ops\n\ny\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
expected='<--tag> <Environment=prod> <--tag> <Owner=platform> <--tag> <Cost-Center=42> <--tag> <Team=data ops>'
printf '%s\n' "$prompted" | grep -q -- "<--dry-run> $expected" \
  || fail "dry-run did not get the prompted tags: $prompted"
[[ "$(printf '%s\n' "$prompted" | grep -c -- "$expected")" == "2" ]] \
  || fail "the real run after the dry-run did not reuse the prompted tags"

# A bad entry is asked again and never forwarded.
# shellcheck disable=SC2034
SETUP_TAGS=()
bad="$(printf 'y\nnovalue\nA=1\nA=2\nB=2\nC=3\n\n' | _run_aws_fargate_setup "$CLI_DIR" "" 2>&1)"
printf '%s\n' "$bad" | grep -q -- '<--tag> <A=1> <--tag> <B=2> <--tag> <C=3>$' \
  || fail "bad or duplicate prompted tags were forwarded: $bad"

# End of input below the minimum: do not run the setup at all.
# shellcheck disable=SC2034
SETUP_TAGS=()
short="$(printf 'y\nA=1\n' | _run_aws_fargate_setup "$CLI_DIR" "" 2>&1)"
if printf '%s\n' "$short" | grep -q 'CALL'; then
  fail "setup ran although input ended below the tag minimum"
fi

# "Not now" asks for nothing.
# shellcheck disable=SC2034
SETUP_TAGS=()
later="$(printf 'n\n' | _run_aws_fargate_setup "$CLI_DIR" "" 2>&1)"
if printf '%s\n' "$later" | grep -q 'Tag 1'; then
  fail "choosing 'not now' must not ask for tags"
fi

# An explicit --min-tags reaches the setup script, so it does not ask again.
# shellcheck disable=SC2034
SETUP_MIN_TAGS=0
# shellcheck disable=SC2034
SETUP_MIN_TAGS_SET=1
none="$(printf 'y\n' | _run_aws_fargate_setup "$CLI_DIR" "")"
printf '%s\n' "$none" | grep -q -- '<--min-tags> <0>' \
  || fail "--min-tags was not forwarded: $none"


# A tag policy that names required keys is the rule: the installer asks for
# each missing key by name, then for any extra tags. The count fallback does
# not apply, so two keys are enough when the policy names two.
# shellcheck disable=SC2034
SETUP_MIN_TAGS=3
# shellcheck disable=SC2034
SETUP_MIN_TAGS_SET=0
AWS_REQUIRED_TAGS_JSON='{"RequiredTags":[{"ResourceType":"ecr:repository","ReportingTagKeys":["CostCenter","Owner"]},{"ResourceType":"s3:bucket","ReportingTagKeys":["DataClass"]}]}'
# shellcheck disable=SC2034
SETUP_TAGS=("owner=platform")
policy="$(printf 'y\n42\n\n' | _run_aws_fargate_setup "$CLI_DIR" "" 2>&1)"
printf '%s\n' "$policy" | grep -q -- '<--tag> <owner=platform> <--tag> <CostCenter=42>$' \
  || fail "the tag policy keys were not asked for and forwarded: $policy"
printf '%s\n' "$policy" | grep -q 'CostCenter = ' || fail "the installer did not ask for CostCenter by name"
if printf '%s\n' "$policy" | grep -q 'DataClass'; then
  fail "a key for a service the setup does not create was asked for"
fi
printf '%s\n' "$policy" | grep -q 'service control policies' \
  || fail "the installer did not state the SCP limit"
AWS_REQUIRED_TAGS_JSON=""

printf 'PASS: install.sh forwards --tag through every guided setup path\n'
