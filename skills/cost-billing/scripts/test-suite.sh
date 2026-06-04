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
echo "[1/8] SKILL.md frontmatter present (name + description)"
for skill_dir in "$SUITE_ROOT"/*/; do
  name=$(basename "$skill_dir")
  case "$name" in
    shared|examples|scripts|docs) continue ;;
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
echo "[2/8] YAML schemas + assets parse"
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
echo "[3/8] Python scripts compile"
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
echo "[4/8] install.sh syntax (wrapper + real)"
check "install.sh wrapper"     bash -n "$SUITE_ROOT/install.sh"
check "shared/install.sh"      bash -n "$SUITE_ROOT/shared/install.sh"
# Also parse under /bin/bash (macOS ships 3.2.57). Catches array / parameter
# expansion syntax that modern bash silently accepts but 3.2 rejects.
if [[ -x /bin/bash ]]; then
  check "install.sh wrapper (/bin/bash)"  /bin/bash -n "$SUITE_ROOT/install.sh"
  check "shared/install.sh (/bin/bash)"   /bin/bash -n "$SUITE_ROOT/shared/install.sh"
fi
echo ""

# ─── install.sh dry-run for each persona ───────────────────────────────
echo "[5/8] install.sh --dry-run per persona"
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

# ─── [6/8] install.sh dry-run under /bin/bash (macOS bash 3.2 customer env) ─
# Phase 5 runs under $PATH bash (typically 5.x on dev boxes). Customers on
# macOS hit /bin/bash 3.2.57 — different array/expansion semantics under
# `set -u`. This phase re-runs the same matrix under /bin/bash to catch
# bash-3.2 regressions before they reach customer dogfood.
echo "[6/8] install.sh --dry-run per persona under /bin/bash (bash 3.2 customer env)"
if [[ ! -x /bin/bash ]]; then
  yellow "  SKIP  /bin/bash not present on this host"
else
  bash32_version="$(/bin/bash --version | head -1)"
  echo "  /bin/bash → $bash32_version"
  for persona in finance product team-product engineering all; do
    if /bin/bash "$SUITE_ROOT/install.sh" \
         --persona "$persona" \
         --dry-run \
         --skip-codegraph \
         --skip-plugins \
         --no-bootstrap-cta \
         --no-prune \
         2>&1 | grep -q "would copy:"; then
      green "  PASS  persona=$persona (/bin/bash)"
      PASS=$((PASS + 1))
    else
      red "  FAIL  persona=$persona (/bin/bash) — install.sh aborted; suspect bash 3.2 + set -u"
      FAIL=$((FAIL + 1))
    fi
  done
fi
echo ""

# ─── [7/8] Codemod-template renders + Codex regression assertions ──────
# Each assertion guards a class of bug found in cross-model adversarial review.
echo "[7/8] template renders + adversarial-regression assertions"
python3 - "$SUITE_ROOT" <<'PYEOF'
import sys
from pathlib import Path
try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("  SKIP  jinja2 not installed; rendered-template assertions skipped")
    sys.exit(0)

suite_root = Path(sys.argv[1])
tpl_dir = suite_root / "instrument" / "assets" / "codemod-templates"
env = Environment(loader=FileSystemLoader(str(tpl_dir)))

entry_base = {
    "event_type":"completion.delivered","workflow_id":"checkout.recommendation.delivered",
    "idempotency_anchor":{"handler":"r","path_param":"customer_id","confidence":0.9},
    "refund_unit":{"unit":"completion","derivation":"1"},
    "cost_kind":"llm-tokens","cost_micros_source":"resp.cm",
    "cost_workflow_ids":["s.llm"],"consumer_agent_source":'"agent"',
}
sources = {"tenant_id":"req.state.tid","request_id":"req.state.rid","customer_id":"req.state.cid",
           "consumer_agent":None,"feature_key":None}
templates = ["python-fastapi.j2","python-django.j2","python-flask.j2",
             "typescript-express.j2","typescript-nestjs.j2","typescript-nextjs.j2"]
patterns = ["sibling-pair","usage-only","cost-only"]

pass_count, fail_count = 0, 0

# Helper-template renders (per-service, capability-true)
helper_ctx = {
    "service_slug": "test-svc",
    "signoff_chain_hashes": [],
    "sdk_key_location": {"strategy": "env_var"},
    "sdk_key_read_pattern": "",
    "sdk_pinned_version": "v0.2.0-rc9",
    "telemetry": {"mode": "brownfield"},
    "capabilities": {
        "cost_event_direct_emit": True,
        "cost_event_method_path": "client.cost.ingest_events_batch",
    },
}
for helper in ["python-moolabs-client.py.j2", "typescript-moolabs-client.ts.j2"]:
    try:
        r = env.get_template(helper).render(**helper_ctx)
        # CODEX-REGRESSION-1: cost log fallback must carry usage_event_id
        # CODEX-REGRESSION-1: usage log fallback must carry event_id
        if helper.startswith("python"):
            # Usage log fallback adds event_id in the initial dict literal.
            # Cost log fallback adds usage_event_id as a conditional bracket assignment.
            usage_id_in_log = '"event_id": resolved_event_id' in r
            cost_id_in_log = 'log_kwargs["usage_event_id"] = str(usage_event_id)' in r
            if usage_id_in_log and cost_id_in_log:
                print(f"  PASS  helper {helper}: sibling-pair ids survive recovery rail")
                pass_count += 1
            else:
                missing = []
                if not usage_id_in_log: missing.append("usage event_id in log")
                if not cost_id_in_log: missing.append("cost usage_event_id in log")
                print(f"  FAIL  helper {helper}: log fallback drops {missing} (Codex Finding #1)")
                fail_count += 1
        else:
            if "logPayload.usage_event_id" in r and "ingestEventsBatch" in r:
                print(f"  PASS  helper {helper}: SDK direct branch + log fallback ids present")
                pass_count += 1
            else:
                print(f"  FAIL  helper {helper}: TS SDK branch missing OR log drops sibling-pair ids (Codex Finding #1/#3)")
                fail_count += 1
    except Exception as e:
        print(f"  FAIL  helper {helper}: render error: {e}")
        fail_count += 1

# Go helper: v0.3.0-rc1 unified-ingest shape — single render (no capability gate),
# gofmt -e syntax gate, structural checks for the three ergonomic methods + env-gated
# error handling + the FR-3 tenant-absent guard.
import shutil, subprocess, tempfile
gofmt = shutil.which("gofmt")
try:
    r = env.get_template("go-moolabs-client.go.j2").render(**helper_ctx)
except Exception as e:
    print(f"  FAIL  go-moolabs-client.go.j2: render error: {e}")
    fail_count += 1
else:
    # v0.3.0 ergonomic methods all present
    has_usage  = "cli.Usage.IngestEvent(ctx, args)" in r
    has_cost   = "cli.Cost.IngestEvent(ctx, args)" in r
    has_events = "cli.Events.Ingest(ctx, args)" in r
    # env-gated strict/lax error handling
    has_devgate = 'os.Getenv(devEnvVar)' in r and 'SDK_DEVELOPMENT' in r
    # never-drop log rail still wired
    has_rail = 'logEvent("moolabs.' in r
    # FR-3: TenantID must be absent from the helper (server derives from API key).
    # The docstring explains the absence, so we check for actual *usage* patterns
    # — struct-field init, field access, or map-key — not bare token mentions.
    no_tenant = ('TenantID:' not in r and 'TenantId:' not in r
                 and '.TenantID' not in r and '.TenantId' not in r
                 and '"tenant_id":' not in r and "'tenant_id':" not in r)
    # v0.2 legacy shapes must be gone
    no_legacy = "IngestEventsBatch" not in r and "ObservedTotalCost" not in r \
                and "BatchIngestRequest" not in r and "cost_event_direct_emit" not in r
    failed = []
    if not has_usage:  failed.append("Usage.IngestEvent missing")
    if not has_cost:   failed.append("Cost.IngestEvent missing")
    if not has_events: failed.append("Events.Ingest missing")
    if not has_devgate:failed.append("SDK_DEVELOPMENT env gate missing")
    if not has_rail:   failed.append("never-drop log rail missing")
    if not no_tenant:  failed.append("TenantID leaked (FR-3 violation)")
    if not no_legacy:  failed.append("v0.2 legacy shape leaked")
    if failed:
        print(f"  FAIL  go-moolabs-client.go.j2: {', '.join(failed)}")
        fail_count += 1
    elif gofmt:
        with tempfile.NamedTemporaryFile("w", suffix=".go", delete=False) as tf:
            tf.write(r); tfp = tf.name
        res = subprocess.run([gofmt, "-e", tfp], capture_output=True, text=True)
        Path(tfp).unlink()
        if res.returncode != 0:
            print(f"  FAIL  go-moolabs-client.go.j2: gofmt -e: {res.stderr.strip()[:200]}")
            fail_count += 1
        else:
            print(f"  PASS  go-moolabs-client.go.j2: v0.3.0 ergonomic methods + env-gated rail + gofmt-clean")
            pass_count += 1
    else:
        print(f"  SKIP-gofmt go-moolabs-client.go.j2: structural-only PASS (gofmt not on PATH)")
        pass_count += 1

# Per-callsite template renders × all 3 patterns
for tpl in templates:
    for pat in patterns:
        entry = {**entry_base, "pattern": pat}
        try:
            r = env.get_template(tpl).render(entry=entry, attribution_sources=sources)
        except Exception as e:
            print(f"  FAIL  {tpl}[{pat}]: render error: {e}")
            fail_count += 1
            continue

        # CODEX-REGRESSION-2: TS usage-only / cost-only must NOT reference _moolabsEventId (undefined in those branches)
        if tpl.startswith("typescript-") and pat in ("usage-only", "cost-only"):
            if "_moolabsEventId" in r:
                print(f"  FAIL  {tpl}[{pat}]: references undefined _moolabsEventId (Codex Finding #2)")
                fail_count += 1; continue

        # Sibling-pair must wire both ids
        if pat == "sibling-pair":
            if tpl.startswith("python-"):
                ok = "_moolabs_event_id" in r and "event_id=_moolabs_event_id" in r and "usage_event_id=_moolabs_event_id" in r
            else:
                ok = "_moolabsEventId" in r and "eventId: _moolabsEventId" in r and "usageEventId: _moolabsEventId" in r
            if not ok:
                print(f"  FAIL  {tpl}[{pat}]: sibling-pair missing shared event_id wiring")
                fail_count += 1; continue

        # Python: ast-compile rendered output
        if tpl.startswith("python-"):
            try:
                src = "import response, request, customer_id, log_context\ndef _fn():\n"
                for line in r.splitlines(): src += "    " + line + "\n"
                compile(src, tpl, "exec")
            except SyntaxError as e:
                print(f"  FAIL  {tpl}[{pat}]: py syntax error: {e.msg}")
                fail_count += 1; continue

        # TS: cost call must be awaited (post-async helper)
        if tpl.startswith("typescript-") and pat in ("sibling-pair", "cost-only"):
            if "emitCostEventSafe(" in r and "await emitCostEventSafe(" not in r:
                print(f"  FAIL  {tpl}[{pat}]: emitCostEventSafe is async; callsite missing await")
                fail_count += 1; continue

        print(f"  PASS  {tpl}[{pat}]")
        pass_count += 1

# CODEX-REGRESSION-4: example attribution-bindings satisfies planner gate
bindings_yaml = suite_root / "examples" / "attribution-bindings.yaml"
import yaml
b = yaml.safe_load(bindings_yaml.read_text())
required = ["tenant_id", "customer_id", "request_id", "consumer_agent"]
declared = list((b.get("bindings") or {}).keys())
missing = [k for k in required if k not in declared]
if missing:
    print(f"  FAIL  examples/attribution-bindings.yaml: missing required keys {missing} (Codex Finding #4)")
    fail_count += 1
else:
    print(f"  PASS  examples/attribution-bindings.yaml satisfies planner refuse-to-run gate")
    pass_count += 1

print(f"\n  Phase-7 result: {pass_count} pass, {fail_count} fail")
sys.exit(0 if fail_count == 0 else 1)
PYEOF
PHASE6=$?
if [[ $PHASE6 -ne 0 ]]; then
  FAIL=$((FAIL + 1))
else
  PASS=$((PASS + 1))
fi
echo ""

# ─── [8/8] Python unit tests (stdlib unittest, no pytest dependency) ────
echo "[8/8] python unit tests (test_*.py)"
_found_tests=0
while IFS= read -r -d '' test_file; do
  _found_tests=1
  rel="${test_file#$SUITE_ROOT/}"
  if python3 "$test_file" >/dev/null 2>&1; then
    green "  PASS  $rel"
    PASS=$((PASS + 1))
  else
    red "  FAIL  $rel"
    FAIL=$((FAIL + 1))
  fi
done < <(find "$SUITE_ROOT" -name "test_*.py" -not -path "*/__pycache__/*" -print0)
if [[ $_found_tests -eq 0 ]]; then
  yellow "  SKIP  no test_*.py files found"
fi
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
