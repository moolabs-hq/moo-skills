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

# Helper-template renders. v0.3.0-rc1 helpers no longer branch on capability
# flags — they unconditionally call client.{usage,cost}.ingest_event /
# client.events.ingest. The capability dict is kept here only because some
# legacy includes may still read it during transition; the `no_legacy`
# rendered-output assertions below verify the OLD flag names cannot leak.
helper_ctx = {
    "service_slug": "test-svc",
    "signoff_chain_hashes": [],
    "sdk_key_location": {"strategy": "env_var"},
    "sdk_key_read_pattern": "",
    "sdk_pinned_version": "v0.3.0-rc1",
    "telemetry": {"mode": "brownfield"},
    "capabilities": {
        "unified_ingest_present": True,
        "usage_ergonomic_ingest": True,
        "cost_ergonomic_ingest": True,
        "events_unified_namespace": True,
        "usage_method_path": "client.usage.ingest_event",
        "cost_method_path": "client.cost.ingest_event",
        "events_method_path": "client.events.ingest",
    },
}
for helper in ["python-moolabs-client.py.j2", "typescript-moolabs-client.ts.j2"]:
    try:
        r = env.get_template(helper).render(**helper_ctx)
    except Exception as e:
        print(f"  FAIL  helper {helper}: render error: {e}")
        fail_count += 1
        continue
    if helper.startswith("python"):
        # v0.3.0-rc1 ergonomic-method assertions (parallel to the Go helper checks).
        # The Codex Finding #1 (usage_event_id / event_id linking via the recovery rail)
        # is obsolete in v0.3: entity_id replaces usage_event_id, the SDK auto-stamps
        # event_id, and tenant_id is gone per FR-3.
        has_usage   = ".usage.ingest_event(" in r
        has_cost    = ".cost.ingest_event(" in r
        has_events  = ".events.ingest(" in r
        has_devgate = "_DEV_ENV_VAR" in r and "SDK_DEVELOPMENT" in r
        has_rail    = 'logger.warning(' in r and 'log_recovery_rail' in r
        # FR-3: surgical check — no tenant_id KWARG or field use, allow docstring prose.
        no_tenant   = ("tenant_id=" not in r and "'tenant_id'" not in r
                       and '"tenant_id"' not in r)
        # v0.2 legacy must be gone
        no_legacy   = ("cost_event_direct_emit" not in r
                       and "ingest_events_batch" not in r
                       and "resolved_event_id" not in r
                       and "skipped_no_tenant_id" not in r
                       and "log_kwargs[" not in r)
        failed = []
        if not has_usage:   failed.append("usage.ingest_event missing")
        if not has_cost:    failed.append("cost.ingest_event missing")
        if not has_events:  failed.append("events.ingest missing")
        if not has_devgate: failed.append("SDK_DEVELOPMENT env gate missing")
        if not has_rail:    failed.append("never-drop log rail missing")
        if not no_tenant:   failed.append("tenant_id leaked (FR-3 violation)")
        if not no_legacy:   failed.append("v0.2 legacy shape leaked")
        if failed:
            print(f"  FAIL  helper {helper}: {', '.join(failed)}")
            fail_count += 1
        else:
            print(f"  PASS  helper {helper}: v0.3.0 ergonomic methods + env-gated rail + FR-3 clean")
            pass_count += 1
    else:
        # v0.3.0 TS ergonomic-method assertions (parallel to Python).
        has_usage   = ".usage.ingestEvent(" in r
        has_cost    = ".cost.ingestEvent(" in r
        has_events  = ".events.ingest(" in r
        has_devgate = "DEV_ENV_VAR" in r and "SDK_DEVELOPMENT" in r
        has_rail    = "logger.warn(" in r and "log_recovery_rail" in r
        no_tenant   = ('tenantId:' not in r and '.tenantId' not in r
                       and "'tenantId'" not in r and '"tenantId"' not in r)
        no_legacy   = ("ingestEventsBatch" not in r
                       and "cost_event_direct_emit" not in r
                       and "EmitUsageEventOptions" not in r
                       and "EmitCostEventOptions" not in r
                       and "EventEnvelope" not in r
                       and "usageEventId" not in r
                       and "logPayload" not in r)
        failed = []
        if not has_usage:   failed.append("usage.ingestEvent missing")
        if not has_cost:    failed.append("cost.ingestEvent missing")
        if not has_events:  failed.append("events.ingest missing")
        if not has_devgate: failed.append("SDK_DEVELOPMENT env gate missing")
        if not has_rail:    failed.append("never-drop log rail missing")
        if not no_tenant:   failed.append("tenantId leaked (FR-3 violation)")
        if not no_legacy:   failed.append("v0.2 legacy shape leaked")
        if failed:
            print(f"  FAIL  helper {helper}: {', '.join(failed)}")
            fail_count += 1
        else:
            print(f"  PASS  helper {helper}: v0.3.0 ergonomic methods + env-gated rail + FR-3 clean")
            pass_count += 1

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

        # Codex Finding #2 ("TS usage-only / cost-only must NOT reference _moolabsEventId")
        # is obsolete in v0.3: _moolabsEventId is now declared in any branch that needs
        # it as an entityId fallback (when no request_id binding) and is correctly scoped.

        # Per-pattern method-presence checks.
        if tpl.startswith("python-"):
            # v0.3.0 ergonomic methods + entity_id linking. The Codex Finding #1
            # "_moolabs_event_id" shared-id dance is obsolete in v0.3 (Events.Ingest
            # is a single call; entity_id is the cross-lane key, sourced from the
            # bound request_id or a local uuid fallback).
            if pat == "sibling-pair":
                ok = ("emit_event_safe(" in r
                      and "event_type=" in r and "customer_id=" in r and "entity_id=" in r
                      and "meter_slug=" in r and "value=" in r and "spans=[" in r)
                if "emit_usage_event_safe(" in r or "emit_cost_event_safe(" in r:
                    ok = False
                reason = "sibling-pair must use emit_event_safe (single dual-lane call)"
            elif pat == "usage-only":
                ok = ("emit_usage_event_safe(" in r
                      and "meter_slug=" in r and "value=" in r
                      and "emit_event_safe(" not in r and "emit_cost_event_safe(" not in r
                      and "spans=[" not in r)
                reason = "usage-only must call emit_usage_event_safe only (no spans, no other emit)"
            else:  # cost-only
                ok = ("emit_cost_event_safe(" in r and "spans=[" in r
                      and "emit_event_safe(" not in r and "emit_usage_event_safe(" not in r
                      and "meter_slug=" not in r and "value=" not in r)
                reason = "cost-only must call emit_cost_event_safe only (no meter_slug/value, no other emit)"
            # v0.2 top-level kwargs must NOT appear (FR-3 + helper-signature contract).
            no_v2 = ("tenant_id=" not in r and "usage_event_id=" not in r
                     and "subject=" not in r and "quantity=" not in r and " unit=" not in r
                     and "feature_key=" not in r and "attributes=" not in r
                     and "kind=" not in r and " data=" not in r)
            if not ok:
                print(f"  FAIL  {tpl}[{pat}]: {reason}")
                fail_count += 1; continue
            if not no_v2:
                print(f"  FAIL  {tpl}[{pat}]: v0.2 top-level kwarg leaked (tenant_id/usage_event_id/subject/quantity/unit/feature_key=/attributes/kind=/data=)")
                fail_count += 1; continue
        else:
            # v0.3.0 TS framework callsite assertions (parallel to Python).
            if pat == "sibling-pair":
                ok = ("emitEventSafe(" in r
                      and "eventType:" in r and "customerId:" in r and "entityId:" in r
                      and "meterSlug:" in r and "value:" in r and "spans: [" in r)
                if "emitUsageEventSafe(" in r or "emitCostEventSafe(" in r:
                    ok = False
                reason = "sibling-pair must use emitEventSafe (single dual-lane call)"
            elif pat == "usage-only":
                ok = ("emitUsageEventSafe(" in r
                      and "meterSlug:" in r and "value:" in r
                      and "emitEventSafe(" not in r and "emitCostEventSafe(" not in r
                      and "spans: [" not in r)
                reason = "usage-only must call emitUsageEventSafe only (no spans, no other emit)"
            else:  # cost-only
                ok = ("emitCostEventSafe(" in r and "spans: [" in r
                      and "emitEventSafe(" not in r and "emitUsageEventSafe(" not in r
                      and "meterSlug:" not in r and "value:" not in r)
                reason = "cost-only must call emitCostEventSafe only (no meterSlug/value, no other emit)"
            # v0.2 top-level kwargs must NOT appear (FR-3 + helper-signature contract).
            # kind/costMicros are allowed inside spans (4-space indented); we check by
            # specific v0.2 patterns that are unique to the old shape.
            no_v2 = ("tenantId:" not in r and "usageEventId:" not in r
                     and "subject:" not in r and "quantity:" not in r and " unit:" not in r
                     and "featureKey:" not in r and "attributes:" not in r)
            if not ok:
                print(f"  FAIL  {tpl}[{pat}]: {reason}")
                fail_count += 1; continue
            if not no_v2:
                print(f"  FAIL  {tpl}[{pat}]: v0.2 top-level kwarg leaked (tenantId/usageEventId/subject/quantity/unit/featureKey/attributes)")
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

# Planner refuse-to-run gate: example attribution-bindings must satisfy it.
# v0.3.0-rc1 (FR-3): tenant_id is NOT required by the helpers or the planner —
# the SDK derives tenant identity server-side from the API key. consumer_agent
# is optional metadata. customer_id and request_id are the only required keys,
# and they must be bound to non-null source expressions (a `source: null`
# binding is treated as "not bound" by the planner's gate at
# task_planner.py:`missing_or_null` check).
bindings_yaml = suite_root / "examples" / "attribution-bindings.yaml"
import yaml
b = yaml.safe_load(bindings_yaml.read_text())
required = ["customer_id", "request_id"]
bindings = b.get("bindings") or {}
missing_or_null = [
    k for k in required
    if not isinstance(bindings.get(k), dict)
       or bindings[k].get("source") is None
]
if missing_or_null:
    print(f"  FAIL  examples/attribution-bindings.yaml: missing or null source for required keys {missing_or_null}")
    fail_count += 1
else:
    print(f"  PASS  examples/attribution-bindings.yaml satisfies planner refuse-to-run gate (v0.3 FR-3)")
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
