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

SKILLS_FINANCE=(
  cost-billing-bootstrap         # generates customer-context (one-time)
  cost-billing-discovery         # CFO does Stage 1 here — fair-usage + pricing
  cost-billing-adversarial-review  # CFO participates in Skill R review of their stage
  cost-billing-shared            # required by every skill
)
SKILLS_PRODUCT=(
  cost-billing-bootstrap
  cost-billing-discovery         # PM does Stage 2 + Stage 2b/3b loops
  cost-billing-cloud-bill        # PM reviews cell ③ findings
  cost-billing-adversarial-review
  cost-billing-shared
)
SKILLS_ENGINEERING=(
  cost-billing-bootstrap
  cost-billing-discovery         # Engineer does Stage 3 here
  cost-billing-cloud-bill        # Engineer wires the cloud-bill exports
  cost-billing-instrument        # Engineer-only: the codemod
  cost-billing-drift-lint        # Engineer-only: CI drift lint
  cost-billing-adversarial-review
  cost-billing-shared
)
SKILLS_ALL=(
  cost-billing-bootstrap
  cost-billing-discovery
  cost-billing-cloud-bill
  cost-billing-instrument
  cost-billing-drift-lint
  cost-billing-adversarial-review
  cost-billing-shared
)
# Note: cost-billing-reconcile was removed from this customer-portable suite.
# It is an engineering-internal Moolabs harness for validating moo-acute's
# attribution_engine.py against real customer cloud bills; it has no business
# running in a customer environment. Tracked separately as Moolabs internal
# infrastructure — see cost-billing-shared/v1-decisions-log.md.

# Will be set to one of the above arrays after persona is known.
SUITE_SKILLS=()

select_skills_for_persona() {
  case "$PERSONA" in
    finance)     SUITE_SKILLS=("${SKILLS_FINANCE[@]}") ;;
    product)     SUITE_SKILLS=("${SKILLS_PRODUCT[@]}") ;;
    engineering) SUITE_SKILLS=("${SKILLS_ENGINEERING[@]}") ;;
    all)         SUITE_SKILLS=("${SKILLS_ALL[@]}") ;;
    *)
      # Uninstall path: remove every possible skill we might have placed.
      SUITE_SKILLS=("${SKILLS_ALL[@]}")
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
NO_BOOTSTRAP_CTA=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --user) SCOPE="user"; shift ;;
    --project) SCOPE="project"; shift ;;
    --persona) PERSONA="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --skip-codegraph) SKIP_CODEGRAPH=1; shift ;;
    --no-bootstrap-cta) NO_BOOTSTRAP_CTA=1; shift ;;
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
    echo "Pass --persona finance|product|engineering|all" >&2
    exit 1
  fi
  echo ""
  echo "Which persona are you installing for?"
  echo ""
  echo "  1) finance      — CFO / finance reviewer. Reviews pricing + projected revenue."
  echo "  2) product      — Product Manager. Builds output↔input bill of materials."
  echo "  3) engineering  — Engineer. Wires SDK calls, runs codemod, reviews drift. (Installs CodeGraph + ingests repo.)"
  echo "  4) all          — Integrator machine; runs the full pipeline end-to-end."
  echo ""
  local choice=""
  while [[ -z "$choice" ]]; do
    read -r -p "Choice [1-4]: " choice
    case "$choice" in
      1|finance)     PERSONA="finance" ;;
      2|product)     PERSONA="product" ;;
      3|engineering|eng) PERSONA="engineering" ;;
      4|all)         PERSONA="all" ;;
      *) echo "Invalid; pick 1-4 or finance|product|engineering|all"; choice="" ;;
    esac
  done
  echo "Persona: $PERSONA"
}

