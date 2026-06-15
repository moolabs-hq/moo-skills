#!/usr/bin/env bash
# Install the Cost+Billing Discovery & Instrumentation suite (7 skills + shared dir).
# Auto-detects the agent platform (Claude Code, Cursor, Copilot, etc.) and asks the
# user which persona they're installing for (finance / product / engineering / all).
# Engineering persona installs CodeGraph and runs ingest on the customer repo.
#
# Usage:
#   ./install.sh                              # interactive: prompts for persona + repo
#   ./install.sh --persona engineering        # skip persona prompt
#   ./install.sh --persona finance --skip-codegraph
#   ./install.sh --persona all --repo /path/to/customer/repo
#   ./install.sh --platform claude-code --user --persona engineering --repo .
#   ./install.sh --project                    # install at project scope
#   ./install.sh --dry-run                    # show what would happen
#   ./install.sh --uninstall                  # remove all skills
#   ./install.sh --no-bootstrap-cta           # don't print the /cost-billing-bootstrap CTA
#   ./install.sh --no-prune                   # don't auto-remove stale cost-billing-* skills
#                                             # (default: prune skills not in the persona's install list,
#                                             # e.g. deprecated cost-billing-bootstrap or cost-billing-reconcile)
#   (engineering/all personas are asked interactively whether to set up the AWS CUR now —
#    installs the moo-cloud-bill CLI and runs its `configure` wizard)
#   ./install.sh --package                    # skip local install; produce .zip bundles
#                                             # uploadable to Claude Desktop / web Projects
#                                             # (Settings → Skills → drag-and-drop). Each .zip
#                                             # is flat-rooted with SKILL.md at the root + any
#                                             # scripts/references/assets folders. cost-billing-
#                                             # shared/chain-handoff.md is bundled INTO each
#                                             # chain-stage zip so the upload is self-contained.
#   ./install.sh --package --persona finance  # only package the finance-stage zip(s)
#   ./install.sh --package-dir <path>         # override default dist/ location
#
#   ./install.sh --list-mcps                  # print catalog of MCPs install.sh knows
#   ./install.sh --mcp outline                # configure ONE MCP (writes to platform's
#                                             # MCP config file; prompts for required env vars)
#   ./install.sh --mcp outline,notion,github  # multiple MCPs (CSV or repeatable --mcp)
#   ./install.sh --mcp outline --mcp notion   # repeatable form
#   ./install.sh --mcp-target claude-desktop  # override which platform's MCP config to write
#                                             # (default: same platform as the skill install)
#   ./install.sh --mcp-config my-mcp.json     # add a CUSTOM MCP from a JSON file
#                                             # (format: see cost-billing-shared/assets/mcp-catalog.json)
#
#   ./install.sh --handoff download           # copies each signed YAML to ~/Downloads
#                                             # + opens it (macOS: open; linux: xdg-open) so
#                                             # the user can attach to email/Slack/etc.
#   ./install.sh --handoff download --download-to ~/Desktop/MoolabsChain
#   ./install.sh --handoff mcp --handoff-mcp google-drive
#                                             # bootstrap uses the named MCP to push docs
#   ./install.sh --handoff shared-folder --shared-folder ~/Drive/MoolabsChain
#                                             # bootstrap writes to a cloud-sync folder
#   ./install.sh --handoff manual             # just print channel instructions (legacy default)
#   ./install.sh --no-handoff-prompt          # skip interactive handoff prompt entirely
#
# Env vars honored:
#   CLAUDE_CONFIG_DIR    Claude Code user-scope root (overrides ~/.claude); installs go to $CLAUDE_CONFIG_DIR/skills/
#
# Personas:
#   finance       — install all skills + scaffold customer-context-template. CFO works mostly via review surface; no extra tooling.
#   product       — install all skills + scaffold customer-context-template. PM works via output-input map editor; no extra tooling.
#   engineering   — install all skills + install CodeGraph + run codegraph ingest on customer repo. Engineer needs deep code-graph access.
#   all           — engineering setup (CodeGraph) + everything else. Pick this for the integrator-machine that runs the whole pipeline.

set -euo pipefail

# Per-persona skill subsets — only install what that persona actually uses.
# cost-billing-shared is required by every persona (loaded by the other skills).
# cost-billing-reconcile is NOT in this suite — it's Moolabs-engineering-internal infrastructure tracked separately.

# Per-persona skill subsets — the bootstrap is SILOED in v0.3.0 (one persona per machine).
# Each persona installs ONLY their stage's bootstrap, plus the adversarial-review skill
# (which fires inside their stage's draft-review phase), plus the shared docs.
# The engineer persona additionally installs discovery / cloud-bill / instrument /
# drift-lint since those run post-chain on the engineer's machine.

SKILLS_FINANCE=(
  cost-billing-bootstrap-finance          # Stage 1: pricing + compliance + tenancy/region
  cost-billing-signoff                    # NEW: state-aware signoff orchestrator (CFO Stage 1 + 2b)
  cost-billing-adversarial-review         # Skill R fires inside Stage 4 of finance bootstrap + every signoff stage
  cost-billing-shared                     # required by every skill
)

# CPO uses persona 'product' (org-level product strategy)
SKILLS_PRODUCT=(
  cost-billing-bootstrap-cpo              # Stage 2: company + product + features + terminology
  cost-billing-adversarial-review
  cost-billing-shared
  # NOTE: CPO does NOT install cost-billing-signoff — the per-product PM signoffs happen on the
  # team-product PMs' machines, not on the CPO machine. CPO's role ends with their bootstrap.
)

# Team-PM (per user's vocabulary "team product engineer") uses persona 'team-product'
SKILLS_TEAM_PRODUCT=(
  cost-billing-bootstrap-team-product     # Stage 3: per-feature unit + event_type + input map (PER PRODUCT)
  cost-billing-signoff                    # NEW: PM Stage 2 + Stage 3b signoffs (per product / per service)
  cost-billing-adversarial-review
  cost-billing-shared
)

# Team-engineer (IC engineer) uses persona 'engineering' — fan out per-service
SKILLS_ENGINEERING=(
  cost-billing-bootstrap-team-engineer    # Stage 4: repo + telemetry + MCP + SDK key (PER SERVICE)
  cost-billing-signoff                    # NEW: Engineer Stage 3 signoff (per service)
  cost-billing-discovery                  # post-chain: produce inventories
  cost-billing-cloud-bill                 # post-chain: wire cloud-bill exports
  cost-billing-instrument                 # post-chain: codemod (--service per engineer)
  cost-billing-drift-lint                 # post-chain: CI drift
  cost-billing-adversarial-review
  cost-billing-shared
)

# 'all' = solo founder / integrator machine running all stages locally.
SKILLS_ALL=(
  cost-billing-bootstrap-finance
  cost-billing-bootstrap-cpo
  cost-billing-bootstrap-team-product
  cost-billing-bootstrap-team-engineer
  cost-billing-signoff                    # NEW
  cost-billing-discovery
  cost-billing-cloud-bill
  cost-billing-instrument
  cost-billing-drift-lint
  cost-billing-adversarial-review
  cost-billing-shared
)
# Note: cost-billing-bootstrap (the v0.1-0.2 single-machine bootstrap) is deprecated
# in v0.3.0; the 4 silo bootstraps replace it. cost-billing-reconcile is not in this
# suite (Moolabs-engineering-internal, tracked separately).
# Note: cost-billing-reconcile was removed from this customer-portable suite.
# It is an engineering-internal Moolabs harness for validating the Moolabs attribution-engine's
# attribution_engine.py against real customer cloud bills; it has no business
# running in a customer environment. Tracked separately as Moolabs internal
# infrastructure — see cost-billing-shared/v1-decisions-log.md.

# Will be set to one of the above arrays after persona is known.
SUITE_SKILLS=()

select_skills_for_persona() {
  case "$PERSONA" in
    finance)        SUITE_SKILLS=("${SKILLS_FINANCE[@]}") ;;
    product|cpo)    SUITE_SKILLS=("${SKILLS_PRODUCT[@]}") ;;   # 'product' is CPO; alias 'cpo'
    team-product|team-pm) SUITE_SKILLS=("${SKILLS_TEAM_PRODUCT[@]}") ;;
    engineering|team-engineer|engineer)  SUITE_SKILLS=("${SKILLS_ENGINEERING[@]}") ;;
    all)            SUITE_SKILLS=("${SKILLS_ALL[@]}") ;;
    *)
      # Uninstall path: remove every possible skill we might have placed.
      # Includes the deprecated v0.1-0.2 cost-billing-bootstrap so uninstall cleans
      # legacy installs too.
      SUITE_SKILLS=(
        cost-billing-bootstrap                  # deprecated v0.1-0.2 single-machine bootstrap
        "${SKILLS_ALL[@]}"
      )
      ;;
  esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE_SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Capture original argv before parsing so we can forward to per-platform
# self-recursion in the multi-target install path below.
_ORIG_ARGS=("$@")

PLATFORM=""
SCOPE="user"
PERSONA=""
REPO=""
DRY_RUN=0
UNINSTALL=0
SKIP_CODEGRAPH=0
SKIP_PLUGINS=0
FORCE_CODEGRAPH_INGEST=0
NO_BOOTSTRAP_CTA=0
NO_PRUNE=0
PACKAGE_MODE=0
PACKAGE_DIR=""
MCP_NAMES=()
MCP_TARGET=""
MCP_CONFIG_PATH=""
LIST_MCPS=0
HANDOFF_MODE=""
HANDOFF_DOWNLOAD_TO=""
HANDOFF_SHARED_FOLDER=""
HANDOFF_MCP_NAME=""
HANDOFF_NO_PROMPT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --user) SCOPE="user"; shift ;;
    --project) SCOPE="project"; shift ;;
    --persona) PERSONA="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --skip-codegraph) SKIP_CODEGRAPH=1; shift ;;
    --skip-plugins) SKIP_PLUGINS=1; shift ;;
    --force-codegraph-ingest) FORCE_CODEGRAPH_INGEST=1; shift ;;
    --no-bootstrap-cta) NO_BOOTSTRAP_CTA=1; shift ;;
    --no-prune) NO_PRUNE=1; shift ;;
    --package) PACKAGE_MODE=1; shift ;;
    --package-dir) PACKAGE_DIR="$2"; shift 2 ;;
    --mcp)
      # Repeatable: --mcp outline --mcp notion. Also supports CSV: --mcp outline,notion
      IFS=',' read -ra _mcp_list <<< "$2"
      for _m in "${_mcp_list[@]}"; do
        MCP_NAMES+=("$_m")
      done
      shift 2 ;;
    --mcp-target) MCP_TARGET="$2"; shift 2 ;;
    --mcp-config) MCP_CONFIG_PATH="$2"; shift 2 ;;
    --list-mcps) LIST_MCPS=1; shift ;;
    --handoff) HANDOFF_MODE="$2"; shift 2 ;;
    --download-to) HANDOFF_DOWNLOAD_TO="$2"; shift 2 ;;
    --shared-folder) HANDOFF_SHARED_FOLDER="$2"; shift 2 ;;
    --handoff-mcp) HANDOFF_MCP_NAME="$2"; shift 2 ;;
    --no-handoff-prompt) HANDOFF_NO_PROMPT=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)
      head -32 "$0" | tail -31
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ──────────────────────────────────────────────────────────────────────
# Run-once coordination across the per-platform fan-out
# ──────────────────────────────────────────────────────────────────────
# The installer re-invokes itself ("$0 --platform X") once per detected platform,
# so a shell variable can't guard "run this once" — each child is its own process.
# A run id is exported by the top-level process and inherited by every child; an
# atomic mkdir marker keyed to it ensures the CUR setup runs exactly once.
if [[ -z "${_MCB_RUN_ID:-}" ]]; then
  export _MCB_RUN_ID="$$"
  _MCB_RUN_OWNER=1            # only the top-level process cleans the marker up
