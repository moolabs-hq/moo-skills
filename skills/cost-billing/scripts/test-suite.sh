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
    import yaml
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
    "slugs_import_path": "app.services.moolabs.slugs_billing",
    "helper_import_path": "app.services.moolabs_client",
    "emission_guard": None,
    "attribution_imports": [],
    "event_type_const": "EVENT_TYPE_COMPLETION_DELIVERED",
    "meter_slug_const": "METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED",
    "feature_key_const": "FEATURE_KEY_RECOMMENDATION",
    "span_type_const": "SPAN_TYPE_LLM_TOKENS",
    "provider_const": None,
}
sources = {"tenant_id":"req.state.tid","request_id":"req.state.rid","customer_id":"req.state.cid",
           "consumer_agent":None,"feature_key":None,"entity_id":"req.state.entity_id"}
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
    "env_config": {
        "mode": "modify",
        "settings_import_path": "app.config",
        "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
        "stub_emit_path": None,
    },
}
for helper in ["python-moolabs-client.py.j2", "typescript-moolabs-client.ts.j2"]:
    # TS uses @/-prefixed settings import path; Python uses dotted module path.
    # Override env_config for TS so that has_get_settings assertion matches.
    if helper.startswith("typescript"):
        ts_env_config = {
            "mode": "modify",
            "settings_import_path": "@/config",
            "api_key_accessor": "getSettings().moolabsApiKey",
            "stub_emit_path": None,
        }
        render_ctx = {**helper_ctx, "env_config": ts_env_config}
    else:
        render_ctx = helper_ctx
    try:
        r = env.get_template(helper).render(**render_ctx)
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
        # Phase 1.7 env-wire: helper imports from get_settings() instead of
        # direct os.environ / strategy-branched fetches.
        has_get_settings = "from app.config import get_settings" in r
        # Phase 1.7 negative-leakage: NO strategy-branched fetches.
        no_strategy_branches = (
            "import boto3" not in r and
            "from google.cloud import secretmanager" not in r and
            "import hvac" not in r and
            "subprocess.run" not in r  # 1Password CLI
        )
        # Phase 1.7 _resolve_api_key reads via accessor, not os.environ direct
        no_direct_environ_resolve = "os.environ.get(\"MOOLABS_API_KEY\")" not in r
        failed = []
        if not has_usage:   failed.append("usage.ingest_event missing")
        if not has_cost:    failed.append("cost.ingest_event missing")
        if not has_events:  failed.append("events.ingest missing")
        if not has_devgate: failed.append("SDK_DEVELOPMENT env gate missing")
        if not has_rail:    failed.append("never-drop log rail missing")
        if not no_tenant:   failed.append("tenant_id leaked (FR-3 violation)")
        if not no_legacy:   failed.append("v0.2 legacy shape leaked")
        if not has_get_settings:        failed.append("env_config get_settings import missing")
        if not no_strategy_branches:    failed.append("v0.2 strategy branch leaked (boto3/google/hvac/op)")
        if not no_direct_environ_resolve: failed.append("os.environ direct read leaked")
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
        # Phase 1.7 env-wire assertions.
        has_get_settings = "from '@/" in r or "from \"@/" in r
        has_settings_import = "getSettings" in r
        no_strategy_branches = (
            "@aws-sdk/client-secrets-manager" not in r and
            "@google-cloud/secret-manager" not in r and
            "'node-vault'" not in r and
            'vault.read(' not in r
        )
        no_direct_process_env_resolve = "process.env.MOOLABS_API_KEY" not in r
        failed = []
        if not has_usage:   failed.append("usage.ingestEvent missing")
        if not has_cost:    failed.append("cost.ingestEvent missing")
        if not has_events:  failed.append("events.ingest missing")
        if not has_devgate: failed.append("SDK_DEVELOPMENT env gate missing")
        if not has_rail:    failed.append("never-drop log rail missing")
        if not no_tenant:   failed.append("tenantId leaked (FR-3 violation)")
        if not no_legacy:   failed.append("v0.2 legacy shape leaked")
        if not has_get_settings:           failed.append("env_config import path missing")
        if not has_settings_import:        failed.append("getSettings() not referenced")
        if not no_strategy_branches:       failed.append("v0.2 TS strategy branch leaked")
        if not no_direct_process_env_resolve: failed.append("process.env direct leaked")
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
# Go uses internal/config import path style; override env_config for Phase 1.7 assertions.
go_helper_ctx = {**helper_ctx, "env_config": {
    "mode": "modify",
    "settings_import_path": "internal/config",
    "api_key_accessor": "config.Get().MoolabsAPIKey",
    "stub_emit_path": None,
}}
try:
    r = env.get_template("go-moolabs-client.go.j2").render(**go_helper_ctx)
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
    # Phase 1.7 env-wire assertions for Go.
    has_config_import = 'config "' in r
    has_config_get = "config.Get()" in r
    no_aws_imports = (
        "aws-sdk-go" not in r and
        "secretsmanager" not in r and
        "hashicorp/vault" not in r
    )
    failed = []
    if not has_usage:  failed.append("Usage.IngestEvent missing")
    if not has_cost:   failed.append("Cost.IngestEvent missing")
    if not has_events: failed.append("Events.Ingest missing")
    if not has_devgate:failed.append("SDK_DEVELOPMENT env gate missing")
    if not has_rail:   failed.append("never-drop log rail missing")
    if not no_tenant:  failed.append("TenantID leaked (FR-3 violation)")
    if not no_legacy:  failed.append("v0.2 legacy shape leaked")
    if not has_config_import: failed.append("env_config Go import missing")
    if not has_config_get:    failed.append("config accessor missing")
    if not no_aws_imports:    failed.append("v0.2 Go strategy import leaked")
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

