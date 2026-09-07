#!/usr/bin/env bash
# AWS credential-session helpers shared by the interactive CUR installer.
# This file is sourced; it deliberately does not enable or alter shell options.

aws_session_account() {
  local aws_profile="${1:-}"
  local account=""

  # Put the STS probe in an explicit conditional so an expired session returns
  # normally even when the caller has `set -e`. Validate the output as an AWS
  # account ID rather than treating arbitrary stdout as proof of authentication.
  if [[ -n "$aws_profile" ]]; then
    if ! account="$(
      aws sts get-caller-identity --profile "$aws_profile" \
        --query Account --output text 2>/dev/null
    )"; then
      return 1
    fi
  elif ! account="$(
    aws sts get-caller-identity --query Account --output text 2>/dev/null
  )"; then
    return 1
  fi

  if [[ "$account" =~ ^[0-9]{12}$ ]]; then
    printf '%s\n' "$account"
    return 0
  fi
  return 1
}

ensure_aws_profile_session() {
  local aws_profile="${1:-}"
  local account=""
  local answer=""

  if account="$(aws_session_account "$aws_profile")"; then
    echo "  ✓ Already authenticated (account $account) — skipping SSO login."
    return 0
  fi

  printf "  Run 'aws sso login%s' now? [Y/n]: " \
    "${aws_profile:+ --profile $aws_profile}"
  read -r answer
  case "$answer" in
    n|N|no|NO)
      echo "    Skipping SSO login — ensure your credentials are valid."
      ;;
    *)
      if [[ -n "$aws_profile" ]]; then
        aws sso login --profile "$aws_profile" \
          || echo "    ! 'aws sso login' failed (non-SSO profile or error) — continuing; configure will report if creds are invalid."
      else
        aws sso login \
          || echo "    ! 'aws sso login' failed (non-SSO profile or error) — continuing; configure will report if creds are invalid."
      fi
      ;;
  esac
}
