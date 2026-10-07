#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=SCRIPTDIR/../scripts/tag-rules.sh
source "$HERE/../scripts/tag-rules.sh"

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
AWS_ARGS_FILE="$TEST_DIR/aws-args"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

# The AWS boundary. AWS_REPLY is what list-required-tags prints; AWS_FAIL=1
# makes the call fail, as a denied call or an old CLI does.
AWS_REPLY=""
AWS_FAIL=0
# shellcheck disable=SC2329
aws() {
  printf '%s\n' "$@" > "$AWS_ARGS_FILE"
  [[ "${1:-} ${2:-}" == "resourcegroupstaggingapi list-required-tags" ]] \
    || { printf 'unexpected fake AWS command: %s\n' "$*" >&2; return 1; }
  [[ $AWS_FAIL -eq 1 ]] && return 254
  printf '%s\n' "$AWS_REPLY"
}

# Shaped like the ListRequiredTags sample response, plus the services this
# setup creates. s3 is not one of them, so its key must be dropped. CostCenter
# repeats in another case and must appear once.
FIXTURE='{"RequiredTags":[
  {"ResourceType":"ecr:repository","CloudFormationResourceTypes":["AWS::ECR::Repository"],"ReportingTagKeys":["CostCenter","Owner"]},
  {"ResourceType":"ecs:cluster","CloudFormationResourceTypes":["AWS::ECS::Cluster"],"ReportingTagKeys":["costcenter","Environment"]},
  {"ResourceType":"iam:role","CloudFormationResourceTypes":["AWS::IAM::Role"],"ReportingTagKeys":["Owner"]},
  {"ResourceType":"s3:bucket","CloudFormationResourceTypes":["AWS::S3::Bucket"],"ReportingTagKeys":["DataClass"]}
]}'

# ── discover_tag_rule ────────────────────────────────────────────────────────
AWS_REPLY="$FIXTURE"
discover_tag_rule "eu-west-1"
[[ "$TAG_RULE_SOURCE" == "policy" ]] || fail "keys for our services must give the policy rule"
[[ "${REQUIRED_TAG_KEYS[*]}" == "CostCenter Owner Environment" ]] \
  || fail "required keys wrong (want our services only, once each): ${REQUIRED_TAG_KEYS[*]}"
grep -qx -- '--region' "$AWS_ARGS_FILE" || fail "discovery did not pass --region"
grep -qx 'eu-west-1' "$AWS_ARGS_FILE" || fail "discovery did not pass the region value"
grep -qx 'json' "$AWS_ARGS_FILE" || fail "discovery must read JSON, not text (text prints None)"