prompt_repo() {
  # Only ask for repo if we'll install CodeGraph
  if [[ "$PERSONA" != "engineering" && "$PERSONA" != "all" ]]; then return; fi
  if [[ $SKIP_CODEGRAPH -eq 1 ]]; then return; fi
  if [[ -n "$REPO" ]]; then return; fi
  if [[ ! -t 0 ]]; then return; fi
  echo ""
  echo "Path to the customer repository for CodeGraph ingest?"
  echo "  (Leave blank to skip CodeGraph init; you can run 'codegraph init -i' manually later.)"
  read -r -p "Repo path: " REPO
  REPO="${REPO/#\~/$HOME}"
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

  # Look up the latest version on npm (best-effort; if offline, skip the comparison)
  if command -v npm >/dev/null 2>&1; then
    latest_version="$(npm view @colbymchenry/codegraph version 2>/dev/null | tail -1)"
    if [[ -n "$latest_version" ]]; then
      echo "Latest on npm: v${latest_version}"
    else
      echo "Latest on npm: (offline or registry lookup failed)"
    fi
  fi

  # Decision: install fresh / upgrade / leave alone
  if [[ -z "$installed_version" ]]; then
    # Not installed — install latest
    if ! command -v npm >/dev/null 2>&1; then
      cat >&2 <<'EOF'
WARNING: npm not found. CodeGraph requires Node.js + npm.
Install Node.js (https://nodejs.org), then:
  npm install -g @colbymchenry/codegraph

Continuing skill install without CodeGraph.
EOF
      return 0
    fi
    echo "Installing @colbymchenry/codegraph (this may take a minute)..."
    if npm install -g @colbymchenry/codegraph@latest 2>&1 | tail -8; then
      installed_version="$(codegraph --version 2>/dev/null | head -1 | awk '{print $NF}')"
      echo "Installed: codegraph v${installed_version}"
    else
      cat >&2 <<'EOF'
WARNING: 'npm install -g @colbymchenry/codegraph' failed.
You may need sudo or to fix your npm global prefix.
See https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally
Continuing skill install without CodeGraph.
EOF
      return 0
    fi
  elif [[ -n "$latest_version" && "$installed_version" != "$latest_version" ]]; then
    # Installed but outdated — upgrade
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

  # Ingest: run codegraph init -i in the customer repo if provided
  if [[ -n "$REPO" ]]; then
    if [[ ! -d "$REPO" ]]; then
      echo "WARNING: --repo $REPO does not exist; skipping codegraph init"
      return 0
    fi
    if [[ -d "$REPO/.codegraph" ]]; then
      echo "CodeGraph already initialized at $REPO/.codegraph (skipping init -i)"
    else
      echo ""
      echo "Running: codegraph init -i (in $REPO)"
      echo "(builds the semantic knowledge graph; /cost-billing-discovery + drift-lint use it)"
      echo ""
      ( cd "$REPO" && codegraph init -i ) || {
        echo "WARNING: 'codegraph init -i' failed in $REPO; you can re-run manually." >&2
      }
    fi
  else
    echo "No --repo provided; skipping codegraph ingest."
    echo "Run manually later: cd <customer-repo> && codegraph init -i"
  fi
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
    cp "$tpl/product-summary.template.md"  "$ctx/product-summary.template.md" 2>/dev/null || true
    cp "$tpl/pricing-model.template.yaml"  "$ctx/pricing-model.template.yaml" 2>/dev/null || true
    cp "$tpl/repo-info.template.yaml"      "$ctx/repo-info.template.yaml" 2>/dev/null || true
    cp "$tpl/telemetry-stack.template.yaml" "$ctx/telemetry-stack.template.yaml" 2>/dev/null || true
    cp "$tpl/terminology.template.yaml"    "$ctx/terminology.template.yaml" 2>/dev/null || true
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

# Copy skills
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
    cost-billing-bootstrap)
      echo "  /cost-billing-bootstrap           — first-run customer-context generator (RUN ME FIRST)" ;;
    cost-billing-discovery)
      echo "  /cost-billing-discovery           — Skill A: scan repo, produce inventories" ;;
    cost-billing-cloud-bill)
      echo "  /cost-billing-cloud-bill          — Skill B: wire AWS / GCP / Azure exports" ;;
    cost-billing-instrument)
      echo "  /cost-billing-instrument          — Skill 2: codemod that wires SDK calls" ;;
    cost-billing-drift-lint)
      echo "  /cost-billing-drift-lint          — Skill 3: CI drift detection" ;;
    cost-billing-adversarial-review)
      echo "  /cost-billing-adversarial-review  — Skill R: 5-phase quality gate" ;;
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
1. Open Claude Code (or your agent surface) in the customer repo:
     cd <customer-repo>
