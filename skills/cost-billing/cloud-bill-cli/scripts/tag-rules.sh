# shellcheck shell=bash
# Shared tag rules for the moo-cloud-bill AWS setup. Sourced by
# aws-fargate-setup.sh and by the cost-billing install.sh, so both entry points
# find, check and ask for tags the same way. Defines functions only; the caller
# owns the globals:
#   TAGS_KV            the tags, one KEY=VALUE per entry
#   MIN_TAGS           the fallback count
#   REQUIRED_TAG_KEYS  set by discover_tag_rule
#   TAG_RULE_SOURCE    set by discover_tag_rule: "policy" or "fallback"
#   TAG_RULE_REASON    set by discover_tag_rule, why the fallback applies:
#                      "unreadable" (AWS refused, the CLI lacks the command,
#                      or python3 is missing)
#                      or "no-keys" (the answer named no keys for this setup)
#
# Where the rule comes from:
#   `aws resourcegroupstaggingapi list-required-tags` returns the tag keys that
#   the organisation's tag policy marks as required (report_required_tag_for).
#   When it names keys for a service this setup creates, those keys are the
#   rule. When it names none, or the call fails, the rule is a count: at least
#   MIN_TAGS tags of any keys.
#
# LIMIT: a member account cannot read service control policies (SCPs). An SCP
# can deny a create for a key that the tag policy does not list. AWS gives no
# way to find that before the create fails.
#
# Bash 3.2-safe: every array expansion uses the ${A[@]+"${A[@]}"} guard.

# Services this setup creates resources in. Matched on the ResourceType prefix
# ("ecr:repository" -> "ecr"), so a type name this file does not spell out
# still counts.
TAG_RULE_SERVICES="ecr ecs iam secretsmanager logs events"

_tag_lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

# 0 when TAGS_KV already holds KEY. IAM roles treat tag keys as
# case-insensitive (Department and department are one key), so this ignores
# case.
has_tag_key() {
  local want have
  want="$(_tag_lower "$1")"
  for have in ${TAGS_KV[@]+"${TAGS_KV[@]}"}; do
    [[ "$(_tag_lower "${have%%=*}")" == "$want" ]] && return 0
  done
  return 1
}

# Validates one KEY=VALUE and appends it to TAGS_KV. An empty VALUE is legal to
# AWS; an empty KEY is not. A repeated KEY is rejected: AWS rejects it, and it
# would count twice toward MIN_TAGS.
add_tag() {
  local kv="$1"
  if [[ "$kv" != *=* || -z "${kv%%=*}" ]]; then
    printf 'Invalid --tag "%s": expected KEY=VALUE\n' "$kv" >&2
    return 1
  fi
  if has_tag_key "${kv%%=*}"; then
    printf 'Duplicate --tag key "%s" (tag keys ignore case)\n' "${kv%%=*}" >&2
    return 1
  fi
  TAGS_KV+=("$kv")
}

# Reads list-required-tags JSON on stdin. Prints each required key once, for
# the services in TAG_RULE_SERVICES only.
_required_keys_from_json() {
  python3 -c '
import json, sys
services = set(sys.argv[1].split())
seen = []
data = json.load(sys.stdin)
for item in data.get("RequiredTags") or []:
    if (item.get("ResourceType") or "").split(":", 1)[0] not in services:
        continue
    for key in item.get("ReportingTagKeys") or []:
        if key and key.lower() not in [s.lower() for s in seen]:
            seen.append(key)
print("\n".join(seen))
' "$TAG_RULE_SERVICES"
}