# Stub Settings templates (Phase 1.7 — env-wire)
for stub_tpl in ("python-moolabs-settings.py.j2",
                 "typescript-moolabs-settings.ts.j2",
                 "go-moolabs-settings.go.j2"):
    try:
        r = env.get_template(stub_tpl).render(service_slug="test-svc")
    except Exception as e:
        print(f"  FAIL  stub {stub_tpl}: render error: {e}")
        fail_count += 1
        continue
    if stub_tpl.startswith("python"):
        try:
            compile(r, stub_tpl, "exec")
        except SyntaxError as e:
            print(f"  FAIL  stub {stub_tpl}: py syntax: {e.msg}")
            fail_count += 1
            continue
        if "def get_settings" in r and "moolabs_api_key" in r:
            print(f"  PASS  stub {stub_tpl}: renders + py-compile clean + get_settings present")
            pass_count += 1
        else:
            print(f"  FAIL  stub {stub_tpl}: missing get_settings/moolabs_api_key")
            fail_count += 1
    elif stub_tpl.startswith("typescript"):
        if "export function getSettings" in r and "MOOLABS_API_KEY" in r:
            print(f"  PASS  stub {stub_tpl}: renders + exports getSettings")
            pass_count += 1
        else:
            print(f"  FAIL  stub {stub_tpl}: missing exports")
            fail_count += 1
    else:  # go
        if "func Get()" in r and "MoolabsAPIKey" in r:
            if gofmt:
                with tempfile.NamedTemporaryFile("w", suffix=".go", delete=False) as tf:
                    tf.write(r); tfp = tf.name
                res = subprocess.run([gofmt, "-e", tfp], capture_output=True, text=True)
                Path(tfp).unlink()
                if res.returncode != 0:
                    print(f"  FAIL  stub {stub_tpl}: gofmt: {res.stderr.strip()[:200]}")
                    fail_count += 1
                    continue
            print(f"  PASS  stub {stub_tpl}: renders + Get/MoolabsAPIKey + gofmt-clean")
            pass_count += 1
        else:
            print(f"  FAIL  stub {stub_tpl}: missing Get function")
            fail_count += 1