2. Run the bootstrap to generate customer-context:
     /cost-billing-bootstrap
   You'll be asked for:
     - product reference doc (path/URL/paste)
     - pricing page URL
     - primary repo path
     - telemetry stack
     - terminology overrides
3. Then start the CFO stage:
     /cost-billing-discovery <customer-repo>
   You'll fill cfo_metadata blocks in usage-events-inventory.yaml
   (fair-usage values, billed units, projected revenue).

After your stage, PM reviews; if PM finds issues, you'll get a Stage 2b cycle.
EOF
      ;;
    product)
      cat <<'EOF'
1. Open Claude Code (or your agent surface) in the customer repo:
     cd <customer-repo>
2. Run the bootstrap to generate customer-context:
     /cost-billing-bootstrap
3. Wait for CFO Stage 1 to be signed off.
4. Then your stage:
     /cost-billing-discovery <customer-repo>
   You'll:
     - pick billable units per output
     - build output-input-map.yaml (the bill of materials)
     - flag CFO reopens if a proposed unit can't be supported

You're the apex of two review loops:
  - CFO ⇄ PM (Stage 2b, hard cap 3 cycles)
  - Engineer ⇄ PM (Stage 3b, uncapped — code reality wins)
EOF
      ;;
    engineering)
      cat <<'EOF'
1. Open Claude Code (or your agent surface) in the customer repo:
     cd <customer-repo>
2. Run the bootstrap to generate customer-context:
     /cost-billing-bootstrap
3. (If you skipped --repo earlier, run codegraph manually now:)
     cd <customer-repo> && codegraph init -i
4. Wait for CFO Stage 1 + PM Stage 2 + Stage 2b cycle to be signed off.
5. Then your stage:
     /cost-billing-discovery <customer-repo>
   You'll verify file:line, framework adapters, idempotency anchors,
   and reject false positives.
6. After all three signoffs + holistic adversarial review:
     /cost-billing-instrument <customer-repo>
   This is the codemod that wires the SDK calls into customer code.
7. Add CI drift-lint (one-time):
     Copy cost-billing-drift-lint/assets/github-action.yml to .github/workflows/

CodeGraph is installed and ingested — /cost-billing-discovery and drift-lint
will use it for higher-fidelity code-graph queries.
EOF
      ;;
    all)
      cat <<'EOF'
You're set up as an integrator — all skills installed, CodeGraph ingested.

Recommended flow:
  1. /cost-billing-bootstrap        — generate customer-context
  2. /cost-billing-cloud-bill       — wire cloud exports (24-48h floor begins)
  3. /cost-billing-discovery        — produce inventories
     [CFO Stage 1 → PM Stage 2 → CFO ⇄ PM Stage 2b → Engineer Stage 3 → Engineer ⇄ PM Stage 3b]
  4. /cost-billing-adversarial-review --phase holistic-pre-codemod
  5. /cost-billing-instrument       — run the codemod
  6. /cost-billing-adversarial-review --phase post-codemod
  7. Add /cost-billing-drift-lint to CI

(Skill C — the attribution-validation harness — is Moolabs-internal infrastructure
and is NOT part of this customer-portable suite.)
EOF
      ;;
  esac
  echo "─────────────────────────────────────────────────────────────────────"
fi