fi
_CUR_SETUP_MARKER="${TMPDIR:-/tmp}/.moo-cloud-bill-cur-setup.${_MCB_RUN_ID}"
if [[ "${_MCB_RUN_OWNER:-0}" == "1" ]]; then
  rm -rf "$_CUR_SETUP_MARKER" 2>/dev/null || true   # clean slate (stale PID reuse)
  trap 'rm -rf "$_CUR_SETUP_MARKER" 2>/dev/null || true' EXIT
fi

# ──────────────────────────────────────────────────────────────────────
# Platform detection
# ──────────────────────────────────────────────────────────────────────

detect_platform() {
  # Back-compat single-platform detector. Returns the first match in the
  # elif chain — used only as a fallback. The default multi-target install
  # path uses detect_all_platforms() instead.
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
  elif [[ -d "$HOME/.codex" ]]; then
    echo "codex"
  elif [[ -d "$HOME/.agents" ]] || [[ -d "./.agents" ]]; then
    echo "universal"
  else
    echo ""
  fi
}

# Enumerates EVERY agent platform present on the machine — not just the first.
# This is the default for `./install.sh` with no --platform: developers commonly
# run multiple agents (Claude Code AND Codex AND Cursor) and expect the suite to
# land in all of them. Output is one platform name per line, deduplicated, in
# precedence order. The main flow recurses self per line.
detect_all_platforms() {
  local found=()
  [[ -n "${CLAUDE_CONFIG_DIR:-}" && -d "$CLAUDE_CONFIG_DIR" ]] && found+=("claude-code")
  { [[ -d "$HOME/.claude" ]] || [[ -d "./.claude" ]]; } && found+=("claude-code")
  { [[ -d "./.cursor" ]] || [[ -d "$HOME/.cursor" ]]; } && found+=("cursor")
  { [[ -d "./.github" ]] && [[ -f "./.github/copilot-instructions.md" ]]; } && found+=("copilot")
  { [[ -d "$HOME/.codeium/windsurf" ]] || [[ -d "./.windsurf" ]]; } && found+=("windsurf")
  [[ -d "./.clinerules" ]] && found+=("cline")
  [[ -d "$HOME/.gemini" ]] && found+=("gemini")
  [[ -d "./.kiro" ]] && found+=("kiro")
  [[ -d "./.trae" ]] && found+=("trae")
  [[ -d "./.roo" ]] && found+=("roo")
  [[ -d "$HOME/.config/goose" ]] && found+=("goose")
  [[ -d "$HOME/.config/opencode" ]] && found+=("opencode")
  [[ -d "$HOME/.codex" ]] && found+=("codex")
  { [[ -d "$HOME/.agents" ]] || [[ -d "./.agents" ]]; } && found+=("universal")
  # Dedupe preserving order (claude-code can appear twice — env var + ~/.claude).
  printf '%s\n' "${found[@]}" | awk 'NF && !seen[$0]++'
}

resolve_dest_dir() {
  local platform="$1" scope="$2"
  case "$platform" in
    claude-code)
      local user_root="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
      if [[ "$scope" == "project" ]]; then echo "./.claude/skills"
      else echo "${user_root}/skills"; fi ;;
    cursor)
      if [[ "$scope" == "project" ]]; then echo "./.cursor/rules"
      else echo "$HOME/.cursor/rules"; fi ;;
    copilot) echo "./.github/skills" ;;
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
    codex) echo "$HOME/.codex/skills" ;;
    universal)
      if [[ "$scope" == "project" ]]; then echo "./.agents/skills"
      else echo "$HOME/.agents/skills"; fi ;;
    *) echo ""; return 1 ;;
  esac
}

# ──────────────────────────────────────────────────────────────────────
# Persona prompt
# ──────────────────────────────────────────────────────────────────────

prompt_persona() {
  if [[ -n "$PERSONA" ]]; then return; fi
  if [[ ! -t 0 ]]; then
    echo "ERROR: --persona required when stdin is not a terminal" >&2
    echo "Pass --persona finance|product|team-product|engineering|all" >&2
    exit 1
  fi
  echo ""
  echo "Which persona are you installing for?"
  echo ""
  echo "Chain order: finance → product (CPO) → team-product (team-PM) → engineering."
  echo "Each persona runs their stage on THEIR machine; the signed doc travels"
  echo "between humans via email / Slack / Drive."
  echo ""
  echo "  1) finance       — Stage 1. CFO. Pricing model, compliance, tenancy/region/env."
  echo "  2) product       — Stage 2. CPO. Company + products + features + terminology."
  echo "  3) team-product  — Stage 3. Team-PM. Per-feature unit + input map + event_type."
  echo "  4) engineering   — Stage 4. IC engineer. Repo + telemetry + MCP + SDK key."
  echo "                    Also installs CodeGraph + downstream skills (discovery,"
  echo "                    cloud-bill, instrument, drift-lint)."
  echo "  5) all           — Solo founder / integrator machine. All 4 stages locally."
  echo ""
  local choice=""
  while [[ -z "$choice" ]]; do
    read -r -p "Choice [1-5]: " choice
    case "$choice" in
      1|finance)               PERSONA="finance" ;;
      2|product|cpo)           PERSONA="product" ;;
      3|team-product|team-pm)  PERSONA="team-product" ;;
      4|engineering|eng|engineer|team-engineer) PERSONA="engineering" ;;
      5|all)                   PERSONA="all" ;;
      *) echo "Invalid; pick 1-5 or finance|product|team-product|engineering|all"; choice="" ;;
    esac
  done
  echo "Persona: $PERSONA"
}

prompt_repo() {
  # Only ask for repo if we'll install CodeGraph
  if [[ "$PERSONA" != "engineering" && "$PERSONA" != "all" ]]; then return; fi
  if [[ $SKIP_CODEGRAPH -eq 1 ]]; then return; fi
  if [[ -n "$REPO" ]]; then resolve_repo_path; return; fi
  if [[ ! -t 0 ]]; then return; fi
  echo ""
  echo "Path to the customer repository for CodeGraph ingest?"
  echo "  (Absolute path strongly preferred — relative paths like '../../' resolve from"
  echo "   THIS install.sh location, which is rarely what you want.)"
  echo "  Leave blank to skip CodeGraph init; you can run 'codegraph init -i' manually later."
  read -r -p "Repo path: " REPO
  resolve_repo_path
}