# Deployment-surface templates (Phase 1.7 — env-wire)
deploy_ctx = {"service_slug": "test-svc"}
for tpl in ("dotenv-moolabs.env.j2", "terraform-moolabs.tf.j2",
            "k8s-secret-moolabs.yaml.j2"):
    try:
        r = env.get_template(tpl).render(**deploy_ctx)
    except Exception as e:
        print(f"  FAIL  deploy {tpl}: render error: {e}")
        fail_count += 1
        continue
    if tpl.endswith(".env.j2") and "MOOLABS_API_KEY=" in r:
        print(f"  PASS  deploy {tpl}")
        pass_count += 1
    elif tpl.endswith(".tf.j2") and 'variable "moolabs_api_key"' in r:
        print(f"  PASS  deploy {tpl}")
        pass_count += 1
    elif tpl.endswith(".yaml.j2") and "kind: Secret" in r and "test-svc-moolabs" in r:
        # Also validate YAML parses
        try:
            yaml.safe_load(r)
            print(f"  PASS  deploy {tpl}")
            pass_count += 1
        except yaml.YAMLError as e:
            print(f"  FAIL  deploy {tpl}: invalid YAML: {e}")
            fail_count += 1
    else:
        print(f"  FAIL  deploy {tpl}: expected content missing")
        fail_count += 1

# Slugs module templates (Phase 1.8 — slugs emission)
slugs_ctx = {
    "product_slug": "billing",
    "generated_at": "2026-06-06T00:00:00+00:00",
    "constants": {
        "EVENT_TYPE": [
            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
            {"name": "CHECKOUT_RECOMMENDATION_DELIVERED",
             "value": "checkout.recommendation.delivered"},
        ],
        "METER_SLUG": [
            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
        ],
        "FEATURE_KEY": [
            {"name": "ASSIGNED", "value": "assigned"},
        ],
        "PROVIDER": [
            {"name": "OPENAI", "value": "openai"},
        ],
        "SPAN_TYPE": [
            {"name": "LLM_TOKENS", "value": "llm-tokens"},
        ],
    },
}

slugs_python_tpl = "slugs-python.j2"
try:
    r = env.get_template(slugs_python_tpl).render(**slugs_ctx)
except Exception as e:
    print(f"  FAIL  slugs {slugs_python_tpl}: render error: {e}")
    fail_count += 1
else:
    has_doc_header = "DO NOT EDIT" in r and "billing" in r
    has_event_type_const = "EVENT_TYPE_SEAT_ASSIGNED: str = \"seat.assigned\"" in r
    has_meter_slug_const = "METER_SLUG_SEAT_ASSIGNED: str = \"seat.assigned\"" in r
    has_provider_const = "PROVIDER_OPENAI: str = \"openai\"" in r
    has_span_type_const = "SPAN_TYPE_LLM_TOKENS: str = \"llm-tokens\"" in r
    # py-compile check
    try:
        compile(r, slugs_python_tpl, "exec")
        py_ok = True
    except SyntaxError as e:
        print(f"  FAIL  slugs {slugs_python_tpl}: py syntax: {e.msg}")
        py_ok = False
        fail_count += 1
    if py_ok and has_doc_header and has_event_type_const and has_meter_slug_const \
            and has_provider_const and has_span_type_const:
        print(f"  PASS  slugs {slugs_python_tpl}: renders + py-compile + all 5 categories present")
        pass_count += 1
    elif py_ok:
        missing = []
        if not has_doc_header: missing.append("doc-header/product_slug")
        if not has_event_type_const: missing.append("EVENT_TYPE constant")
        if not has_meter_slug_const: missing.append("METER_SLUG constant")
        if not has_provider_const: missing.append("PROVIDER constant")
        if not has_span_type_const: missing.append("SPAN_TYPE constant")
        print(f"  FAIL  slugs {slugs_python_tpl}: missing {', '.join(missing)}")
        fail_count += 1

