#!/usr/bin/env bash
# test-suite.sh — smoke test the cost-billing suite.
#
# Validates:
#   - Every skill has a parseable SKILL.md with name + description in frontmatter
#   - Every YAML schema in assets/ parses
#   - Every Python script in scripts/ parses
#   - install.sh (both the wrapper and the real one in shared/) pass bash -n
#   - Dry-run install for each of the 4 silo personas + 'all'
#
# Exit code: 0 if everything passes, 1 if any check fails.
#
# Run from anywhere — paths are anchored to this script's location.

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SUITE_ROOT="$(cd "$HERE/.." && pwd)"

FAIL=0
PASS=0

red() { printf "\033[31m%s\033[0m\n" "$1"; }
green() { printf "\033[32m%s\033[0m\n" "$1"; }
yellow() { printf "\033[33m%s\033[0m\n" "$1"; }

check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    green "  PASS  $label"
    PASS=$((PASS + 1))
  else
    red "  FAIL  $label"
    FAIL=$((FAIL + 1))
  fi
}

echo "Cost+Billing suite smoke test"
echo "Suite root: $SUITE_ROOT"
echo ""

# ─── SKILL.md frontmatter ──────────────────────────────────────────────
echo "[1/5] SKILL.md frontmatter present (name + description)"
for skill_dir in "$SUITE_ROOT"/*/; do
  name=$(basename "$skill_dir")
  case "$name" in
    shared|examples|scripts) continue ;;
  esac
  skill_md="$skill_dir/SKILL.md"
  if [[ ! -f "$skill_md" ]]; then
    red "  FAIL  $name: SKILL.md missing"
    FAIL=$((FAIL + 1))
    continue
  fi
  if ! grep -q "^name: " "$skill_md"; then
    red "  FAIL  $name: SKILL.md missing 'name:' frontmatter"
    FAIL=$((FAIL + 1))
    continue
  fi
  if ! grep -q "^description:" "$skill_md"; then
    red "  FAIL  $name: SKILL.md missing 'description:' frontmatter"
    FAIL=$((FAIL + 1))
    continue
  fi
  green "  PASS  $name/SKILL.md"
  PASS=$((PASS + 1))
done
echo ""

# ─── YAML schemas parse ─────────────────────────────────────────────────
echo "[2/5] YAML schemas + assets parse"
while IFS= read -r -d '' yaml_file; do
  rel="${yaml_file#$SUITE_ROOT/}"
  if python3 -c "import yaml, sys; yaml.safe_load(open(sys.argv[1]).read())" "$yaml_file" 2>/dev/null; then
    green "  PASS  $rel"
    PASS=$((PASS + 1))
  else
    red "  FAIL  $rel"
    FAIL=$((FAIL + 1))
  fi
done < <(find "$SUITE_ROOT" -name "*.yaml" -not -path "*/node_modules/*" -not -path "*/.git/*" -print0)
echo ""

# ─── Python scripts parse ──────────────────────────────────────────────
echo "[3/5] Python scripts compile"
while IFS= read -r -d '' py_file; do
  rel="${py_file#$SUITE_ROOT/}"
  if python3 -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" "$py_file" 2>/dev/null; then
    green "  PASS  $rel"
    PASS=$((PASS + 1))
  else
    red "  FAIL  $rel"
    FAIL=$((FAIL + 1))
  fi
done < <(find "$SUITE_ROOT" -name "*.py" -not -path "*/__pycache__/*" -print0)
echo ""

# ─── install.sh syntax ─────────────────────────────────────────────────
echo "[4/5] install.sh syntax (wrapper + real)"
check "install.sh wrapper"     bash -n "$SUITE_ROOT/install.sh"
check "shared/install.sh"      bash -n "$SUITE_ROOT/shared/install.sh"
echo ""

# ─── install.sh dry-run for each persona ───────────────────────────────
echo "[5/5] install.sh --dry-run per persona"
for persona in finance product team-product engineering all; do
  if bash "$SUITE_ROOT/install.sh" \
       --persona "$persona" \
       --dry-run \
       --skip-codegraph \
       --skip-plugins \
       --no-bootstrap-cta \
       --no-prune \
       2>&1 | grep -q "would copy:"; then
    green "  PASS  persona=$persona"
    PASS=$((PASS + 1))
  else
    red "  FAIL  persona=$persona (no 'would copy:' in dry-run output)"
    FAIL=$((FAIL + 1))
  fi
done
echo ""

# ─── Summary ───────────────────────────────────────────────────────────
echo "─────────────────────────────────────"
echo "  PASS: $PASS    FAIL: $FAIL"
echo "─────────────────────────────────────"
if [[ $FAIL -gt 0 ]]; then
  red "Suite smoke test FAILED ($FAIL failures)"
  exit 1
else
  green "Suite smoke test passed ($PASS checks)"
  exit 0
fi
