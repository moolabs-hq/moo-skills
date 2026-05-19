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
  cost-billing-adversarial-review         # Skill R fires inside Stage 4 of finance bootstrap
  cost-billing-shared                     # required by every skill
)

# CPO uses persona 'product' (org-level product strategy)
SKILLS_PRODUCT=(
  cost-billing-bootstrap-cpo              # Stage 2: company + product + features + terminology
  cost-billing-adversarial-review
  cost-billing-shared
)

# Team-PM (team product engineer per user's vocabulary) uses persona 'team-product'
SKILLS_TEAM_PRODUCT=(
  cost-billing-bootstrap-team-product     # Stage 3: per-feature unit + event_type + input map
  cost-billing-adversarial-review
  cost-billing-shared
)

# Team-engineer (IC engineer) uses persona 'engineering'
SKILLS_ENGINEERING=(
  cost-billing-bootstrap-team-engineer    # Stage 4: repo + telemetry + MCP + SDK key
  cost-billing-discovery                  # post-chain: produce inventories
  cost-billing-cloud-bill                 # post-chain: wire cloud-bill exports
  cost-billing-instrument                 # post-chain: codemod
  cost-billing-drift-lint                 # post-chain: CI drift
  cost-billing-adversarial-review
  cost-billing-shared
)

# 'all' = solo founder / integrator machine running all 4 stages locally.
SKILLS_ALL=(
  cost-billing-bootstrap-finance
  cost-billing-bootstrap-cpo
  cost-billing-bootstrap-team-product
  cost-billing-bootstrap-team-engineer
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
# It is an engineering-internal Moolabs harness for validating moo-acute's
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

PLATFORM=""
SCOPE="user"
PERSONA=""
REPO=""
DRY_RUN=0
UNINSTALL=0
SKIP_CODEGRAPH=0
FORCE_CODEGRAPH_INGEST=0
NO_BOOTSTRAP_CTA=0
NO_PRUNE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --user) SCOPE="user"; shift ;;
    --project) SCOPE="project"; shift ;;
    --persona) PERSONA="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --skip-codegraph) SKIP_CODEGRAPH=1; shift ;;
    --force-codegraph-ingest) FORCE_CODEGRAPH_INGEST=1; shift ;;
    --no-bootstrap-cta) NO_BOOTSTRAP_CTA=1; shift ;;
    --no-prune) NO_PRUNE=1; shift ;;
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
# Platform detection
# ──────────────────────────────────────────────────────────────────────

detect_platform() {
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
# CodeGraph install + ingest
# ──────────────────────────────────────────────────────────────────────

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
  local tpl="$SUITE_SRC_DIR/cost-billing-bootstrap/assets/customer-context-templates"
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
  PLATFORM="$(detect_platform)"
fi
if [[ -z "$PLATFORM" ]]; then
  cat >&2 <<'EOF'
ERROR: Could not auto-detect agent platform.

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
EOF
  exit 1
fi

DEST_DIR="$(resolve_dest_dir "$PLATFORM" "$SCOPE")"
if [[ -z "$DEST_DIR" ]]; then
  echo "ERROR: Unknown platform: $PLATFORM" >&2
  exit 1
fi

if [[ $UNINSTALL -eq 0 ]]; then
  prompt_persona
  prompt_repo
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

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] would create: $DEST_DIR"
  for skill in "${SUITE_SKILLS[@]}"; do
    echo "[dry-run] would copy:   $SUITE_SRC_DIR/$skill  →  $DEST_DIR/$skill"
  done
  if [[ "$PERSONA" == "engineering" || "$PERSONA" == "all" ]] && [[ $SKIP_CODEGRAPH -eq 0 ]]; then
    echo "[dry-run] would install codegraph + run codegraph init -i in ${REPO:-<no-repo>}"
  fi
  if [[ -n "$REPO" ]]; then
    echo "[dry-run] would scaffold customer-context/ at $REPO/.moolabs/customer-context/"
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
  echo ""
  echo "Uninstall complete. (customer-context/ in repo NOT touched — remove manually if desired.)"
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

# Engineering persona: CodeGraph
if [[ "$PERSONA" == "engineering" || "$PERSONA" == "all" ]] && [[ $SKIP_CODEGRAPH -eq 0 ]]; then
  install_codegraph
fi

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
echo "  cost-billing-shared/SUITE_README.md"
echo "  cost-billing-shared/anchor-taxonomy.md"
echo "  cost-billing-shared/sdk-surface-reference.md"
echo "  cost-billing-shared/v1-decisions-log.md"
echo "  cost-billing-shared/three-role-review.md"
echo "  cost-billing-shared/gaps-tracker.md"
echo ""

if [[ $NO_BOOTSTRAP_CTA -eq 0 ]]; then
  echo "─── Next step for the $PERSONA persona ──────────────────────────────"
  case "$PERSONA" in
    finance)
      cat <<'EOF'
You're Stage 1 of 4 in the chain.

1. Open Claude Code (or your agent surface):
     /cost-billing-bootstrap-finance

2. You'll be asked ~12 questions, ONE AT A TIME:
     - Pricing model TYPE + sub-aspects
     - Pricing source of truth
     - Billable units (in your own words)
     - Fair-usage thresholds + overages + bundling
     - Per-customer custom pricing
     - Compliance regimes (SOC2/HIPAA/GDPR/FedRAMP)
     - PII / PHI field blocklists
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