slugs_ts_tpl = "slugs-typescript.j2"
try:
    r = env.get_template(slugs_ts_tpl).render(**slugs_ctx)
except Exception as e:
    print(f"  FAIL  slugs {slugs_ts_tpl}: render error: {e}")
    fail_count += 1
else:
    has_doc = "DO NOT EDIT" in r and "billing" in r
    has_event = 'export const EVENT_TYPE_SEAT_ASSIGNED = "seat.assigned"' in r
    has_meter = 'export const METER_SLUG_SEAT_ASSIGNED = "seat.assigned"' in r
    has_provider = 'export const PROVIDER_OPENAI = "openai"' in r
    has_span = 'export const SPAN_TYPE_LLM_TOKENS = "llm-tokens"' in r
    has_as_const = "as const" in r  # TS literal type annotation
    if has_doc and has_event and has_meter and has_provider and has_span and has_as_const:
        print(f"  PASS  slugs {slugs_ts_tpl}: renders + 5 categories + as-const annotations")
        pass_count += 1
    else:
        missing = []
        if not has_doc: missing.append("doc-header")
        if not has_event: missing.append("EVENT_TYPE")
        if not has_meter: missing.append("METER_SLUG")
        if not has_provider: missing.append("PROVIDER")
        if not has_span: missing.append("SPAN_TYPE")
        if not has_as_const: missing.append("as-const annotation")
        print(f"  FAIL  slugs {slugs_ts_tpl}: missing {', '.join(missing)}")
        fail_count += 1

slugs_go_tpl = "slugs-go.j2"
try:
    r = env.get_template(slugs_go_tpl).render(**slugs_ctx)
except Exception as e:
    print(f"  FAIL  slugs {slugs_go_tpl}: render error: {e}")
    fail_count += 1
else:
    has_doc = "DO NOT EDIT" in r and "billing" in r
    has_package = "package moolabsslugs_billing" in r
    has_event = 'EVENT_TYPE_SEAT_ASSIGNED = "seat.assigned"' in r
    has_meter = 'METER_SLUG_SEAT_ASSIGNED = "seat.assigned"' in r
    has_provider = 'PROVIDER_OPENAI = "openai"' in r
    has_span = 'SPAN_TYPE_LLM_TOKENS = "llm-tokens"' in r
    if not (has_doc and has_package and has_event and has_meter and has_provider and has_span):
        missing = []
        if not has_doc: missing.append("doc-header")
        if not has_package: missing.append("package declaration")
        if not has_event: missing.append("EVENT_TYPE constant")
        if not has_meter: missing.append("METER_SLUG constant")
        if not has_provider: missing.append("PROVIDER constant")
        if not has_span: missing.append("SPAN_TYPE constant")
        print(f"  FAIL  slugs {slugs_go_tpl}: missing {', '.join(missing)}")
        fail_count += 1
    elif gofmt:
        with tempfile.NamedTemporaryFile("w", suffix=".go", delete=False) as tf:
            tf.write(r); tfp = tf.name
        res = subprocess.run([gofmt, "-e", tfp], capture_output=True, text=True)
        Path(tfp).unlink()
        if res.returncode != 0:
            print(f"  FAIL  slugs {slugs_go_tpl}: gofmt: {res.stderr.strip()[:200]}")
            fail_count += 1
        else:
            print(f"  PASS  slugs {slugs_go_tpl}: renders + 5 categories + gofmt-clean")
            pass_count += 1
    else:
        print(f"  PASS-no-gofmt  slugs {slugs_go_tpl}: structural check only (gofmt absent)")
        pass_count += 1