# Sets REQUIRED_TAG_KEYS, TAG_RULE_SOURCE and TAG_RULE_REASON. $1 = region
# (optional). Never fails: a denied call, an AWS CLI without the command, no
# python3, and an empty answer all mean the fallback count rule. The CLI
# follows NextToken itself (list-required-tags is a paginated operation).
discover_tag_rule() {
  local json keys key
  REQUIRED_TAG_KEYS=()
  TAG_RULE_SOURCE="fallback"
  TAG_RULE_REASON="unreadable"
  json="$(aws resourcegroupstaggingapi list-required-tags ${1:+--region "$1"} --output json 2>/dev/null)" || return 0
  keys="$(printf '%s' "$json" | _required_keys_from_json 2>/dev/null)" || return 0
  TAG_RULE_REASON="no-keys"
  while IFS= read -r key; do
    if [[ -n "$key" ]]; then REQUIRED_TAG_KEYS+=("$key"); fi
  done <<< "$keys"
  if [[ ${#REQUIRED_TAG_KEYS[@]} -gt 0 ]]; then
    TAG_RULE_SOURCE="policy"
    TAG_RULE_REASON=""
  fi
  return 0
}

_tag_scp_note() {
  printf '  Note: AWS does not let this account read service control policies.\n'
  printf '  If a create later fails with an explicit deny, re-run with the missing --tag.\n'
}

# Optional extra tags, after the rule is met. An empty line or end of input
# ends the input.
_tag_ask_extra() {
  local line
  printf '  Add more tags as KEY=VALUE, one per line. Press Enter on an empty line to finish.\n'
  while true; do
    printf '  Tag %s: ' "$(( ${#TAGS_KV[@]} + 1 ))"
    read -r line || { printf '\n'; return 0; }
    [[ -z "$line" ]] && return 0
    add_tag "$line" || true
  done
}

# Policy rule: ask for a value for each missing required key, by name.
_tag_ask_required() {
  local key value
  for key in "$@"; do
    while true; do
      printf '  %s = ' "$key"
      if ! read -r value; then
        printf '\n  ! Input ended before every required tag had a value. Nothing was changed.\n'
        return 1
      fi
      if [[ -z "$value" ]]; then
        printf '  A value is required for %s.\n' "$key"
        continue
      fi
      add_tag "$key=$value" && break
    done
  done
}

# Fallback rule: at least MIN_TAGS tags of any keys, then as many more as the
# operator wants. An empty line ends the input only once MIN_TAGS is met.
_tag_ask_count() {
  local line
  printf '  Enter one tag per line as KEY=VALUE. Add as many as you need.\n'
  printf '  Press Enter on an empty line when you are done.\n'
  while true; do
    printf '  Tag %s: ' "$(( ${#TAGS_KV[@]} + 1 ))"
    if ! read -r line; then
      printf '\n  ! Input ended with %s of %s required tags. Nothing was changed.\n' "${#TAGS_KV[@]}" "$MIN_TAGS"
      return 1
    fi
    if [[ -z "$line" ]]; then
      [[ ${#TAGS_KV[@]} -ge $MIN_TAGS ]] && return 0
      printf '  Need %s more tag(s).\n' "$(( MIN_TAGS - ${#TAGS_KV[@]} ))"
      continue
    fi
    add_tag "$line" || true
  done
}

# Checks TAGS_KV against the rule from discover_tag_rule, and asks for what is
# missing. $1 = 1 when no one can answer (--yes): then a missing tag is an
# error, not a prompt. Returns 1 when the rule is not met.
ensure_tags() {
  local noninteractive="${1:-0}" key missing=()
  if [[ "$TAG_RULE_SOURCE" == "policy" ]]; then
    for key in "${REQUIRED_TAG_KEYS[@]}"; do
      has_tag_key "$key" || missing+=("$key")
    done
    [[ ${#missing[@]} -eq 0 ]] && return 0
  else
    [[ ${#TAGS_KV[@]} -ge $MIN_TAGS ]] && return 0
  fi

  if [[ "$noninteractive" -eq 1 ]]; then
    if [[ "$TAG_RULE_SOURCE" == "policy" ]]; then
      printf '! Your organisation'"'"'s tag policy requires these tag keys. Missing: %s\n' "${missing[*]}" >&2
      printf '  Pass --tag KEY=VALUE for each one.\n' >&2
    else
      printf '! %s tag(s) given; this setup needs at least %s.\n' "${#TAGS_KV[@]}" "$MIN_TAGS" >&2
      printf '  Pass more --tag KEY=VALUE, or --min-tags 0 if your organisation needs none.\n' >&2
    fi
    return 1
  fi

  printf '\n'
  if [[ "$TAG_RULE_SOURCE" == "policy" ]]; then
    printf '  Resource tags: your organisation'"'"'s tag policy requires: %s\n' "${REQUIRED_TAG_KEYS[*]}"
    printf '  Enter a value for each missing key.\n'
    _tag_scp_note
    _tag_ask_required "${missing[@]}" || return 1
    _tag_ask_extra
  else
    if [[ "${TAG_RULE_REASON:-}" == "no-keys" ]]; then
      printf '  Resource tags: your tag policy names no required keys for this setup.\n'
    else
      printf '  Resource tags: could not read your tag policy from AWS (access denied,\n'
      printf '  an AWS CLI without list-required-tags, or no python3).\n'
    fi
    printf '  So this setup needs at least %s tags (you gave %s).\n' "$MIN_TAGS" "${#TAGS_KV[@]}"
    _tag_scp_note
    _tag_ask_count || return 1
  fi
  return 0
}
