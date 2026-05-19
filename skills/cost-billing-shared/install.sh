#!/usr/bin/env bash
# Install the Cost+Billing Discovery & Instrumentation suite (6 skills + shared dir).
# Detects the platform (Claude Code, Cursor, Copilot, etc.) and copies each skill into
# the right install location.
#
# Usage:
#   ./install.sh                          # auto-detect platform; install all 6 skills
#   ./install.sh --platform claude-code   # explicit platform
#   ./install.sh --user                    # install at user scope ($CLAUDE_CONFIG_DIR/skills/ or ~/.claude/skills/)
#   ./install.sh --project                 # install at project scope (./.claude/skills/)
#   ./install.sh --dry-run                 # show what would happen
#   ./install.sh --uninstall               # remove all 6 skills
#
# Env vars honored:
#   CLAUDE_CONFIG_DIR    # Claude Code user-scope root (overrides ~/.claude); installs go to $CLAUDE_CONFIG_DIR/skills/
#
# Skills installed:
#   cost-billing-discovery
#   cost-billing-cloud-bill
#   cost-billing-instrument
#   cost-billing-drift-lint
#   cost-billing-adversarial-review
#   cost-billing-reconcile
#   cost-billing-shared          (shared docs; not a slash-invocable skill)

set -euo pipefail

SUITE_SKILLS=(
  "cost-billing-discovery"
  "cost-billing-cloud-bill"
  "cost-billing-instrument"
  "cost-billing-drift-lint"
  "cost-billing-adversarial-review"
  "cost-billing-reconcile"
  "cost-billing-shared"
)

# Locate the suite source directory (the parent of this script).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PLATFORM=""
SCOPE="user"
DRY_RUN=0
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --user) SCOPE="user"; shift ;;
    --project) SCOPE="project"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)
      head -28 "$0" | tail -27
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

detect_platform() {
  # Honor CLAUDE_CONFIG_DIR (Claude Code env var overriding the default ~/.claude location).
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" && -d "$CLAUDE_CONFIG_DIR" ]]; then
    echo "claude-code"
  elif [[ -d "$HOME/.claude" ]] || [[ -d "./.claude" ]]; then
    echo "claude-code"
  elif [[ -d "./.cursor" ]] || [[ -d "$HOME/.cursor" ]]; then
    echo "cursor"
  elif [[ -d "./.github" ]] && [[ -f "./.github/copilot-instructions.md" ]]; then
    echo "copilot"
  elif [[ -d "$HOME/.codeium/windsurf" ]] || [[ -d "./.windsurf" ]]; then
    echo "windsurf"
  elif [[ -d "./.clinerules" ]]; then
    echo "cline"
  elif [[ -d "$HOME/.gemini" ]]; then
    echo "gemini"
  elif [[ -d "./.kiro" ]]; then
    echo "kiro"
  elif [[ -d "./.trae" ]]; then
    echo "trae"
  elif [[ -d "./.roo" ]]; then
    echo "roo"
  elif [[ -d "$HOME/.config/goose" ]]; then
    echo "goose"
  elif [[ -d "$HOME/.config/opencode" ]]; then
    echo "opencode"
  elif [[ -d "$HOME/.agents" ]] || [[ -d "./.agents" ]]; then
    echo "universal"
  else
    echo ""
  fi
}

resolve_dest_dir() {
  local platform="$1" scope="$2"
  case "$platform" in
    claude-code)
      # Claude Code honors CLAUDE_CONFIG_DIR over $HOME/.claude (per the Claude Code CLI spec).
      # Examples: CLAUDE_CONFIG_DIR=~/.claude-work, CLAUDE_CONFIG_DIR=~/.claude-moolabs
      local user_root="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
      if [[ "$scope" == "project" ]]; then echo "./.claude/skills"
      else echo "${user_root}/skills"; fi ;;
    cursor)
      if [[ "$scope" == "project" ]]; then echo "./.cursor/rules"
      else echo "$HOME/.cursor/rules"; fi ;;
    copilot)
      echo "./.github/skills" ;;
    windsurf)
      if [[ "$scope" == "project" ]]; then echo "./.windsurf/rules"
      else echo "$HOME/.codeium/windsurf/rules"; fi ;;
    cline) echo "./.clinerules" ;;
    gemini) echo "$HOME/.gemini/skills" ;;
    kiro) echo "./.kiro/skills" ;;
    trae) echo "./.trae/rules" ;;
    roo) echo "./.roo/rules" ;;
    goose) echo "$HOME/.config/goose/skills" ;;
    opencode) echo "$HOME/.config/opencode/skills" ;;
    universal)
      if [[ "$scope" == "project" ]]; then echo "./.agents/skills"
      else echo "$HOME/.agents/skills"; fi ;;
    *) echo ""; return 1 ;;
  esac
}