# Per-callsite template renders × all 3 patterns
for tpl in templates:
    for pat in patterns:
        entry = {**entry_base, "pattern": pat}
        if tpl.startswith("typescript-"):
            entry["slugs_import_path"] = "@/services/moolabs/slugs_billing"
            entry["helper_import_path"] = "@/services/moolabs-client"
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

        # TS: every async helper call must be awaited at the callsite. All three
        # helpers are Promise-returning (`emitUsageEventSafe`, `emitCostEventSafe`,
        # `emitEventSafe`). A missing `await` produces a dangling Promise — the
        # SDK call still fires but the caller doesn't wait, so any error handling
        # (env-gated throw OR structured-log rail) races against the next handler
        # statement. The previous check only covered emitCostEventSafe; extended
        # to all three so a future template edit that forgets await fails loudly.
        if tpl.startswith("typescript-"):
            await_fail = False
            for fn in ("emitUsageEventSafe", "emitCostEventSafe", "emitEventSafe"):
                if f"{fn}(" in r and f"await {fn}(" not in r:
                    print(f"  FAIL  {tpl}[{pat}]: {fn} is async; callsite missing await")
                    fail_count += 1
                    await_fail = True
                    break
            if await_fail:
                continue

        if tpl.startswith("python-"):
            # Phase C slugs assertions (Python)
            has_slugs_import = "from app.services.moolabs.slugs_billing import" in r
            has_event_type_const = "event_type=EVENT_TYPE_COMPLETION_DELIVERED" in r
            has_meter_slug_const = ("meter_slug=METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED" in r
                                    or pat == "cost-only")
            no_event_type_literal = 'event_type="completion.delivered"' not in r
            no_meter_slug_literal = 'meter_slug="checkout.recommendation.delivered"' not in r
            if not has_slugs_import:
                print(f"  FAIL  {tpl}[{pat}]: Phase C slugs import missing")
                fail_count += 1; continue
            if not has_event_type_const:
                print(f"  FAIL  {tpl}[{pat}]: event_type_const not rendered")
                fail_count += 1; continue
            if not has_meter_slug_const:
                print(f"  FAIL  {tpl}[{pat}]: meter_slug_const not rendered")
                fail_count += 1; continue
            if not no_event_type_literal:
                print(f"  FAIL  {tpl}[{pat}]: event_type STRING LITERAL leaked")
                fail_count += 1; continue
            if not no_meter_slug_literal:
                print(f"  FAIL  {tpl}[{pat}]: meter_slug STRING LITERAL leaked")
                fail_count += 1; continue

        if tpl.startswith("typescript-"):
            # Phase C slugs assertions (TS)
            has_slugs_import = "from '@/services/moolabs/slugs_billing'" in r
            has_event_type_const = "eventType: EVENT_TYPE_COMPLETION_DELIVERED" in r
            has_meter_slug_const = ("meterSlug: METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED" in r
                                    or pat == "cost-only")
            no_event_type_literal = ("eventType: 'completion.delivered'" not in r
                                      and 'eventType: "completion.delivered"' not in r)
            no_meter_slug_literal = ("meterSlug: 'checkout.recommendation.delivered'" not in r
                                     and 'meterSlug: "checkout.recommendation.delivered"' not in r)
            if not has_slugs_import:
                print(f"  FAIL  {tpl}[{pat}]: Phase C TS slugs import missing")
                fail_count += 1; continue
            if not has_event_type_const:
                print(f"  FAIL  {tpl}[{pat}]: TS event_type_const not rendered")
                fail_count += 1; continue
            if not has_meter_slug_const:
                print(f"  FAIL  {tpl}[{pat}]: TS meter_slug_const not rendered")
                fail_count += 1; continue
            if not no_event_type_literal:
                print(f"  FAIL  {tpl}[{pat}]: TS event_type STRING LITERAL leaked")
                fail_count += 1; continue
            if not no_meter_slug_literal:
                print(f"  FAIL  {tpl}[{pat}]: TS meter_slug STRING LITERAL leaked")
                fail_count += 1; continue

        print(f"  PASS  {tpl}[{pat}]")
        pass_count += 1