# Normalize REPO to absolute path + show user the resolved path before any heavy work.
resolve_repo_path() {
  if [[ -z "$REPO" ]]; then return; fi
  REPO="${REPO/#\~/$HOME}"
  if [[ "$REPO" != /* ]]; then
    # Relative — resolve against the user's current working directory
    REPO="$(cd "$REPO" 2>/dev/null && pwd)" || {
      echo "ERROR: --repo $REPO doesn't resolve to a directory." >&2
      REPO=""
      return
    }
  fi
}

# ──────────────────────────────────────────────────────────────────────
# Handoff-config helpers (where signed YAMLs flow between chain personas)
# DEFINED EARLY so they are visible at the call site (prompt_handoff runs in
# the main flow alongside prompt_persona / prompt_repo).
# ──────────────────────────────────────────────────────────────────────

default_download_dir() {
  case "$(uname -s)" in
    Darwin) echo "$HOME/Downloads" ;;
    Linux)
      if [[ -d "$HOME/Downloads" ]]; then echo "$HOME/Downloads"
      else echo "$HOME"; fi ;;
    *) echo "$HOME/Downloads" ;;
  esac
}

prompt_handoff() {
  if [[ -n "$HANDOFF_MODE" ]]; then return; fi
  if [[ $HANDOFF_NO_PROMPT -eq 1 ]]; then return; fi
  if [[ ! -t 0 ]]; then return; fi
  if [[ $UNINSTALL -eq 1 ]]; then return; fi

  echo ""
  echo "How should signed YAMLs flow between chain personas?"
  echo "(The CFO's signed YAML needs to reach the CPO, the CPO's needs to reach"
  echo "the team-PM, and so on.)"
  echo ""
  echo "  1) MCP push       — use an MCP server you'll configure (--mcp ...)"
  echo "                       Best for: teams with Drive/Notion/S3 MCP already running."
  echo "  2) Download       — bootstrap copies each signed YAML to your Downloads folder"
  echo "                       and opens it. You attach to email/Slack manually."
  echo "                       Best for: no cloud-sync, no MCP, want zero friction."
  echo "  3) Shared folder  — write to a cloud-sync folder (Drive/Dropbox/OneDrive)."
  echo "                       Other personas pull from the same folder."
  echo "                       Best for: teams already on Drive without an MCP."
  echo "  4) Manual         — print the channel-list table (current default)."
  echo "                       Best for: customers who pick a channel per-doc."
  echo "  5) Skip           — don't write a handoff config (skill defaults to manual)."
  echo ""
  local choice=""
  while [[ -z "$choice" ]]; do
    read -r -p "Choice [1-5]: " choice
    case "$choice" in
      1|mcp)            HANDOFF_MODE="mcp" ;;
      2|download)       HANDOFF_MODE="download" ;;
      3|shared|shared-folder) HANDOFF_MODE="shared-folder" ;;
      4|manual)         HANDOFF_MODE="manual" ;;
      5|skip)           HANDOFF_MODE="skip" ;;
      *) echo "Invalid; pick 1-5"; choice="" ;;
    esac
  done
  echo "Handoff mode: $HANDOFF_MODE"

  case "$HANDOFF_MODE" in
    download)
      local default_dl; default_dl="$(default_download_dir)"
      read -r -p "Download path [default: $default_dl]: " HANDOFF_DOWNLOAD_TO
      HANDOFF_DOWNLOAD_TO="${HANDOFF_DOWNLOAD_TO:-$default_dl}"
      HANDOFF_DOWNLOAD_TO="${HANDOFF_DOWNLOAD_TO/#\~/$HOME}"
      ;;
    shared-folder)
      read -r -p "Shared folder path (must be cloud-synced — Drive, Dropbox, etc.): " HANDOFF_SHARED_FOLDER
      HANDOFF_SHARED_FOLDER="${HANDOFF_SHARED_FOLDER/#\~/$HOME}"
      [[ -d "$HANDOFF_SHARED_FOLDER" ]] || echo "(Note: $HANDOFF_SHARED_FOLDER doesn't exist yet — bootstrap will create it.)"
      ;;
    mcp)
      if [[ -z "$HANDOFF_MCP_NAME" && ${#MCP_NAMES[@]} -gt 0 ]]; then
        HANDOFF_MCP_NAME="${MCP_NAMES[0]}"
        echo "  (using first configured MCP for handoff: $HANDOFF_MCP_NAME)"
      elif [[ -z "$HANDOFF_MCP_NAME" ]]; then
        echo "  (no --mcp passed; you can set --handoff-mcp <name> later or"
        echo "   pass --mcp <name> in this install so the handoff has a target.)"
        read -r -p "Which MCP should the chain use for handoff?: " HANDOFF_MCP_NAME
      fi
      ;;
  esac
}

write_handoff_config() {
  if [[ -z "$HANDOFF_MODE" || "$HANDOFF_MODE" == "skip" ]]; then return; fi
  if [[ $UNINSTALL -eq 1 ]]; then return; fi

  local cfg_dir="$HOME/.moolabs"
  local cfg_path="$cfg_dir/handoff-config.yaml"
  mkdir -p "$cfg_dir"

  case "$HANDOFF_MODE" in
    download)
      [[ -z "$HANDOFF_DOWNLOAD_TO" ]] && HANDOFF_DOWNLOAD_TO="$(default_download_dir)"
      cat > "$cfg_path" <<EOF
# Cost+Billing chain handoff configuration — generated by install.sh
\$schema: https://moolabs.com/schemas/cost-billing-handoff/0.1.0
mode: download
download_to: $HANDOFF_DOWNLOAD_TO
open_after_write: true
notes: |
  Each chain stage will:
    1. Write .moolabs/chain/<NN>-<stage>.signed.yaml (local source of truth)
    2. ALSO copy to $HANDOFF_DOWNLOAD_TO/<NN>-<stage>.signed.yaml
    3. Open with OS default app (macOS: open, Linux: xdg-open) so the user
       can attach to email/Slack/whatever.
  Switch modes by re-running install.sh --handoff <mode>.
EOF
      ;;
    shared-folder)
      mkdir -p "$HANDOFF_SHARED_FOLDER" 2>/dev/null || true
      cat > "$cfg_path" <<EOF
\$schema: https://moolabs.com/schemas/cost-billing-handoff/0.1.0
mode: shared-folder
shared_folder: $HANDOFF_SHARED_FOLDER
notes: |
  Bootstrap stages write signed YAMLs to this folder. Cloud-synced (Drive /
  Dropbox / OneDrive) is recommended so next personas see the file auto-sync.
EOF
      ;;
    mcp)
      cat > "$cfg_path" <<EOF
\$schema: https://moolabs.com/schemas/cost-billing-handoff/0.1.0
mode: mcp
mcp_name: ${HANDOFF_MCP_NAME:-unknown}
notes: |
  Bootstrap stages invoke the named MCP server to push signed YAMLs.
  Required: the MCP must be configured (./install.sh --mcp $HANDOFF_MCP_NAME).
EOF
      ;;
    manual)
      cat > "$cfg_path" <<EOF
\$schema: https://moolabs.com/schemas/cost-billing-handoff/0.1.0
mode: manual
notes: |
  Bootstrap stages just print the channel-list table at handoff time;
  user picks email/Slack/Drive/etc. per signed YAML.
EOF
      ;;
  esac

  echo ""
  echo "─── Handoff config written ─────────────────────────────────────────"
  echo "  $cfg_path"
  echo "  mode: $HANDOFF_MODE"
  case "$HANDOFF_MODE" in
    download)      echo "  download_to: $HANDOFF_DOWNLOAD_TO" ;;
    shared-folder) echo "  shared_folder: $HANDOFF_SHARED_FOLDER" ;;
    mcp)           echo "  mcp_name: $HANDOFF_MCP_NAME" ;;
  esac
  echo "  → Each chain stage's Phase 6 reads this file to decide handoff behavior."
  echo "  → Switch modes later: ./install.sh --handoff <mode>"
  echo "─────────────────────────────────────────────────────────────────────"
}

# ──────────────────────────────────────────────────────────────────────
# CodeGraph install + ingest
# ──────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────
# ralph-skills + moo-skills/superpowers (Claude Code plugins)
# ──────────────────────────────────────────────────────────────────────
#
# Why these two:
#   • ralph-skills (ralph-marketplace)
#       Autonomous-loop framework. /cost-billing-instrument Phase 2d
#       dispatches per-file subagent tasks; ralph-loop is the queue
#       runner that makes that ergonomic to invoke.
#   • moo-skills / superpowers
#       Hosts /superpowers:requesting-code-review and
#       /cost-billing-adversarial-review's parent skill. The cost-billing
#       skills reference these by name; without them, the Phase 4
#       adversarial-review handoff is a dangling cross-skill call.
#
# install.sh CANNOT register a Claude Code plugin programmatically — the
# plugin system is interactive. What this function CAN do:
#   1. Detect whether the plugins are present in any known cache location.
#   2. Print a copy-pasteable CTA with the exact /plugin commands.
#   3. Optionally append a marketplaces entry to ~/.claude/settings.json
#      (gated behind --auto-register-plugins, default OFF).
#
install_ralph_and_superpowers() {
  echo ""
  echo "─── ralph-skills + superpowers plugin check ─────────────────────────"

  local cache_dirs=(
    "$HOME/.claude/plugins/cache"
    "$HOME/.claude-moolabs/plugins/cache"
    "${CLAUDE_CONFIG_DIR:-}/plugins/cache"
  )

  local ralph_found=0
  local superpowers_found=0

  for cd in "${cache_dirs[@]}"; do
    [[ -z "$cd" || ! -d "$cd" ]] && continue
    if [[ -d "$cd/ralph-marketplace/ralph-skills" ]]; then
      ralph_found=1
      echo "  ✓ ralph-skills detected at $cd/ralph-marketplace/ralph-skills"
    fi
    if [[ -d "$cd/moo-skills" || -d "$cd/claude-plugins-official/superpowers" ]]; then
      superpowers_found=1
      echo "  ✓ superpowers detected at $cd"
    fi
  done

  if [[ $ralph_found -eq 1 && $superpowers_found -eq 1 ]]; then
    echo "Both plugins already installed — nothing to do."
    echo "─────────────────────────────────────────────────────────────────────"
    return 0
  fi

  echo ""
  echo "REQUIRED PLUGIN DEPENDENCIES — install via Claude Code's /plugin command:"
  echo ""
  if [[ $ralph_found -eq 0 ]]; then
    echo "  1. ralph-skills (autonomous task-fanout loop)"
    echo "     /plugin marketplace add https://github.com/ralph-marketplace/ralph-marketplace"
    echo "     /plugin install ralph-skills@ralph-marketplace"
    echo ""
  fi
  if [[ $superpowers_found -eq 0 ]]; then
    echo "  2. superpowers (adversarial-pr-review, requesting-code-review)"
    echo "     /plugin marketplace add https://github.com/moolabs-hq/moo-skills"
    echo "     /plugin install superpowers@moo-skills"
    echo ""
  fi
  echo "  Paste the commands above into Claude Code. Without these, the"
  echo "  Phase 2d task-dispatch and Phase 4 adversarial-review handoff will"
  echo "  fail silently when cost-billing skills cross-call them."
  echo ""
  echo "  Skip this check next time with --skip-plugins."
  echo "─────────────────────────────────────────────────────────────────────"
}

install_codegraph() {
  echo ""
  echo "─── CodeGraph (engineering persona) ─────────────────────────────────"
  echo "Package: @colbymchenry/codegraph (https://github.com/colbymchenry/codegraph)"

  local installed_version=""
  local latest_version=""

  if command -v codegraph >/dev/null 2>&1; then
    installed_version="$(codegraph --version 2>/dev/null | head -1 | awk '{print $NF}')"
    echo "Detected: codegraph v${installed_version} at $(command -v codegraph)"
  else
    echo "Detected: codegraph NOT on PATH."
  fi

  # Look up the latest on npm (best-effort)
  if command -v npm >/dev/null 2>&1; then
    latest_version="$(npm view @colbymchenry/codegraph version 2>/dev/null | tail -1)"
    if [[ -n "$latest_version" ]]; then
      echo "Latest on npm: v${latest_version}"
    fi
  fi

  if [[ -z "$installed_version" ]]; then
    # Fresh install
    if ! command -v npm >/dev/null 2>&1; then
      echo "WARNING: npm not found. Install Node.js (https://nodejs.org), then:" >&2
      echo "  npm install -g @colbymchenry/codegraph" >&2
      echo "Continuing skill install without CodeGraph." >&2
      return 0
    fi
    echo "Installing @colbymchenry/codegraph@latest..."
    if npm install -g @colbymchenry/codegraph@latest 2>&1 | tail -8; then
      installed_version="$(codegraph --version 2>/dev/null | head -1 | awk '{print $NF}')"
      echo "Installed: codegraph v${installed_version}"
    else
      echo "WARNING: 'npm install -g @colbymchenry/codegraph' failed." >&2
      echo "(May need sudo or a different npm prefix; install manually then re-run.)" >&2
      return 0
    fi
  elif [[ -n "$latest_version" && "$installed_version" != "$latest_version" ]]; then
    # Upgrade
    echo "Outdated: v${installed_version} → latest v${latest_version}. Upgrading..."
    if npm install -g @colbymchenry/codegraph@latest 2>&1 | tail -8; then
      installed_version="$(codegraph --version 2>/dev/null | head -1 | awk '{print $NF}')"
      echo "Upgraded to: codegraph v${installed_version}"
    else
      echo "WARNING: upgrade failed. Continuing with installed v${installed_version}."
    fi
  else
    echo "Up to date — no action needed."
  fi

  # Ingest — only with safety checks
  if [[ -z "$REPO" ]]; then
    echo "No --repo provided; skipping codegraph ingest."
    echo "Run manually later: cd <customer-repo> && codegraph init -i"
    echo "─────────────────────────────────────────────────────────────────────"
    return 0
  fi

  if [[ ! -d "$REPO" ]]; then
    echo "WARNING: --repo $REPO does not exist; skipping codegraph init"
    echo "─────────────────────────────────────────────────────────────────────"
    return 0
  fi

  # Resolved-absolute path (resolve_repo_path runs earlier in main flow)
  echo "Target: $REPO"

  if [[ -d "$REPO/.codegraph" ]]; then
    echo "CodeGraph already initialized at $REPO/.codegraph (skipping init -i)"
    echo "─────────────────────────────────────────────────────────────────────"
    return 0
  fi

  # SAFETY CHECK — count files before running init -i
  # Previous OOM crash: a 205k-file parent workspace crashed Node at heap limit.
  # Warn at 10k files, hard-block at 50k unless --force-codegraph-ingest passed.
  echo "Counting files in $REPO (excludes .git, node_modules, .venv)..."
  local file_count
  file_count=$(find "$REPO" \
    \( -name .git -o -name node_modules -o -name .venv -o -name venv -o -name dist -o -name build -o -name target -o -name __pycache__ \) -prune -o \
    -type f -print 2>/dev/null | wc -l | tr -d ' ')
  echo "File count (after excludes): $file_count"

  if [[ "$file_count" -gt 50000 ]]; then
    cat >&2 <<EOF

WARNING: $REPO contains $file_count files after excludes.
CodeGraph init has been observed to OOM on ~200k-file workspaces
(Node.js v8 heap limit hit at 93% progress).

Likely causes:
  - $REPO is a parent workspace containing multiple repos.
  - $REPO contains a vendored dependency tree not caught by excludes.

Recommendations:
  - Re-run with a SMALLER --repo (a single service/repo, not a workspace).
  - Verify your intended target with: ls -la $REPO

Aborting codegraph init. Skills are still installed and usable.
Re-run with --force-codegraph-ingest to override this safety check.
EOF
    echo "─────────────────────────────────────────────────────────────────────"
    return 0
  fi

  if [[ "$file_count" -gt 10000 ]]; then
    echo "NOTE: $file_count files is large but under the 50k safety threshold."
    echo "Init may take 5-15 minutes and consume significant RAM."
    if [[ -t 0 ]] && [[ $FORCE_CODEGRAPH_INGEST -eq 0 ]]; then
      local confirm=""
      read -r -p "Proceed? [y/N]: " confirm
      if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Skipped codegraph init at user request."
        echo "Run manually later: cd $REPO && codegraph init -i"
        echo "─────────────────────────────────────────────────────────────────────"
        return 0
      fi
    fi
  fi

  echo ""
  echo "Running: codegraph init -i (in $REPO)"
  echo "(builds the semantic knowledge graph; /cost-billing-discovery + drift-lint use it)"
  echo ""
  ( cd "$REPO" && codegraph init -i ) || {
    echo "WARNING: 'codegraph init -i' failed in $REPO; you can re-run manually." >&2
    echo "If this was an OOM: $REPO is probably too big. Try a more focused subdirectory." >&2
  }
  echo "─────────────────────────────────────────────────────────────────────"
}

# ──────────────────────────────────────────────────────────────────────
# Customer-context scaffold
# ──────────────────────────────────────────────────────────────────────

scaffold_customer_context() {
  if [[ -z "$REPO" || ! -d "$REPO" ]]; then return; fi
  local ctx="$REPO/.moolabs/customer-context"
  if [[ -d "$ctx" ]]; then
    echo "customer-context/ already exists at $ctx (leaving as-is)"
    return
  fi
  echo ""
  echo "Scaffolding customer-context/ at $ctx ..."
  mkdir -p "$ctx"
  local tpl="$SUITE_SRC_DIR/bootstrap/assets/customer-context-templates"
  if [[ -d "$tpl" ]]; then
    cp "$tpl/product-summary.template.md"        "$ctx/product-summary.template.md" 2>/dev/null || true
    cp "$tpl/pricing-model.template.yaml"        "$ctx/pricing-model.template.yaml" 2>/dev/null || true
    cp "$tpl/repo-info.template.yaml"            "$ctx/repo-info.template.yaml" 2>/dev/null || true
    cp "$tpl/telemetry-stack.template.yaml"      "$ctx/telemetry-stack.template.yaml" 2>/dev/null || true
    cp "$tpl/terminology.template.yaml"          "$ctx/terminology.template.yaml" 2>/dev/null || true
    cp "$tpl/mcp-config.template.yaml"           "$ctx/mcp-config.template.yaml" 2>/dev/null || true
    cp "$tpl/integration-config.template.yaml"   "$ctx/integration-config.template.yaml" 2>/dev/null || true
  fi
  cat > "$ctx/README.md" <<EOF
# customer-context/ — generated by /cost-billing-bootstrap

This directory holds the customer-specific reference files every skill in the
Cost+Billing suite reads before running. It is currently SCAFFOLDED with
.template.* files but NOT YET POPULATED.

Run \`/cost-billing-bootstrap\` from the agent surface (Claude Code, Cursor,
Gemini CLI, etc.) to fill these in by answering 5 questions about your product.

Files (after bootstrap):

  product-summary.md       — 200-400 line synthesis of your product docs
  pricing-model.yaml       — billable units + prices + fair-usage thresholds
  repo-info.yaml           — services, languages, frameworks, existing instrumentation
  telemetry-stack.yaml     — primary tracer (OTel/Datadog/Sentry/none) + brownfield-vs-greenfield
  terminology.yaml         — your words for things (e.g. "generation" not "completion")
  bootstrap-log.yaml       — when bootstrap last ran, which LLM, what source artifacts

Persona at install: $PERSONA
Generated by: install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  echo "  scaffolded: $ctx/README.md + 5 .template.* files"
}

# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

if [[ -z "$PLATFORM" ]]; then
  # No --platform passed: auto-detect ALL agent platforms on this machine and
  # install to each. Developers typically run multiple agents (Claude Code +
  # Codex + Cursor) and expect the suite to land in all of them. To install
  # to just one, pass --platform NAME explicitly.
  declare -a _DETECTED=()
  while IFS= read -r _p; do
    [[ -n "$_p" ]] && _DETECTED+=("$_p")
  done < <(detect_all_platforms)

  if [[ ${#_DETECTED[@]} -eq 0 ]]; then
    cat >&2 <<'EOF'
ERROR: Could not auto-detect any agent platform.

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
  ./install.sh --platform codex
  ./install.sh --platform universal
EOF
    exit 1
  fi

  if [[ ${#_DETECTED[@]} -gt 1 ]]; then
    echo ""
    echo "Detected ${#_DETECTED[@]} agent platforms on this machine: ${_DETECTED[*]}"
    echo "Installing to each (override with --platform NAME for a single target)."

    # Run prompts ONCE at the top so per-platform recursions inherit the answers
    # via explicit flags instead of re-prompting per platform.
    if [[ $UNINSTALL -eq 0 ]]; then
      if [[ $PACKAGE_MODE -eq 1 && -z "$PERSONA" ]]; then PERSONA="all"; fi
      prompt_persona
      prompt_repo
      if [[ "$PERSONA" == "finance" || "$PERSONA" == "product" || "$PERSONA" == "cpo" || "$PERSONA" == "team-product" || "$PERSONA" == "all" ]]; then
        prompt_handoff
      fi
    fi

    _arg_in() { local n="$1"; shift; for a in "$@"; do [[ "$a" == "$n" ]] && return 0; done; return 1; }
    # Bash 3.2 (macOS default) + `set -u` treats "${arr[@]}" on an empty array
    # as an unbound-variable error. Use "${arr[@]+"${arr[@]}"}" everywhere:
    # expands to the array contents iff non-empty, else to nothing.
    declare -a _FWD=("${_ORIG_ARGS[@]+"${_ORIG_ARGS[@]}"}")
    # Inject prompt-captured values when not already present in original args
    # so children skip the corresponding prompt.
    if [[ -n "$PERSONA" ]] && ! _arg_in --persona "${_ORIG_ARGS[@]+"${_ORIG_ARGS[@]}"}"; then
      _FWD+=("--persona" "$PERSONA")
    fi
    if [[ -n "$REPO" ]] && ! _arg_in --repo "${_ORIG_ARGS[@]+"${_ORIG_ARGS[@]}"}"; then
      _FWD+=("--repo" "$REPO")
    fi
    # Handoff was prompted once above (writes shared artifacts independent of
    # install dest); children should not re-prompt.
    if ! _arg_in --no-handoff-prompt "${_ORIG_ARGS[@]+"${_ORIG_ARGS[@]}"}"; then
      _FWD+=("--no-handoff-prompt")
    fi
    # Codegraph init is per-repo, not per-platform: run it on the first child
    # only. Forward --skip-codegraph to every child *after* the first.
    declare -a _FAILED=()
    local_idx=0
    for plat in "${_DETECTED[@]}"; do
      echo ""
      echo "═══════════════════════════════════════════════════════════"
      echo "  Target platform: $plat"
      echo "═══════════════════════════════════════════════════════════"
      declare -a _PLAT_FWD=("${_FWD[@]+"${_FWD[@]}"}")
      if [[ $local_idx -gt 0 ]] && ! _arg_in --skip-codegraph "${_ORIG_ARGS[@]+"${_ORIG_ARGS[@]}"}"; then
        _PLAT_FWD+=("--skip-codegraph")
      fi
      if ! "$0" --platform "$plat" "${_PLAT_FWD[@]+"${_PLAT_FWD[@]}"}"; then
        _FAILED+=("$plat")
      fi
      local_idx=$((local_idx + 1))
    done

    echo ""
    echo "═══════════════════════════════════════════════════════════"
    if [[ ${#_FAILED[@]} -eq 0 ]]; then
      echo "  Done. Installed to ${#_DETECTED[@]} platform(s): ${_DETECTED[*]}"
    else
      echo "  Installed to ${#_DETECTED[@]} platform(s); ${#_FAILED[@]} failed: ${_FAILED[*]}"
      exit 1
    fi
    echo "═══════════════════════════════════════════════════════════"
    exit 0
  fi

  # Single platform detected — fall through to existing single-target flow.
  PLATFORM="${_DETECTED[0]}"
fi

DEST_DIR="$(resolve_dest_dir "$PLATFORM" "$SCOPE")"
if [[ -z "$DEST_DIR" ]]; then
  echo "ERROR: Unknown platform: $PLATFORM" >&2
  exit 1
fi

if [[ $UNINSTALL -eq 0 ]]; then
  # For --package, default to 'all' if no persona specified (typical: package everything for distribution).
  if [[ $PACKAGE_MODE -eq 1 && -z "$PERSONA" ]]; then
    PERSONA="all"
    echo "Note: --package defaulting to --persona all (override with --persona <name>)"
  fi
  prompt_persona
  prompt_repo
  # Only personas that PRODUCE handoff docs need the prompt — finance / cpo /
  # team-product / all. Engineer is last in the chain; doesn't hand off further.
  if [[ "$PERSONA" == "finance" || "$PERSONA" == "product" || "$PERSONA" == "cpo" || "$PERSONA" == "team-product" || "$PERSONA" == "all" ]]; then
    prompt_handoff
  fi
fi

# Resolve which skills to install (or remove) based on persona.
select_skills_for_persona

echo ""
echo "Suite source : $SUITE_SRC_DIR"
echo "Platform     : $PLATFORM"
echo "Scope        : $SCOPE"
echo "Dest dir     : $DEST_DIR"
echo "Persona      : ${PERSONA:-N/A (uninstall removes all)}"
echo "Customer repo: ${REPO:-N/A}"
echo "Skills       : ${#SUITE_SKILLS[@]} (${SUITE_SKILLS[*]})"
echo ""

# ──────────────────────────────────────────────────────────────────────
# --package mode: produce .zip bundles + drag-and-drop instructions.
# Skips local install entirely; just emits artifacts in $PACKAGE_DIR.
# ──────────────────────────────────────────────────────────────────────

package_skills() {
  if ! command -v zip >/dev/null 2>&1; then
    echo "ERROR: 'zip' not on PATH. Install with: brew install zip / apt install zip" >&2
    exit 1
  fi

  local dist="${PACKAGE_DIR:-${SUITE_SRC_DIR}/../dist/cost-billing-skills}"
  dist="$(mkdir -p "$dist" && cd "$dist" && pwd)"     # normalize absolute path
  rm -f "$dist"/*.zip
  rm -rf "$dist"/_staging
  mkdir -p "$dist/_staging"

  echo ""
  echo "─── Packaging skills for Claude Desktop / web upload ──────────────"
  echo "Source : $SUITE_SRC_DIR"
  echo "Dist   : $dist"
  echo "Persona: $PERSONA"
  echo ""

  local shared_handoff="$SUITE_SRC_DIR/shared/chain-handoff.md"
  local shared_principles="$SUITE_SRC_DIR/shared/operating-principles.md"
  local pkg_count=0

  for skill in "${SUITE_SKILLS[@]}"; do
    # cost-billing-shared isn't a slash-invocable skill — its contents get bundled
    # INTO each chain-stage zip below as references/chain-handoff.md.
    if [[ "$skill" == "cost-billing-shared" ]]; then continue; fi

    local src="$SUITE_SRC_DIR/${skill#cost-billing-}"
    if [[ ! -d "$src" ]]; then
      echo "  SKIP $skill (not found)" >&2
      continue
    fi
    if [[ ! -f "$src/SKILL.md" ]]; then
      echo "  SKIP $skill (no SKILL.md)" >&2
      continue
    fi

    # Stage the skill in a temp dir so we can bundle shared docs alongside it.
    local stage="$dist/_staging/$skill"
    rm -rf "$stage"
    mkdir -p "$stage"
    cp -R "$src"/. "$stage"/

    # Bundle shared docs into every skill's references/ so the upload is self-contained
    # (cross-skill `../cost-billing-shared/*.md` references would break in the cloud sandbox).
    mkdir -p "$stage/references"
    if [[ -f "$shared_principles" ]]; then
      cp "$shared_principles" "$stage/references/operating-principles.md"
    fi
    # chain-handoff.md only into the 4 chain-stage skills (the others don't reference it).
    case "$skill" in
      cost-billing-bootstrap-finance|cost-billing-bootstrap-cpo|cost-billing-bootstrap-team-product|cost-billing-bootstrap-team-engineer)
        if [[ -f "$shared_handoff" ]]; then
          cp "$shared_handoff" "$stage/references/chain-handoff.md"
        fi
        ;;
    esac

    # Zip with FLAT root (no top-level dir wrapper) — Claude's upload looks for SKILL.md
    # at the root of the archive.
    local zip_path="$dist/${skill}.zip"
    ( cd "$stage" && zip -rq "$zip_path" . \
        -x ".DS_Store" -x "*/.DS_Store" \
        -x "__pycache__/*" -x "*/__pycache__/*" \
        -x ".git/*" -x "*/.git/*" ) || {
      echo "  FAILED to zip $skill" >&2
      continue
    }

    local size; size=$(du -h "$zip_path" | awk '{print $1}')
    local entries; entries=$(unzip -l "$zip_path" 2>/dev/null | tail -1 | awk '{print $2}')
    printf "  packaged: %-50s  %s  (%s files)\n" "$(basename "$zip_path")" "$size" "$entries"
    pkg_count=$((pkg_count + 1))
  done

  rm -rf "$dist/_staging"

  echo ""
  echo "✓ Packaged $pkg_count skills."
  echo ""

  print_upload_instructions "$dist"
}

print_upload_instructions() {
  local dist="$1"
  cat <<EOF
═══════════════════════════════════════════════════════════════════
 UPLOAD INSTRUCTIONS — Claude Desktop / claude.ai
═══════════════════════════════════════════════════════════════════

Each persona gets their OWN Claude Project. Within that project,
the relevant chain-stage skill is uploaded. The signed YAML between
stages is downloaded from one project and uploaded to the next as a
project file attachment (this is the "email/Slack/Drive handoff"
expressed in the Claude Projects UX).

  ┌──────────────────── Finance / CFO ────────────────────────┐
  │ Project: "Cost Billing — Finance"                          │
  │   Settings → Skills → Upload skill                         │
  │   Drop:  ${dist}/                                          │
  │           cost-billing-bootstrap-finance.zip                │
  │           cost-billing-adversarial-review.zip               │
  │                                                             │
  │   Run inside the project:                                   │
  │     /cost-billing-bootstrap-finance                         │
  │                                                             │
  │   Output:  01-finance.signed.yaml (in project files)        │
  │   Hand off: download + send to CPO via Slack/email/Drive    │
  └─────────────────────────────────────────────────────────────┘

  ┌──────────────────── CPO ──────────────────────────────────┐
  │ Project: "Cost Billing — CPO"                              │
  │   Upload: cost-billing-bootstrap-cpo.zip                   │
  │           cost-billing-adversarial-review.zip               │
  │   Upload as file: 01-finance.signed.yaml (from CFO)        │
  │                                                             │
  │   Run:  /cost-billing-bootstrap-cpo                         │
  │         --input-from 01-finance.signed.yaml                 │
  │                                                             │
  │   Output:  02-cpo.signed.yaml                               │
  └─────────────────────────────────────────────────────────────┘

  ┌──────────────────── Team Product PM ──────────────────────┐
  │ Project: "Cost Billing — Team Product"                     │
  │   Upload: cost-billing-bootstrap-team-product.zip          │
  │           cost-billing-adversarial-review.zip               │
  │   Upload as files: 01-finance + 02-cpo signed YAMLs        │
  │                                                             │
  │   Run:  /cost-billing-bootstrap-team-product                │
  │         --input-from 01-finance.signed.yaml                 │
  │         --input-from 02-cpo.signed.yaml                     │
  │                                                             │
  │   Output:  03-team-product.signed.yaml                      │
  └─────────────────────────────────────────────────────────────┘

  ┌──────────────────── IC Engineer ──────────────────────────┐
  │ NOTE: engineer's downstream needs LOCAL filesystem +       │
  │ codegraph + repo access. Claude Projects (cloud) cannot    │
  │ provide that. Run the engineer stage + downstream skills   │
  │ on Claude Code CLI, Cursor, Codex CLI, etc.                │
  │                                                             │
  │ Local install:                                              │
  │   ./install.sh --persona engineering --repo /path/to/repo   │
  └─────────────────────────────────────────────────────────────┘

WHERE TO UPLOAD (Claude Desktop or claude.ai web):
  1. Open the Project
  2. Settings (gear icon) → "Skills" section
  3. "Upload skill" button → drag-and-drop the .zip
  4. Skill appears in the project's slash-command menu

REQUIREMENTS Anthropic enforces on each upload:
  - .md file must contain skill name + description as YAML frontmatter ✓
  - .zip/.skill must contain SKILL.md at the root ✓
  Both are satisfied by these bundles.

CHATGPT DESKTOP:
  ChatGPT Desktop does not (as of this writing) support the same skill-
  upload UX. For ChatGPT, use the Local MCP server path (see
  cost-billing-shared/desktop-app-guide.md) instead of these zips.
═══════════════════════════════════════════════════════════════════
EOF
}

# Dispatch BEFORE the local-install path so --package short-circuits early.
if [[ $PACKAGE_MODE -eq 1 ]]; then
  package_skills
  exit 0
fi

# ──────────────────────────────────────────────────────────────────────
# MCP configuration helpers
# ──────────────────────────────────────────────────────────────────────

mcp_catalog_path() {
  echo "$SUITE_SRC_DIR/shared/assets/mcp-catalog.json"
}

# Print the curated catalog of MCPs.
list_mcps() {
  local catalog
  catalog="$(mcp_catalog_path)"
  if [[ ! -f "$catalog" ]]; then
    echo "ERROR: mcp-catalog.json not found at $catalog" >&2
    exit 1
  fi
  echo ""
  echo "Curated MCP catalog (use with --mcp <name>):"
  echo ""
  python3 - "$catalog" <<'PYEOF'
import json, sys, textwrap
catalog = json.load(open(sys.argv[1]))
for name, mcp in sorted(catalog["mcps"].items()):
    print(f"  {name}")
    print(f"    {mcp['description']}")
    print(f"    suite_uses: {', '.join(mcp['suite_uses'])}")
    env = mcp.get("env_vars", [])
    if env:
        secrets = [v["name"] for v in env if v.get("secret")]
        configs = [v["name"] for v in env if not v.get("secret")]
        if secrets:
            print(f"    secrets:    {', '.join(secrets)}")
        if configs:
            print(f"    config:     {', '.join(configs)}")
    print()

print("Platforms install.sh can write MCP config to:")
for plat in catalog["platform_mcp_paths"]:
    print(f"  {plat}")
print("")
print("Custom MCP not in catalog?  Use --mcp-config <path-to-json>.")
print("JSON format: see cost-billing-shared/assets/mcp-catalog.json structure.")
PYEOF
}

# Resolve target platform for the MCP write — defaults to the install platform.
resolve_mcp_target() {
  if [[ -n "$MCP_TARGET" ]]; then echo "$MCP_TARGET"; return; fi
  echo "$PLATFORM"
}

# Resolve the MCP config file path for a given target platform.
resolve_mcp_config_file() {
  local target="$1"
  case "$target" in
    claude-code)    echo "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/mcp.json" ;;
    claude-desktop)
      case "$(uname -s)" in
        Darwin)  echo "$HOME/Library/Application Support/Claude/claude_desktop_config.json" ;;
        Linux)   echo "$HOME/.config/Claude/claude_desktop_config.json" ;;
        *)       echo "$HOME/.config/Claude/claude_desktop_config.json" ;;
      esac ;;
    cursor)         echo "$HOME/.cursor/mcp.json" ;;
    codex)          echo "$HOME/.codex/mcp.json" ;;
    windsurf)       echo "$HOME/.codeium/windsurf/mcp_config.json" ;;
    cline)          echo "./.cline/mcp.json" ;;
    goose)          echo "$HOME/.config/goose/mcp.json" ;;
    chatgpt-desktop) echo "" ;;  # UI-only — install.sh prints snippet instead
    *)              echo "" ;;
  esac
}

# Write one MCP entry to the target platform's config file.
# Reads from catalog (or --mcp-config custom JSON), prompts for required env vars,
# writes a placeholder ${VAR_NAME} into the config (NOT the actual secret value).
configure_one_mcp() {
  local mcp_name="$1"
  local catalog="$(mcp_catalog_path)"
  local target="$(resolve_mcp_target)"
  local config_file="$(resolve_mcp_config_file "$target")"

  echo ""
  echo "─── Configuring MCP '$mcp_name' for $target ─────────────────────────"

  # Verify the MCP exists in the catalog.
  if ! python3 -c "
import json, sys
c = json.load(open('$catalog'))
sys.exit(0 if '$mcp_name' in c['mcps'] else 1)
" 2>/dev/null; then
    echo "ERROR: MCP '$mcp_name' not in catalog." >&2
    echo "Run './install.sh --list-mcps' to see the catalog." >&2
    echo "For custom MCPs, use './install.sh --mcp-config <path>'." >&2
    return 1
  fi

  # Special case: chatgpt-desktop has no config file (UI-only)
  if [[ "$target" == "chatgpt-desktop" ]]; then
    echo "ChatGPT Desktop configures MCPs via UI (no file). Copy this snippet:"
    python3 - "$catalog" "$mcp_name" <<'PYEOF'
import json, sys
c = json.load(open(sys.argv[1]))
mcp = c["mcps"][sys.argv[2]]
snippet = {
    "name": sys.argv[2],
    "command": mcp["command"],
    "args": mcp["args"],
    "env": {v["name"]: f"${{{v['name']}}}" for v in mcp.get("env_vars", [])}
}
print(json.dumps(snippet, indent=2))
PYEOF
    echo ""
    echo "ChatGPT Desktop → Settings → MCP servers → Add new server → paste."
    echo "─────────────────────────────────────────────────────────────────────"
    return 0
  fi

  if [[ -z "$config_file" ]]; then
    echo "ERROR: no MCP config path known for target '$target'." >&2
    return 1
  fi

  # Make sure the parent dir exists.
  mkdir -p "$(dirname "$config_file")"

  # Bootstrap the config file if missing.
  if [[ ! -f "$config_file" ]]; then
    echo '{"mcpServers": {}}' > "$config_file"
  fi

  # Merge the MCP entry into the config file (preserves existing mcpServers entries).
  python3 - "$catalog" "$mcp_name" "$config_file" <<'PYEOF'
import json, sys, os, pathlib

catalog_path, mcp_name, config_path = sys.argv[1], sys.argv[2], sys.argv[3]
catalog = json.load(open(catalog_path))
mcp = catalog["mcps"][mcp_name]

# Load existing config; tolerate empty/invalid.
try:
    config = json.load(open(config_path))
except Exception:
    config = {}
if not isinstance(config, dict):
    config = {}
config.setdefault("mcpServers", {})

# Build the MCP entry. Env values are PLACEHOLDERS ${VAR_NAME} — install.sh does
# NOT prompt for or store secret values. The user sets the env vars in their
# shell profile before launching the agent.
entry = {
    "command": mcp["command"],
    "args": mcp["args"],
}
env_block = {v["name"]: f"${{{v['name']}}}" for v in mcp.get("env_vars", [])}
if env_block:
    entry["env"] = env_block

config["mcpServers"][mcp_name] = entry

# Write back, preserving any other top-level keys (some platforms use them).
pathlib.Path(config_path).write_text(json.dumps(config, indent=2) + "\n")
print(f"Wrote MCP entry '{mcp_name}' to {config_path}")

# Print required env vars for the user.
required = [v for v in mcp.get("env_vars", []) if v.get("required")]
if required:
    print("")
    print("REQUIRED — set these env vars BEFORE next agent start:")
    for v in required:
        if v.get("secret"):
            print(f"  export {v['name']}=<your_value>           # {v['prompt']}")
        else:
            print(f"  export {v['name']}=<value>               # {v['prompt']}")
    print("")
    print("Recommended: store the secrets in 1Password / Vault / Doppler and")
    print("reference them with `op read`, `vault read`, etc. — don't paste raw")
    print("secrets into your shell history.")

skills_using = mcp.get("used_by_skills", [])
if skills_using:
    print("")
    print(f"This MCP will be used by these suite skills (per mcp-config.yaml):")
    for s in skills_using:
        print(f"  /{s}")
PYEOF

  echo "─────────────────────────────────────────────────────────────────────"
}

# Write a CUSTOM MCP from a user-provided JSON file. The JSON must have the same
# shape as one entry in mcp-catalog.json["mcps"]["<name>"].
configure_custom_mcp() {
  local custom_path="$1"
  if [[ ! -f "$custom_path" ]]; then
    echo "ERROR: --mcp-config $custom_path not found." >&2
    return 1
  fi
  local target="$(resolve_mcp_target)"
  local config_file="$(resolve_mcp_config_file "$target")"
  if [[ -z "$config_file" ]]; then
    echo "ERROR: no MCP config path known for target '$target'." >&2
    return 1
  fi
  mkdir -p "$(dirname "$config_file")"
  [[ -f "$config_file" ]] || echo '{"mcpServers": {}}' > "$config_file"

  python3 - "$custom_path" "$config_file" <<'PYEOF'
import json, sys, pathlib
custom = json.load(open(sys.argv[1]))
cfg_path = sys.argv[2]
if "name" not in custom:
    print("ERROR: custom MCP JSON must have top-level 'name' field.", file=sys.stderr)
    sys.exit(1)
name = custom["name"]
try:
    cfg = json.load(open(cfg_path))
except Exception:
    cfg = {}
cfg.setdefault("mcpServers", {})

entry = {"command": custom["command"], "args": custom["args"]}
env_block = {v["name"]: f"${{{v['name']}}}" for v in custom.get("env_vars", [])}
if env_block:
    entry["env"] = env_block

cfg["mcpServers"][name] = entry
pathlib.Path(cfg_path).write_text(json.dumps(cfg, indent=2) + "\n")
print(f"Wrote custom MCP '{name}' to {cfg_path}")
PYEOF
}

# Print the restart instruction for the target platform.
print_mcp_restart() {
  local target="$1"
  case "$target" in
    claude-code)     echo "  → Restart Claude Code (Cmd+Q / quit + relaunch) for MCPs to take effect." ;;
    claude-desktop)  echo "  → Quit and relaunch Claude Desktop for MCPs to take effect." ;;
    cursor)          echo "  → Reload Cursor window (Cmd+Shift+P → 'Developer: Reload Window')." ;;
    codex)           echo "  → Restart your Codex CLI process." ;;
    windsurf)        echo "  → Restart Windsurf." ;;
    cline)           echo "  → Reload VS Code window." ;;
    goose)           echo "  → Restart Goose." ;;
    *)               echo "  → Restart your agent surface for MCPs to take effect." ;;
  esac
}

# Top-level dispatcher: configure all the --mcp args + --mcp-config path.
configure_mcps() {
  if [[ ${#MCP_NAMES[@]} -eq 0 && -z "$MCP_CONFIG_PATH" ]]; then
    return  # no MCP flags passed; skip silently
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 required for --mcp setup (used to merge JSON config)." >&2
    return 1
  fi

  echo ""
  echo "═══════════════════════════════════════════════════════════════════"
  echo " MCP setup"
  echo "═══════════════════════════════════════════════════════════════════"
  for m in "${MCP_NAMES[@]}"; do
    configure_one_mcp "$m"
  done
  if [[ -n "$MCP_CONFIG_PATH" ]]; then
    configure_custom_mcp "$MCP_CONFIG_PATH"
  fi
  echo ""
  print_mcp_restart "$(resolve_mcp_target)"
  echo "═══════════════════════════════════════════════════════════════════"
}

# Short-circuit for --list-mcps (no install, no config, just print catalog).
if [[ $LIST_MCPS -eq 1 ]]; then
  list_mcps
  exit 0
fi

# (Handoff helpers were inadvertently placed below their first call site during refactor;
# they are now defined immediately after prompt_repo() — search for "Handoff-config helpers".)

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] would create: $DEST_DIR"
  for skill in "${SUITE_SKILLS[@]}"; do
    echo "[dry-run] would copy:   $SUITE_SRC_DIR/${skill#cost-billing-}  →  $DEST_DIR/$skill"
  done
  if [[ "$PERSONA" == "engineering" || "$PERSONA" == "all" ]] && [[ $SKIP_CODEGRAPH -eq 0 ]]; then
    echo "[dry-run] would install codegraph + run codegraph init -i in ${REPO:-<no-repo>}"
  fi
  if [[ -n "$REPO" ]]; then
    echo "[dry-run] would scaffold customer-context/ at $REPO/.moolabs/customer-context/"
  fi
  if [[ -d "$SUITE_SRC_DIR/cloud-bill-cli" ]] \
     && [[ "$PERSONA" == "engineering" || "$PERSONA" == "all" ]]; then
    echo "[dry-run] would prompt to set up the AWS CUR (install moo-cloud-bill, run 'configure', then schedule a daily 'push' via cron)"
  fi
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
  # Remove the managed daily-push cron line if present (machine-global, so do it
  # here regardless of platform; stripping an absent marker is a harmless no-op).
  if command -v crontab >/dev/null 2>&1; then
    _marker="# moo-cloud-bill push (managed by install.sh)"
    if crontab -l 2>/dev/null | grep -qF "$_marker"; then
      crontab -l 2>/dev/null | grep -vF "$_marker" | grep -v '^[[:space:]]*$' | crontab - \
        && echo "  removed managed 'moo-cloud-bill push' cron entry"
    fi
  fi
  echo ""
  echo "Uninstall complete. (customer-context/ in repo NOT touched — remove manually if desired.)"
  echo "(moo-cloud-bill CLI itself, AWS CUR export, and ~/.config/moo-cloud-bill creds left intact — remove manually if desired.)"
  exit 0
fi

# Auto-prune stale cost-billing-* skills NOT in this persona's install list.
# Catches: deprecated v0.1-0.2 cost-billing-bootstrap; legacy cost-billing-reconcile;
# anything from a prior persona install (e.g., user switched from 'all' to 'finance').
# Opt out via --no-prune.
mkdir -p "$DEST_DIR"
if [[ $NO_PRUNE -eq 0 ]]; then
  pruned_count=0
  for existing in "$DEST_DIR"/cost-billing-*; do
    [[ -d "$existing" ]] || continue
    name="$(basename "$existing")"
    in_list=0
    for skill in "${SUITE_SKILLS[@]}"; do
      if [[ "$name" == "$skill" ]]; then
        in_list=1
        break
      fi
    done
    if [[ $in_list -eq 0 ]]; then
      echo "  PRUNED stale: $name (not in '$PERSONA' install list)"
      rm -rf "$existing"
      pruned_count=$((pruned_count + 1))
    fi
  done
  if [[ $pruned_count -gt 0 ]]; then
    echo "  → pruned $pruned_count stale skill(s) before install"
  fi
fi

# Copy skills
for skill in "${SUITE_SKILLS[@]}"; do
  src="$SUITE_SRC_DIR/${skill#cost-billing-}"
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

# Engineering persona: CodeGraph
if [[ "$PERSONA" == "engineering" || "$PERSONA" == "all" ]] && [[ $SKIP_CODEGRAPH -eq 0 ]]; then
  install_codegraph
fi

# All personas that touch code: ralph-skills (autonomous loop) +
# moo-skills/superpowers (adversarial-pr-review, requesting-code-review,
# using-superpowers). The cost-billing skills REFERENCE these — without them
# every "/superpowers:* " or "/cost-billing-adversarial-review" cross-skill
# call fails silently.
if [[ "$PERSONA" == "engineering" || "$PERSONA" == "all" || "$PERSONA" == "product" ]] && [[ $SKIP_PLUGINS -eq 0 ]]; then
  install_ralph_and_superpowers
fi

# Optional: configure MCP servers picked by the user (--mcp / --mcp-config)
configure_mcps

# Optional: write the chain handoff config (~/.moolabs/handoff-config.yaml)
write_handoff_config

# Scaffold customer-context-template if repo given
scaffold_customer_context

# ──────────────────────────────────────────────────────────────────────
# Final report
# ──────────────────────────────────────────────────────────────────────

echo ""
echo "═════════════════════════════════════════════════════════════════════"
echo " Install complete — persona: $PERSONA"
echo "═════════════════════════════════════════════════════════════════════"
echo ""
echo "Slash-invocable skills installed for persona '$PERSONA':"
for skill in "${SUITE_SKILLS[@]}"; do
  case "$skill" in
    cost-billing-bootstrap-finance)
      echo "  /cost-billing-bootstrap-finance        — Chain Stage 1 (CFO): pricing + compliance + tenancy" ;;
    cost-billing-bootstrap-cpo)
      echo "  /cost-billing-bootstrap-cpo            — Chain Stage 2 (CPO): products + features + terminology" ;;
    cost-billing-bootstrap-team-product)
      echo "  /cost-billing-bootstrap-team-product   — Chain Stage 3 (team-PM): per-feature unit + input map" ;;
    cost-billing-bootstrap-team-engineer)
      echo "  /cost-billing-bootstrap-team-engineer  — Chain Stage 4 (engineer): repo + telemetry + MCP + SDK key" ;;
    cost-billing-signoff)
      echo "  /cost-billing-signoff                  — Three-role review orchestrator (CFO/PM/Engineer signoffs)" ;;
    cost-billing-bootstrap)
      echo "  /cost-billing-bootstrap                — DEPRECATED (prints redirect to chain stages)" ;;
    cost-billing-discovery)
      echo "  /cost-billing-discovery                — Skill A: scan repo, produce inventories (post-chain)" ;;
    cost-billing-cloud-bill)
      echo "  /cost-billing-cloud-bill               — Skill B: wire AWS / GCP / Azure exports (post-chain)" ;;
    cost-billing-instrument)
      echo "  /cost-billing-instrument               — Skill 2: codemod that wires SDK calls (post-chain)" ;;
    cost-billing-drift-lint)
      echo "  /cost-billing-drift-lint               — Skill 3: CI drift detection (post-chain)" ;;
    cost-billing-adversarial-review)
      echo "  /cost-billing-adversarial-review       — Skill R: per-stage adversarial gate + holistic + post-codemod" ;;
    cost-billing-shared)
      : ;;  # shared dir; not slash-invocable, listed below
  esac
done
echo ""
echo "Shared docs (read-only, not slash-invocable):"
echo "  cost-billing/README.md"
echo "  cost-billing-shared/anchor-taxonomy.md"
echo "  cost-billing-shared/sdk-surface-reference.md"
echo "  cost-billing-shared/v1-decisions-log.md"
echo "  cost-billing-shared/three-role-review.md"
echo "  cost-billing-shared/gaps-tracker.md"
echo ""

# ── Optional: set up the AWS CUR via the moo-cloud-bill CLI ─────────────────
# Installs the customer-run CLI (NOT an agent skill) and runs its discovery-first
# `configure` wizard (creates/reuses the AWS CUR 2.0 export; mutates AWS only on
# the engineer's explicit confirmation), then optionally schedules a daily `push`
# via cron. Opt-in: always asks interactively (skipped only when there's no TTY).
# Engineering/all personas only.
list_aws_profiles() {
  # Prefer the AWS CLI (authoritative); fall back to parsing ~/.aws files so this
  # works even if the CLI isn't installed. Portable awk (BSD + GNU), no bash 4.
  if command -v aws >/dev/null 2>&1; then
    local out; out="$(aws configure list-profiles 2>/dev/null)"
    if [[ -n "$out" ]]; then printf '%s\n' "$out"; return 0; fi
  fi
  local cfg="${AWS_CONFIG_FILE:-$HOME/.aws/config}"
  local creds="${AWS_SHARED_CREDENTIALS_FILE:-$HOME/.aws/credentials}"
  {
    [[ -f "$cfg" ]]   && awk -F'[][]' '/^\[/{p=$2; sub(/^profile[ \t]+/,"",p); print p}' "$cfg"
    [[ -f "$creds" ]] && awk -F'[][]' '/^\[/{print $2}' "$creds"
  } 2>/dev/null | awk 'NF' | sort -u
}

maybe_setup_cur() {
  # Run ONCE across the whole multi-platform fan-out. Each platform re-invokes the
  # script as a separate process, so a shell var can't guard this — claim an atomic
  # marker (mkdir is atomic) keyed to the shared run id. First process wins; the
  # rest return immediately.
  if ! mkdir "$_CUR_SETUP_MARKER" 2>/dev/null; then
    return 0
  fi
  [[ $PACKAGE_MODE -eq 1 || $UNINSTALL -eq 1 ]] && return 0
  # CUR setup is the customer engineer's job — only the engineering persona (who
  # also installs cost-billing-cloud-bill). Skip finance/CPO/team-product
  # (different machines in the chain-handoff design).
  case "$PERSONA" in engineering|team-engineer|engineer|all) ;; *) return 0 ;; esac
  local cli_dir="$SUITE_SRC_DIR/cloud-bill-cli"
  [[ -d "$cli_dir" ]] || return 0   # not bundled in this layout

  # Defensive: never mutate during a dry-run (the dry-run block exits earlier and
  # previews this step, so this guard only matters if the call site ever moves).
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[dry-run] would offer to install the CLI and run 'moo-cloud-bill configure'."
    return 0
  fi

  # Always ask interactively; only a non-interactive shell (no TTY) skips it.
  if [[ ! -t 0 ]]; then
    echo "AWS CUR setup: skipped (non-interactive). Later:  pip install \"$cli_dir\" && moo-cloud-bill configure"
    return 0
  fi
  echo "─── AWS Cost & Usage Report setup ──────────────────────────────────"
  echo "moo-cloud-bill configures your AWS CUR and pushes it to Acute (needs AWS creds)."
  printf "Configure the CUR now (installs the CLI, then runs its discovery-first wizard)? [y/N]: "
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "Skipped. Later:  pip install \"$cli_dir\" && moo-cloud-bill configure"; return 0 ;;
  esac

  # Prefer pip into the active env so the command is immediately invocable here.
  local pipcmd=""
  if command -v pip  >/dev/null 2>&1; then pipcmd="pip install"
  elif command -v pip3 >/dev/null 2>&1; then pipcmd="pip3 install"
  elif command -v pipx >/dev/null 2>&1; then pipcmd="pipx install"
  else
    echo "  ! pip not found — install + configure manually:  pip install \"$cli_dir\" && moo-cloud-bill configure" >&2
    return 0
  fi

  echo "  Installing the CLI ($pipcmd)…"
  if ! $pipcmd "$cli_dir"; then
    echo "  ! CLI install failed — run manually:  $pipcmd \"$cli_dir\" && moo-cloud-bill configure" >&2
    return 0
  fi

  echo "  ── AWS account & permissions ──────────────────────────────────"
  echo "  Use the account whose bill you want to ingest (single-account v1)."
  echo "  The chosen profile / SSO role needs at SETUP time:"
  echo "    bcm-data-exports:CreateExport, bcm-data-exports:ListExports,"
  echo "    bcm-data-exports:GetExport, s3:ListAllMyBuckets, s3:PutBucketPolicy,"
  echo "    sts:GetCallerIdentity (+ s3:CreateBucket if you create a new bucket)"
  echo "  AND the account must have 'IAM access to Billing' enabled"
  echo "  (Billing console → Account → IAM access) — without it, CUR calls 403"
  echo "  even with the right IAM policy. Ongoing 'push' needs only READ"
  echo "  (s3:ListBucket + s3:GetObject)."

  # boto3 needs valid credentials before configure can call STS. Let the engineer
  # SELECT a profile from ~/.aws, then (re)authenticate via SSO so an expired
  # token doesn't blow up the wizard.
  local _profiles=() _p _i _choice
  while IFS= read -r _p; do [[ -n "$_p" ]] && _profiles+=("$_p"); done < <(list_aws_profiles)

  local aws_profile=""
  if [[ ${#_profiles[@]} -gt 0 ]]; then
    echo "  Select an AWS profile for boto3:"
    _i=1
    for _p in "${_profiles[@]}"; do echo "    $_i) $_p"; _i=$((_i+1)); done
    echo "    $_i) (none — use the default credential chain)"
    printf "  Choice [1-%d]: " "$_i"
    read -r _choice
    if [[ "$_choice" =~ ^[0-9]+$ ]] && (( _choice >= 1 && _choice < _i )); then
      aws_profile="${_profiles[$((_choice - 1))]}"
    fi   # the "none" option or any other input → empty → default credential chain
  else
    echo "  No AWS profiles found in ~/.aws — using the default credential chain."
  fi
  local profile_args=()
  [[ -n "$aws_profile" ]] && profile_args=(--profile "$aws_profile")

  if command -v aws >/dev/null 2>&1; then
    printf "  Run 'aws sso login %s' now? [Y/n]: " "${aws_profile:+--profile $aws_profile}"
    read -r ans
    case "$ans" in
      n|N|no|NO) echo "    Skipping SSO login — ensure your credentials are valid." ;;
      *) aws sso login "${profile_args[@]}" \
           || echo "    ! 'aws sso login' failed (non-SSO profile or error) — continuing; configure will report if creds are invalid." ;;
    esac
  else
    echo "    (aws CLI not found — ensure your AWS credentials are valid before configure)"
  fi

  # One command prefix for both the installed entry point and the from-source run.
  local mcb_cmd
  if command -v moo-cloud-bill >/dev/null 2>&1; then
    mcb_cmd=(moo-cloud-bill)
  else
    mcb_cmd=(env "PYTHONPATH=$cli_dir/src" python3 -m moo_cloud_bill)
  fi

  echo "  Running the CUR configuration wizard (discovery-first; mutates AWS only on your confirmation)…"
  "${mcb_cmd[@]}" "${profile_args[@]}" configure \
    || echo "  (configure did not finish — re-run later:  moo-cloud-bill ${aws_profile:+--profile $aws_profile} configure)"

  # Capture the Moolabs API key (separate from AWS creds) so push/seed can reach
  # Acute. init has its own skip path if you don't have the key yet.
  echo "  Now capture your Moolabs API key (Moolabs UI → API Keys) so 'push' can send data:"
  "${mcb_cmd[@]}" init || true

  # Test that it actually works: Acute reachability + auth now (the part most
  # likely misconfigured). The first CUR delivers in ~24-48h, so a real data push
  # can't be validated yet — verify reports CUR-data readiness too.
  echo "  Verifying the Acute connection…"
  "${mcb_cmd[@]}" "${profile_args[@]}" verify || echo "  (verify reported an issue — see above; re-run: moo-cloud-bill verify)"

  # Automate the ongoing push (the whole point — the CUR refreshes daily and Acute
  # supersedes per-period, so a daily unattended push keeps attribution current).
  schedule_push_cron "$cli_dir" "$aws_profile"

  echo "  Setup done."
}

# Build a self-contained `push` command line (absolute paths, no reliance on the
# caller's PATH/PYTHONPATH) suitable for a cron entry, and install it as a daily
# job. The Moolabs API key is NOT inlined — `push` resolves it from the 0600
# credentials file written by `init` (env > file), so no secret lands in crontab.
schedule_push_cron() {
  local cli_dir="$1" aws_profile="$2"

  if ! command -v crontab >/dev/null 2>&1; then
    echo "  (crontab not found — automate 'push' with your OS scheduler;"
    echo "   e.g. a daily systemd timer or launchd agent running: moo-cloud-bill ${aws_profile:+--profile $aws_profile} push)"
    return 0
  fi

  echo ""
  echo "  ── Automate the daily push (cron) ─────────────────────────────"
  if [[ -n "$aws_profile" ]]; then
    # An SSO profile's short-lived token expires; cron can't `aws sso login`.
    # Flag it honestly rather than scheduling a job that silently 401s nightly.
    echo "  NOTE: cron runs unattended. If profile '$aws_profile' is AWS SSO, its"
    echo "  token expires and the nightly push will fail. For unattended push use an"
    echo "  instance role (EC2/ECS) or a non-SSO profile with long-lived/credential_process creds."
  fi
  printf "  Schedule a daily 'push' via cron now? [Y/n]: "
  read -r ans
  case "$ans" in
    n|N|no|NO) echo "  Skipped. Schedule later: moo-cloud-bill ${aws_profile:+--profile $aws_profile} push"; return 0 ;;
  esac

  # Resolve an absolute, self-contained command — cron has a minimal PATH.
  local push_bin
  if command -v moo-cloud-bill >/dev/null 2>&1; then
    push_bin="$(command -v moo-cloud-bill)"
  else
    local py; py="$(command -v python3 || echo /usr/bin/env python3)"
    push_bin="env PYTHONPATH=\"$cli_dir/src\" $py -m moo_cloud_bill"
  fi
  local push_cmd="$push_bin"
  [[ -n "$aws_profile" ]] && push_cmd="$push_cmd --profile $aws_profile"
  push_cmd="$push_cmd push"

  local logdir="$HOME/.moolabs/cloud-bill"
  mkdir -p "$logdir"
  local marker="# moo-cloud-bill push (managed by install.sh)"
  # Daily at 06:17 local — off the top of the hour to avoid a thundering herd, and
  # well after midnight so the prior UTC day's CUR has refreshed.
  local schedule="17 6 * * *"
  local line="$schedule $push_cmd >> \"$logdir/push.log\" 2>&1  $marker"

  # Idempotent: strip any prior managed line first, then append — re-running the
  # installer updates the entry in place instead of duplicating it.
  local existing; existing="$(crontab -l 2>/dev/null | grep -vF "$marker" || true)"
  if ! printf '%s\n%s\n' "$existing" "$line" | grep -v '^[[:space:]]*$' | crontab -; then
    echo "  ! Could not write crontab — add this line manually via 'crontab -e':" >&2
    echo "      $line" >&2
    return 0
  fi
  echo "  ✓ Scheduled: daily 06:17 local. Log → $logdir/push.log"
  echo "    Change the time or remove it with:  crontab -e   (find the managed marker)"
}
maybe_setup_cur
echo ""

if [[ $NO_BOOTSTRAP_CTA -eq 0 ]]; then
  echo "─── Next step for the $PERSONA persona ──────────────────────────────"
  case "$PERSONA" in
    finance)
      cat <<'EOF'
You're Stage 1 of 4 in the chain.

1. Open Claude Code (or your agent surface):
     /cost-billing-bootstrap-finance

2. You'll be asked ~10 questions, ONE AT A TIME:
     - Pricing model TYPE + sub-aspects
     - Pricing source of truth
     - Billable units (in your own words)
     - Fair-usage thresholds + overages + bundling
     - Per-customer custom pricing
     - Compliance regimes (SOC2/HIPAA/GDPR/FedRAMP) — regimes only; the
       sensitive-data categories they imply are asked at the CPO stage
     - Region(s), environments, multi-tenant shape

3. AI synthesizes a draft, Skill R reviews adversarially, you read R's
   findings + sign off.

4. Skill writes .moolabs/chain/01-finance.signed.yaml and prints
   instructions to email/Slack/Drive it to your CPO.
EOF
      ;;
    product|cpo)
      cat <<'EOF'
You're Stage 2 of 4 in the chain.

PRECONDITION: you should have received 01-finance.signed.yaml from
finance/CFO (via email, Slack, Drive, etc.). Save it locally.

1. Run:
     /cost-billing-bootstrap-cpo --input-from /path/to/01-finance.signed.yaml

2. AI loads finance's commitments + asks YOU 7-9 questions, ONE AT A TIME:
     - Company + product/vertical names (multi-product split if any)
     - Product doc sources (folders, URLs, MCPs — multiple OK)
     - Top features customer-enumerated
     - Internal-only callouts
     - End-user term, billable-output term, synonyms, unique concepts

3. AI synthesizes draft, Skill R reviews (catches hallucinated features +
   contradictions with finance), you sign off.

4. Skill writes .moolabs/chain/02-cpo.signed.yaml. Email/Slack/Drive to
   your team-product PM.
EOF
      ;;
    team-product)
      cat <<'EOF'
You're Stage 3 of 4 in the chain.

PRECONDITION: you should have received BOTH 01-finance.signed.yaml AND
02-cpo.signed.yaml from upstream personas.

1. Run:
     /cost-billing-bootstrap-team-product \
         --input-from /path/to/01-finance.signed.yaml \
         --input-from /path/to/02-cpo.signed.yaml

2. AI walks per-CPO-feature, ONE AT A TIME:
     - Confirm/refine CPO's top-features list
     - Per feature: which finance billable unit does this map to?
     - Per feature: conceptual input map (which vendor calls feed it)
     - Event_type naming convention + exact strings
     - Per-feature synonyms, refund-test pattern, cross-feature trace

3. AI synthesizes draft, Skill R reviews (catches per-feature unit drift
   vs finance, double-counted inputs, event_type collisions), you sign off.

4. Skill writes .moolabs/chain/03-team-product.signed.yaml. Send to the
   team engineer.
EOF
      ;;
    engineering)
      cat <<'EOF'
You're Stage 4 of 4 — the FINAL stage in the chain.

PRECONDITION: you should have all three upstream signed docs:
  01-finance.signed.yaml, 02-cpo.signed.yaml, 03-team-product.signed.yaml

1. (If you skipped --repo at install) run codegraph manually:
     cd <customer-repo> && codegraph init -i

2. Run:
     /cost-billing-bootstrap-team-engineer \
         --input-from /path/to/01-finance.signed.yaml \
         --input-from /path/to/02-cpo.signed.yaml \
         --input-from /path/to/03-team-product.signed.yaml \
         --repo /path/to/customer/repo

3. AI walks technical surface, ONE AT A TIME:
     - Repo paths, multi-repo shape, sub-services
     - Build/test commands, branch strategy
     - Primary tracer, secondary instrumentation, request-context pattern
     - Attribute prefix collisions
     - Agent surface, active LLM, MCP inventory + selection + restrictions
     - SDK key location + read pattern
   (Region/env/tenancy come from finance — confirm technical source only.)

4. AI synthesizes draft, Skill R reviews, you sign off. Skill generates
   the consolidated customer-context/ that downstream skills read.

5. AFTER your stage signs off, the downstream pipeline unblocks:
     /cost-billing-discovery <repo>                  # 3 inventories
     /cost-billing-cloud-bill --cloud aws|gcp|azure   # if wiring cloud
     /cost-billing-adversarial-review --phase holistic-pre-codemod
     /cost-billing-instrument <repo>                  # codemod
     /cost-billing-adversarial-review --phase post-codemod
     # CI: add cost-billing-drift-lint/assets/github-action.yml
EOF
      ;;
    all)
      cat <<'EOF'
You're a solo founder / integrator — all 4 chain stages on this machine.

Run the chain in order. Each stage's output becomes the next stage's input.

  1. /cost-billing-bootstrap-finance
     → writes .moolabs/chain/01-finance.signed.yaml

  2. /cost-billing-bootstrap-cpo \
       --input-from .moolabs/chain/01-finance.signed.yaml
     → writes .moolabs/chain/02-cpo.signed.yaml

  3. /cost-billing-bootstrap-team-product \
       --input-from .moolabs/chain/01-finance.signed.yaml \
       --input-from .moolabs/chain/02-cpo.signed.yaml
     → writes .moolabs/chain/03-team-product.signed.yaml

  4. /cost-billing-bootstrap-team-engineer \
       --input-from .moolabs/chain/01-finance.signed.yaml \
       --input-from .moolabs/chain/02-cpo.signed.yaml \
       --input-from .moolabs/chain/03-team-product.signed.yaml \
       --repo /path/to/customer/repo
     → writes .moolabs/chain/04-final.signed.yaml
       AND consolidated .moolabs/customer-context/

POST-CHAIN (downstream pipeline):
  5. /cost-billing-cloud-bill --cloud aws|gcp|azure   (if cloud-bill needed)
  6. /cost-billing-discovery <repo>                    (produce inventories)
  7. /cost-billing-adversarial-review --phase holistic-pre-codemod
  8. /cost-billing-instrument <repo>                   (codemod)
  9. /cost-billing-adversarial-review --phase post-codemod
 10. Wire /cost-billing-drift-lint to CI

(Skill C — attribution validation harness — is Moolabs-internal and NOT
in this customer-portable suite.)
EOF
      ;;
  esac
  echo "─────────────────────────────────────────────────────────────────────"
fi
