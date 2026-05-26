#!/usr/bin/env bash
# Install multiphase-project-skill into a Claude Code / Cursor / Copilot / etc.
# skills directory.
#
# Auto-detects the user's platform by checking for well-known paths. If
# auto-detection fails or the user wants something specific, pass --platform.
#
#   ./install.sh                              # auto-detect
#   ./install.sh --platform claude            # $CLAUDE_CONFIG_DIR/skills/ (or ~/.claude/skills/)
#   ./install.sh --platform claude-moolabs    # ~/.claude-moolabs/skills/
#   ./install.sh --platform cursor            # ./.cursor/rules/
#   ./install.sh --platform copilot           # ./.github/skills/
#   ./install.sh --platform universal         # ~/.agents/skills/
#   ./install.sh --all                        # install to every detected platform
#   ./install.sh --dry-run                    # print what would be done
#
# The `claude` platform honors $CLAUDE_CONFIG_DIR (the same env var Claude
# Code uses). If unset, it defaults to ~/.claude. The `claude-moolabs`
# platform is always the literal path ~/.claude-moolabs — pass it
# explicitly when you want that exact target regardless of env.
#
set -euo pipefail

SKILL_NAME="multiphase-project-skill"
SOURCE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

PLATFORM=""
DRY_RUN=0
INSTALL_ALL=0

usage() {
  sed -n '2,16p' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM="$2"; shift 2 ;;
    --platform=*)
      PLATFORM="${1#*=}"; shift ;;
    --all)
      INSTALL_ALL=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage 0 ;;
    *)
      echo "unknown arg: $1" >&2; usage 1 ;;
  esac
done

detect_platforms() {
  local detected=()
  [[ -d "$CLAUDE_DIR" ]]             && detected+=("claude")
  # Only surface claude-moolabs separately when it's a distinct path
  # from CLAUDE_DIR — otherwise installing both would duplicate work.
  if [[ -d "$HOME/.claude-moolabs" && "$HOME/.claude-moolabs" != "$CLAUDE_DIR" ]]; then
    detected+=("claude-moolabs")
  fi
  [[ -d ".cursor" || -d "$HOME/.cursor" ]] && detected+=("cursor")
  [[ -d ".github" ]]                 && detected+=("copilot")
  [[ -d "$HOME/.agents" || -d ".agents" ]] && detected+=("universal")
  [[ -d "$HOME/.gemini" ]]           && detected+=("gemini")
  [[ -d ".clinerules" ]]             && detected+=("cline")
  [[ -d "$HOME/.codeium/windsurf" || -d ".windsurf" ]] && detected+=("windsurf")
  printf '%s\n' "${detected[@]}"
}

dest_for() {
  case "$1" in
    claude)          echo "$CLAUDE_DIR/skills/$SKILL_NAME" ;;
    claude-moolabs)  echo "$HOME/.claude-moolabs/skills/$SKILL_NAME" ;;
    cursor)
      if [[ -d "$HOME/.cursor" && ! -d ".cursor" ]]; then
        echo "$HOME/.cursor/rules/$SKILL_NAME"
      else
        echo ".cursor/rules/$SKILL_NAME"
      fi
      ;;
    copilot)         echo ".github/skills/$SKILL_NAME" ;;
    universal)
      if [[ -d ".agents" ]]; then
        echo ".agents/skills/$SKILL_NAME"
      else
        echo "$HOME/.agents/skills/$SKILL_NAME"
      fi
      ;;
    gemini)          echo "$HOME/.gemini/skills/$SKILL_NAME" ;;
    cline)           echo ".clinerules/$SKILL_NAME" ;;
    windsurf)
      if [[ -d ".windsurf" ]]; then
        echo ".windsurf/rules/$SKILL_NAME"
      else
        echo "$HOME/.codeium/windsurf/skills/$SKILL_NAME"
      fi
      ;;
    *)
      echo "unknown platform: $1" >&2; return 1 ;;
  esac
}

install_one() {
  local platform="$1"
  local dest
  dest="$( dest_for "$platform" )" || return 1

  echo "[$platform] -> $dest"
  if [[ $DRY_RUN -eq 1 ]]; then
    return 0
  fi

  mkdir -p "$( dirname "$dest" )"
  if [[ -e "$dest" || -L "$dest" ]]; then
    rm -rf "$dest"
  fi
  cp -R "$SOURCE_DIR" "$dest"
  # don't ship install.sh itself inside the installed skill — it's metadata
  rm -f "$dest/install.sh"
  echo "[$platform] installed."
}

targets=()
if [[ $INSTALL_ALL -eq 1 ]]; then
  mapfile -t targets < <( detect_platforms )
  if [[ ${#targets[@]} -eq 0 ]]; then
    echo "No platforms detected. Specify --platform explicitly." >&2
    exit 1
  fi
elif [[ -n "$PLATFORM" ]]; then
  targets=("$PLATFORM")
else
  mapfile -t detected < <( detect_platforms )
  if [[ ${#detected[@]} -eq 0 ]]; then
    echo "Could not auto-detect a platform. Available: claude, claude-moolabs, cursor, copilot, universal, gemini, cline, windsurf" >&2
    echo "Run again with --platform <name> or --all." >&2
    exit 1
  fi
  targets=("${detected[0]}")
  if [[ ${#detected[@]} -gt 1 ]]; then
    echo "Multiple platforms detected: ${detected[*]}"
    echo "Installing to first: ${targets[0]}"
    echo "Use --all to install everywhere, or --platform <name> to pick one."
  fi
fi

for platform in "${targets[@]}"; do
  install_one "$platform"
done

if [[ $DRY_RUN -eq 0 ]]; then
  echo ""
  echo "Installed. To use, open a new session and type:"
  echo ""
  echo "  /$SKILL_NAME Build a real-time analytics dashboard"
  echo ""
fi