# Phase D: customer-fixture-env-routing presence check.
# Round 1 review HIGH fix: use suite_root (absolute path from sys.argv[1])
# instead of CWD-relative. Previously, invoking test-suite.sh from any
# directory other than the repo root silently skipped this entire fence.
phase_d_fixture = suite_root / "examples" / "customer-fixture-env-routing"
if phase_d_fixture.is_dir():
    files_required = [
        phase_d_fixture / "customer-repo" / "app" / "settings.py",
        phase_d_fixture / "inventories" / "slug-inventory.yaml",
        phase_d_fixture / "inventories" / "attribution-bindings.yaml",
        phase_d_fixture / "customer-context" / "04-final.signed.yaml",
    ]
    missing = [str(f) for f in files_required if not f.exists()]
    if missing:
        print(f"  FAIL  phase-d fixture: missing {missing}")
        fail_count += 1
    else:
        # Validate slug-inventory.yaml round-trips
        try:
            import yaml
            data = yaml.safe_load(files_required[1].read_text())
            assert "products" in data and len(data["products"]) == 1
            assert data["products"][0]["product_slug"] == "billing"
            print(f"  PASS  phase-d fixture: all 4 files present + slug-inventory parses")
            pass_count += 1
        except Exception as e:
            print(f"  FAIL  phase-d fixture: parse error: {e}")
            fail_count += 1
else:
    # Fixture not present — skip (not a failure if Phase D hasn't merged)
    pass

# customer-fixture-centralized-infra: regression guard for PR #531 root
# cause. Verifies the scanner now detects BOTH service-scope surfaces
# (under services/moo-arc/) AND repo-scope surfaces (under
# infrastructure/terraform/). Without the fix, the repo-scope terraform
# files would be invisible.
centralized = suite_root / "examples" / "customer-fixture-centralized-infra"
if centralized.is_dir():
    try:
        scan_scripts_dir = str(suite_root / "discovery" / "scripts")
        sys.path.insert(0, scan_scripts_dir)
        # Re-import path inside this block so the import is local + cleanup-safe.
        import env_loader_scan as _els  # noqa: E402
        repo_root = centralized / "customer-repo"
        service = {"slug": "moo-arc", "root": "services/moo-arc", "language": "python"}
        entry = _els._service_entry(
            repo_root, service, repo_root / "services" / "moo-arc", catalog=[]
        )
        scopes = {(s["kind"], s["scope"]) for s in entry["deployment_surfaces"]}
        # Service-scope (must be detected)
        expected_service = {
            ("dotenv_example", "service"),
            ("dockerfile", "service"),
            ("docker-compose", "service"),
        }
        # Repo-scope (PR #531 reproducer — MUST be detected for fix to hold)
        expected_repo = {("terraform", "repo")}
        missing_service = expected_service - scopes
        missing_repo = expected_repo - scopes
        if missing_service:
            print(f"  FAIL  centralized-infra fixture: missing service-scope {missing_service}")
            fail_count += 1
        elif missing_repo:
            print(f"  FAIL  centralized-infra fixture: missing repo-scope {missing_repo} "
                  f"— PR #531 regression!")
            fail_count += 1
        elif entry["infra_discovery_gap"]:
            print(f"  FAIL  centralized-infra fixture: infra_discovery_gap should be False "
                  f"(terraform + dockerfile found)")
            fail_count += 1
        else:
            print(f"  PASS  centralized-infra fixture: service+repo scopes both detected, "
                  f"no gap flag")
            pass_count += 1
        sys.path.remove(scan_scripts_dir)
    except Exception as e:
        print(f"  FAIL  centralized-infra fixture: scan error: {type(e).__name__}: {e}")
        fail_count += 1
else:
    # Fixture not present — skip.
    pass

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