if [[ -z "$PLATFORM" ]]; then
  PLATFORM="$(detect_platform)"
fi

if [[ -z "$PLATFORM" ]]; then
  cat >&2 <<EOF
ERROR: Could not auto-detect platform.

Pass one explicitly:
  ./install.sh --platform claude-code
  ./install.sh --platform cursor
  ./install.sh --platform copilot
  ./install.sh --platform windsurf
  ./install.sh --platform cline
  ./install.sh --platform gemini
  ./install.sh --platform kiro
  ./install.sh --platform trae
  ./install.sh --platform roo
  ./install.sh --platform goose
  ./install.sh --platform opencode
  ./install.sh --platform universal

Or install to a generic universal path (works with most agent CLIs):
  ./install.sh --platform universal --user
EOF
  exit 1
fi

DEST_DIR="$(resolve_dest_dir "$PLATFORM" "$SCOPE")"

if [[ -z "$DEST_DIR" ]]; then
  echo "ERROR: Unknown platform: $PLATFORM" >&2
  exit 1
fi

echo "Suite source : $SUITE_SRC_DIR"
echo "Platform     : $PLATFORM"
echo "Scope        : $SCOPE"
echo "Dest dir     : $DEST_DIR"
echo "Skills       : ${#SUITE_SKILLS[@]}"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] would create: $DEST_DIR"
  for skill in "${SUITE_SKILLS[@]}"; do
    echo "[dry-run] would copy:   $SUITE_SRC_DIR/$skill  →  $DEST_DIR/$skill"
  done
  exit 0
fi

if [[ $UNINSTALL -eq 1 ]]; then
  echo "Uninstalling..."
  for skill in "${SUITE_SKILLS[@]}"; do
    target="$DEST_DIR/$skill"
    if [[ -e "$target" ]]; then
      rm -rf "$target"
      echo "  removed $target"
    fi
  done
  echo ""
  echo "Uninstall complete."
  exit 0
fi

mkdir -p "$DEST_DIR"

for skill in "${SUITE_SKILLS[@]}"; do
  src="$SUITE_SRC_DIR/$skill"
  dest="$DEST_DIR/$skill"
  if [[ ! -d "$src" ]]; then
    echo "  SKIP $skill (not found at $src)" >&2
    continue
  fi
  if [[ -e "$dest" ]]; then
    rm -rf "$dest"
  fi
  cp -R "$src" "$dest"
  echo "  installed $skill"
done

echo ""
echo "Install complete."
echo ""
echo "Slash-invocable skills:"
echo "  /cost-billing-discovery           — Skill A: scan repo, produce inventories"
echo "  /cost-billing-cloud-bill          — Skill B: wire AWS / GCP / Azure exports"
echo "  /cost-billing-instrument          — Skill 2: codemod that wires SDK calls"
echo "  /cost-billing-drift-lint          — Skill 3: CI drift detection"
echo "  /cost-billing-adversarial-review  — Skill R: 5-phase quality gate"
echo "  /cost-billing-reconcile           — Skill C: WAPE/Coverage validation"
echo ""
echo "Shared docs (read-only, not slash-invocable):"
echo "  cost-billing-shared/README.md"
echo "  cost-billing-shared/anchor-taxonomy.md"
echo "  cost-billing-shared/sdk-surface-reference.md"
echo "  cost-billing-shared/v1-decisions-log.md"
echo "  cost-billing-shared/three-role-review.md"
echo "  cost-billing-shared/gaps-tracker.md"
echo ""
echo "Open a new agent session and type:"
echo "  /cost-billing-discovery /path/to/customer/repo"