# No tag policy: the call succeeds with nothing in it. That is the fallback.
AWS_REPLY='{"RequiredTags":[]}'
discover_tag_rule ""
[[ "$TAG_RULE_SOURCE" == "fallback" && ${#REQUIRED_TAG_KEYS[@]} -eq 0 ]] \
  || fail "an empty answer must give the fallback rule"
[[ "$TAG_RULE_REASON" == "no-keys" ]] || fail "an empty answer must record no-keys, got $TAG_RULE_REASON"
if grep -qx -- '--region' "$AWS_ARGS_FILE"; then fail "no region must pass no --region"; fi

# Keys only for services this setup does not create: the fallback.
AWS_REPLY='{"RequiredTags":[{"ResourceType":"s3:bucket","ReportingTagKeys":["DataClass"]}]}'
discover_tag_rule ""
[[ "$TAG_RULE_SOURCE" == "fallback" ]] || fail "keys for other services only must give the fallback"

# Denied call or an AWS CLI without the command: the fallback, never a failure.
AWS_FAIL=1
discover_tag_rule "" || fail "a failed call must not fail discovery"
[[ "$TAG_RULE_SOURCE" == "fallback" ]] || fail "a failed call must give the fallback"
[[ "$TAG_RULE_REASON" == "unreadable" ]] || fail "a failed call must record unreadable, got $TAG_RULE_REASON"
AWS_FAIL=0

AWS_REPLY='not json'
discover_tag_rule "" || fail "a bad answer must not fail discovery"
[[ "$TAG_RULE_SOURCE" == "fallback" ]] || fail "a bad answer must give the fallback"

# A stale rule from an earlier call must not survive a new call.
AWS_REPLY="$FIXTURE"; discover_tag_rule ""
AWS_REPLY='{"RequiredTags":[]}'; discover_tag_rule ""
[[ ${#REQUIRED_TAG_KEYS[@]} -eq 0 ]] || fail "discovery kept keys from an earlier call"

# ── ensure_tags: policy rule ─────────────────────────────────────────────────
# Input comes by process substitution, not a pipe: a pipe runs the function in
# a subshell and the new tags would be lost.
MIN_TAGS=3
AWS_REPLY="$FIXTURE"; discover_tag_rule ""

# All required keys given (one in another case): no prompt, nothing read.
TAGS_KV=("costcenter=42" "Owner=platform" "Environment=prod")
ensure_tags 0 </dev/null >/dev/null 2>&1 || fail "all required keys given must not prompt"

# Two keys given is enough when the policy needs only those two: the count
# fallback does not apply on top of the policy.
REQUIRED_TAG_KEYS=("CostCenter" "Owner")
TAGS_KV=("CostCenter=42" "Owner=platform")
ensure_tags 0 </dev/null >/dev/null 2>&1 || fail "the 3-tag fallback must not apply when a policy names keys"
REQUIRED_TAG_KEYS=("CostCenter" "Owner" "Environment")

# Missing keys are asked by name. An empty value is asked again. Then the
# operator adds as many extra tags as they want, until an empty line.
TAGS_KV=("Owner=platform")
ensure_tags 0 < <(printf '42\n\nprod\nTeam=data ops\nTier=gold\n\n') >"$TEST_DIR/policy-out" 2>&1 \
  || fail "policy prompt did not accept the missing values"
[[ "${TAGS_KV[*]}" == "Owner=platform CostCenter=42 Environment=prod Team=data ops Tier=gold" ]] \
  || fail "policy prompt tags wrong: ${TAGS_KV[*]}"
grep -q "CostCenter = " "$TEST_DIR/policy-out" || fail "the prompt did not ask for CostCenter by name"
if grep -q "Owner = " "$TEST_DIR/policy-out"; then fail "a key already given was asked again"; fi
grep -q "A value is required for Environment" "$TEST_DIR/policy-out" \
  || fail "an empty value was not asked again"
grep -q "service control policies" "$TEST_DIR/policy-out" \
  || fail "the prompt did not state the SCP limit"

# End of input during the extra tags is fine: the rule is already met.
TAGS_KV=("Owner=platform" "Environment=prod")
ensure_tags 0 < <(printf '42\n') >/dev/null 2>&1 || fail "end of input after the required keys must pass"
[[ "${TAGS_KV[*]}" == "Owner=platform Environment=prod CostCenter=42" ]] \
  || fail "end-of-input tags wrong: ${TAGS_KV[*]}"

# End of input before every required key has a value: fail.
TAGS_KV=()
if ensure_tags 0 < <(printf '42\n') >/dev/null 2>&1; then
  fail "end of input before every required key must fail"
fi

# No one to ask: fail and name the missing keys.
TAGS_KV=("Owner=platform")
if ensure_tags 1 </dev/null >"$TEST_DIR/policy-yes" 2>&1; then
  fail "a missing required key under --yes must fail"
fi
grep -q "Missing: CostCenter Environment" "$TEST_DIR/policy-yes" \
  || fail "--yes failure did not name the missing keys"

# ── ensure_tags: fallback count rule ─────────────────────────────────────────
# The banner says WHY the fallback applies, so the operator knows whether the
# AWS check ran at all.
TAGS_KV=()
AWS_REPLY='{"RequiredTags":[]}'; discover_tag_rule ""
ensure_tags 0 < <(printf 'A=1\nB=2\nC=3\n\n') >"$TEST_DIR/why-empty" 2>&1 || fail "count prompt failed"
grep -q "names no required keys" "$TEST_DIR/why-empty" || fail "the banner did not say the policy names no keys"
TAGS_KV=()
AWS_FAIL=1; discover_tag_rule ""; AWS_FAIL=0
ensure_tags 0 < <(printf 'A=1\nB=2\nC=3\n\n') >"$TEST_DIR/why-denied" 2>&1 || fail "count prompt failed"
grep -q "could not read your tag policy" "$TEST_DIR/why-denied" \
  || fail "the banner did not say the tag policy could not be read"

AWS_FAIL=1; discover_tag_rule ""; AWS_FAIL=0
MIN_TAGS=3

TAGS_KV=("A=1" "B=2" "C=3")
ensure_tags 0 </dev/null >/dev/null 2>&1 || fail "3 of 3 tags must not prompt"

# One from --tag, two from the prompt. An early empty line does not end input.
TAGS_KV=("Environment=prod")
ensure_tags 0 < <(printf '\nOwner=platform\nCost-Center=42\n\n') >"$TEST_DIR/count-out" 2>&1 \
  || fail "count prompt did not accept the missing tags"
[[ "${TAGS_KV[*]}" == "Environment=prod Owner=platform Cost-Center=42" ]] \
  || fail "count prompt tags wrong: ${TAGS_KV[*]}"
grep -q "at least 3" "$TEST_DIR/count-out" || fail "count prompt did not state the minimum"
grep -q "2 more" "$TEST_DIR/count-out" || fail "an early empty line did not say how many are still needed"

# More than the minimum: the operator keeps adding until an empty line.
TAGS_KV=()
ensure_tags 0 < <(printf 'A=1\nB=2\nC=3\nD=4\nE=five with space\n\n') >/dev/null 2>&1 \
  || fail "count prompt did not accept more than the minimum"
[[ ${#TAGS_KV[@]} -eq 5 ]] || fail "count prompt must accept as many tags as given, got ${#TAGS_KV[@]}"
[[ "${TAGS_KV[4]}" == "E=five with space" ]] || fail "a value with a space was mangled: ${TAGS_KV[4]}"

# A bad or duplicate entry is reported and asked again; it is not recorded.
TAGS_KV=()
ensure_tags 0 < <(printf 'novalue\n=x\nA=1\na=2\nB=2\nC=3\n\n') >"$TEST_DIR/count-bad" 2>&1 \
  || fail "count prompt must recover from a bad entry"
[[ "${TAGS_KV[*]}" == "A=1 B=2 C=3" ]] || fail "bad or duplicate entries were recorded: ${TAGS_KV[*]}"
grep -q "Duplicate" "$TEST_DIR/count-bad" || fail "a duplicate key was not reported"

TAGS_KV=("A=1")
if ensure_tags 0 < <(printf 'B=2\n') >/dev/null 2>&1; then
  fail "end of input below the minimum must fail"
fi

TAGS_KV=("A=1")
if ensure_tags 1 </dev/null >"$TEST_DIR/count-yes" 2>&1; then
  fail "--yes with too few tags must fail"
fi
grep -q -- "--tag" "$TEST_DIR/count-yes" || fail "--yes failure did not say to pass --tag"

# shellcheck disable=SC2034
MIN_TAGS=0
TAGS_KV=()
ensure_tags 0 </dev/null >/dev/null 2>&1 || fail "--min-tags 0 must not require tags"

printf 'PASS: tag rules come from the tag policy, fall back to a count, and ask for what is missing\n'
