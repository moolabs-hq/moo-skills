# Cost-billing env-routing + slugs — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Phase A's env-routing-inventory.yaml into the actual codemod emission. The generated per-service helper now reads `MOOLABS_API_KEY` via the customer's existing settings layer (pydantic-settings / dotenv / viper / etc.) instead of the v0.2-era strategy-branched fetches. Customers with unrecognized config get a generated stub Settings file to merge. Deployment-surface stubs (`.env.example` line, Terraform variable file, k8s Secret manifest) emitted as new files only — never modify existing infra.

**Architecture:** New `instrument/scripts/config_wire.py` (Phase 1.7) reads `env-routing-inventory.yaml` and produces a per-service `config-wiring-plan.yaml` carrying the exact `settings_import_path` + `api_key_accessor` expression each helper template should render. Three existing helper templates (Python/TS/Go) get Jinja branches: `modify` mode imports from the customer's existing module; `stub` mode imports from a generated `moolabs_settings.{py,ts,go}` file (three new templates). Three new deployment-surface templates emit `.env.example` lines, `moolabs.tf`, and `secret-moolabs.yaml` as new files. `task_planner.py` consumes `config-wiring-plan.yaml` and emits the per-service env-wire tasks. Phase 7 of `test-suite.sh` gains four new render assertions for the new helper shape + negative-leakage guards against v0.2 strategy branches.

**Tech Stack:** Python 3.10+ (existing); stdlib `unittest` (existing); Jinja2 for templates (existing); PyYAML for inventory reads, hand-rolled emit for outputs (matches sdk_snapshot.py / env_loader_scan.py); bash test driver `test-suite.sh` Phase 8 auto-discovers `test_*.py` (existing).

**Spec:** `docs/superpowers/specs/2026-06-06-cost-billing-env-routing-and-slugs-design.md`

**Branch:** `spec/cost-billing-phase-b-env-wire` (off `main` at PR #3's merge commit).

**Phase A artifacts already on main** (consumed by Phase B):
- `shared/assets/env-loader-patterns.yaml` (recognition catalog)
- `discovery/scripts/env_loader_scan.py` (produces `env-routing-inventory.yaml`)
- `discovery/scripts/slug_inventory.py` (Phase C consumer; ignore here)
- `bootstrap-team-engineer` Q14b + schema fields
- 67/67 smoke baseline

**Out of scope for Phase B** (separate plans):
- Phase C: instrument slugs emission (slugs Jinja templates + framework callsite import-instead-of-literal updates)
- Phase D: e2e fixture + adversarial-review tuning
- Phase B follow-up: fix the pre-existing backslash YAML escape bugs in attribution_discovery.py / task_planner.py / sdk_snapshot.py (from Phase A review's sibling search)

---

## File Structure (Phase B)

**Create:**
- `skills/cost-billing/instrument/scripts/config_wire.py` — Phase 1.7 orchestrator
- `skills/cost-billing/instrument/scripts/test_config_wire.py` — unit tests
- `skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-settings.py.j2` — stub Settings template (Python)
- `skills/cost-billing/instrument/assets/codemod-templates/typescript-moolabs-settings.ts.j2` — stub Settings template (TS)
- `skills/cost-billing/instrument/assets/codemod-templates/go-moolabs-settings.go.j2` — stub Settings template (Go)
- `skills/cost-billing/instrument/assets/codemod-templates/dotenv-moolabs.env.j2` — `.env.example` snippet
- `skills/cost-billing/instrument/assets/codemod-templates/terraform-moolabs.tf.j2` — Terraform variable stub
- `skills/cost-billing/instrument/assets/codemod-templates/k8s-secret-moolabs.yaml.j2` — k8s Secret manifest stub

**Modify:**
- `skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-client.py.j2` — `_resolve_api_key` rewrites via `get_settings()`
- `skills/cost-billing/instrument/assets/codemod-templates/typescript-moolabs-client.ts.j2` — same via `getSettings()`
- `skills/cost-billing/instrument/assets/codemod-templates/go-moolabs-client.go.j2` — same via `config.Get()`
- `skills/cost-billing/instrument/scripts/task_planner.py` — consume `config-wiring-plan.yaml`
- `skills/cost-billing/instrument/SKILL.md` — Phase 1.7 documentation
- `skills/cost-billing/scripts/test-suite.sh` — Phase 7 helper-render assertions for the new shape

**Verify (no changes):**
- `skills/cost-billing/scripts/test-suite.sh` Phase 8 auto-discovers `test_config_wire.py`.

---

## Task 1: Branch off main + baseline verification

**Files:** none (operational)

- [ ] **Step 1: Switch to main + pull**

```bash
git checkout main
git pull origin main
```

Expected: HEAD at PR #3's merge commit (`6258c27` or whatever the merge produced). `git log --oneline -3` should show the merge commit on top.

- [ ] **Step 2: Branch for Phase B**

```bash
git checkout -b spec/cost-billing-phase-b-env-wire
```

- [ ] **Step 3: Confirm smoke baseline**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 67    FAIL: 0`. If anything is failing, stop and investigate.

- [ ] **Step 4: Confirm Phase A artifacts are present**

```bash
ls skills/cost-billing/discovery/scripts/env_loader_scan.py \
   skills/cost-billing/discovery/scripts/slug_inventory.py \
   skills/cost-billing/shared/assets/env-loader-patterns.yaml
```

Expected: all three files exist.

---

## Task 2: config_wire.py skeleton + read env-routing-inventory.yaml

**Files:**
- Create: `skills/cost-billing/instrument/scripts/config_wire.py`
- Create: `skills/cost-billing/instrument/scripts/test_config_wire.py`

- [ ] **Step 1: Write failing tests**

Create `skills/cost-billing/instrument/scripts/test_config_wire.py`:

```python
#!/usr/bin/env python3
"""Unit tests for config_wire.py (Phase 1.7 env-wire orchestrator).

Stdlib unittest; runs in the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config_wire as cw  # noqa: E402


class LoadEnvRoutingInventory(unittest.TestCase):
    def test_load_basic_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "env-routing-inventory.yaml"
            inv.write_text(
                "generated_at: 2026-06-06T00:00:00+00:00\n"
                "granularity: per-service\n"
                "granularity_source: declared\n"
                "services:\n"
                "  - service_slug: payments-api\n"
                "    app_config:\n"
                "      pattern: python-pydantic-settings-v2\n"
                "      file: services/payments-api/app/config.py\n"
                "      line_to_insert: 5\n"
                "      confidence: high\n"
                "      stub_required: false\n"
                "      wire_target:\n"
                "        kind: \"add_pydantic_settings_field\"\n"
                "        field_template: \"moolabs_api_key: SecretStr\"\n"
                "    deployment_surfaces: []\n"
            )
            data = cw.load_env_routing_inventory(inv)
            self.assertEqual(data["granularity"], "per-service")
            self.assertEqual(len(data["services"]), 1)
            self.assertEqual(data["services"][0]["service_slug"], "payments-api")

    def test_load_missing_file_returns_empty(self):
        data = cw.load_env_routing_inventory(Path("/nonexistent/path.yaml"))
        self.assertEqual(data, {"services": []})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3
```

Expected: `ModuleNotFoundError: No module named 'config_wire'`.

- [ ] **Step 3: Create config_wire.py skeleton**

Create `skills/cost-billing/instrument/scripts/config_wire.py`:

```python
#!/usr/bin/env python3
"""Phase 1.7 — env-wire orchestrator for /cost-billing-instrument.

Reads `.moolabs/customer-context/env-routing-inventory.yaml` (produced by
Phase A's env_loader_scan.py) and produces a per-service config-wiring plan
that the helper templates + task_planner consume.

For each service:
  - mode = "modify" when the scanner recognized a config pattern at
    medium+ confidence and stub_required=False
  - mode = "stub" otherwise (low confidence, unrecognized pattern, OR
    deployment-surface only)

The plan output specifies:
  - settings_import_path: where the helper template imports get_settings from
  - api_key_accessor: the exact expression that reads the key
  - stub_emit: when mode=="stub", the path of the stub Settings file to emit
  - deployment_stubs: list of files to emit (.env.example line, terraform
    moolabs.tf, k8s secret-moolabs.yaml)

Usage:
    python config_wire.py \\
        --env-routing-inventory .moolabs/customer-context/env-routing-inventory.yaml \\
        --customer-context-dir .moolabs/customer-context
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Inventory load
# ──────────────────────────────────────────────────────────────────────

def load_env_routing_inventory(path: Path) -> dict:
    """Read env-routing-inventory.yaml. Returns {"services": []} on missing
    file or unreadable YAML so the rest of the pipeline degrades gracefully.
    """
    if not path.exists():
        return {"services": []}
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except ImportError:
        return {"services": []}
    data.setdefault("services", [])
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--env-routing-inventory",
        default=".moolabs/customer-context/env-routing-inventory.yaml",
    )
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    args = ap.parse_args(argv)

    inv = load_env_routing_inventory(Path(args.env_routing_inventory))
    print(
        f"Phase B Task 2 skeleton — loaded {len(inv.get('services', []))} services.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 69    FAIL: 0` (2 new auto-discovered checks: Phase 3 script compile + Phase 8 test).

- [ ] **Step 6: Commit**

```bash
git add skills/cost-billing/instrument/scripts/config_wire.py \
        skills/cost-billing/instrument/scripts/test_config_wire.py
git commit -m "feat(cost-billing/instrument): config_wire.py skeleton + inventory loader

Phase 1.7 orchestrator that consumes env-routing-inventory.yaml from
Phase A and produces a per-service config-wiring plan for the helper
templates + task_planner.

load_env_routing_inventory() returns {\"services\": []} on missing file
or absent PyYAML — downstream pipeline degrades gracefully per the same
pattern as slug_inventory.py.

Stdlib unittest, auto-discovered by test-suite.sh Phase 8. Smoke 69/69."
```

---

## Task 3: Python wire-target dispatch + api_key_accessor derivation

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/test_config_wire.py`
- Modify: `skills/cost-billing/instrument/scripts/config_wire.py`

- [ ] **Step 1: Append failing Python dispatch tests**

Append to `test_config_wire.py` (before `if __name__ == "__main__"`):

```python
class PythonWireTargetDispatch(unittest.TestCase):
    """Derive settings_import_path + api_key_accessor for each recognized
    Python pattern. The helper template's _resolve_api_key() will render
    these verbatim."""

    def test_pydantic_settings_v2(self):
        service = {
            "service_slug": "payments-api",
            "app_config": {
                "pattern": "python-pydantic-settings-v2",
                "file": "services/payments-api/app/config.py",
                "line_to_insert": 5,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_pydantic_settings_field",
                                "field_template": "moolabs_api_key: SecretStr"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        # settings module derived from file path: services/payments-api/app/config.py
        # → import via app.config
        self.assertEqual(plan["settings_import_path"], "app.config")
        # pydantic-settings v2 uses SecretStr — extracted via .get_secret_value()
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )

    def test_pydantic_v1_settings(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-pydantic-v1-settings",
                "file": "svc/app/settings.py",
                "line_to_insert": 3,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_pydantic_settings_field",
                                "field_template": "moolabs_api_key: SecretStr"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["settings_import_path"], "app.settings")

    def test_python_decouple(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-decouple",
                "file": "svc/app/config.py",
                "line_to_insert": 10,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_decouple_line",
                                "line_template": "MOOLABS_API_KEY = config('MOOLABS_API_KEY')"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        # decouple exposes module-level constants — accessor is direct import
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")

    def test_python_dotenv_os_getenv(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-dotenv-os-getenv",
                "file": "svc/app/config.py",
                "line_to_insert": 8,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_os_getenv_line",
                                "line_template": "MOOLABS_API_KEY = os.getenv(\"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")
```

- [ ] **Step 2: Run — must FAIL (AttributeError on plan_service_env_wire)**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -5
```

Expected: `AttributeError: module 'config_wire' has no attribute 'plan_service_env_wire'`.

- [ ] **Step 3: Implement plan_service_env_wire (Python branch)**

In `config_wire.py`, ADD above `main()`:

```python
# ──────────────────────────────────────────────────────────────────────
# Per-service plan derivation
# ──────────────────────────────────────────────────────────────────────

# Maps each pattern_id → (mode-when-recognized, accessor template).
# Accessor template uses {{settings_call}} as a placeholder for the language-
# specific get_settings() call site.
_PYTHON_PATTERN_ACCESSORS = {
    "python-pydantic-settings-v2": "get_settings().moolabs_api_key.get_secret_value()",
    "python-pydantic-v1-settings": "get_settings().moolabs_api_key.get_secret_value()",
    "python-decouple":            "MOOLABS_API_KEY",
    "python-dotenv-os-getenv":    "MOOLABS_API_KEY",
}


def _python_settings_import_path(file_path: str) -> str:
    """Derive the Python import path for the customer's settings module.

    Convention:
      services/<svc>/<pkg>/config.py   → <pkg>.config
      svc/app/settings.py              → app.settings
      packages/config/settings.py      → packages.config.settings
    """
    parts = file_path.split("/")
    # Strip leading "services/<svc>/" if present
    if len(parts) >= 2 and parts[0] == "services":
        parts = parts[2:]
    # Drop the .py extension from the final segment
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def plan_service_env_wire(service: dict, language: str) -> dict:
    """Derive the per-service env-wiring plan from an inventory entry.

    Returns:
        {
            "service_slug": str,
            "mode": "modify" | "stub",
            "settings_import_path": str,
            "api_key_accessor": str,
            "stub_emit_path": str | None,  # only when mode == "stub"
        }
    """
    app_config = service.get("app_config") or {}
    pattern = app_config.get("pattern", "unrecognized")
    stub_required = bool(app_config.get("stub_required", True))

    if stub_required or pattern == "unrecognized":
        # Stub mode — landed in Task 5.
        return {
            "service_slug": service.get("service_slug", ""),
            "mode": "stub",
            "settings_import_path": "",
            "api_key_accessor": "",
            "stub_emit_path": None,
        }

    if language == "python":
        accessor = _PYTHON_PATTERN_ACCESSORS.get(pattern)
        if accessor is None:
            return {
                "service_slug": service.get("service_slug", ""),
                "mode": "stub",
                "settings_import_path": "",
                "api_key_accessor": "",
                "stub_emit_path": None,
            }
        import_path = _python_settings_import_path(app_config.get("file", ""))
        return {
            "service_slug": service.get("service_slug", ""),
            "mode": "modify",
            "settings_import_path": import_path,
            "api_key_accessor": accessor,
            "stub_emit_path": None,
        }

    # Other languages handled in Task 4.
    return {
        "service_slug": service.get("service_slug", ""),
        "mode": "stub",
        "settings_import_path": "",
        "api_key_accessor": "",
        "stub_emit_path": None,
    }
```

- [ ] **Step 4: Run — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/scripts/config_wire.py \
        skills/cost-billing/instrument/scripts/test_config_wire.py
git commit -m "feat(cost-billing/instrument): config_wire Python wire-target dispatch

plan_service_env_wire() returns {mode, settings_import_path,
api_key_accessor, stub_emit_path} per service. Python branch handles
4 patterns:
- python-pydantic-settings-v2  → get_settings().moolabs_api_key.get_secret_value()
- python-pydantic-v1-settings  → same (both use SecretStr)
- python-decouple              → MOOLABS_API_KEY (module-level constant)
- python-dotenv-os-getenv      → MOOLABS_API_KEY (module-level constant)

_python_settings_import_path converts file paths like
services/<svc>/app/config.py → app.config (helper template renders the
import verbatim).

Low-confidence / unrecognized / stub_required services dispatch to
mode='stub' (Task 5 lands the stub generation). TS + Go branches land
in Task 4."
```

---

## Task 4: TypeScript + Go wire-target dispatch

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/test_config_wire.py`
- Modify: `skills/cost-billing/instrument/scripts/config_wire.py`

- [ ] **Step 1: Append failing TS + Go tests**

Append to `test_config_wire.py` (before `if __name__`):

```python
class TypeScriptWireTargetDispatch(unittest.TestCase):
    def test_zod_env_schema(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "ts-zod-env-schema",
                "file": "src/env.ts",
                "line_to_insert": 5,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_zod_field",
                                "field_template": "MOOLABS_API_KEY: z.string().min(1)"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "modify")
        # Convention: TS env modules export via @/-aliased path
        self.assertEqual(plan["settings_import_path"], "@/env")
        self.assertEqual(plan["api_key_accessor"], "env.MOOLABS_API_KEY")

    def test_process_env_direct(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "ts-process-env-direct",
                "file": "src/config.ts",
                "line_to_insert": 12,
                "confidence": "medium",
                "stub_required": False,
                "wire_target": {"kind": "add_process_env_line",
                                "line_template": "export const MOOLABS_API_KEY = process.env.MOOLABS_API_KEY ?? \"\";"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["settings_import_path"], "@/config")
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")

    def test_env_var_library(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "ts-env-var-library",
                "file": "src/env.ts",
                "line_to_insert": 8,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_env_var_line",
                                "line_template": "export const MOOLABS_API_KEY = env.get(\"MOOLABS_API_KEY\").required().asString();"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")


class GoWireTargetDispatch(unittest.TestCase):
    def test_viper(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "go-viper",
                "file": "internal/config/config.go",
                "line_to_insert": 14,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_viper_bindenv",
                                "line_template": "viper.BindEnv(\"moolabs_api_key\", \"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "modify")
        # Go convention: import the package containing the config struct
        self.assertEqual(plan["settings_import_path"], "internal/config")
        # viper uses GetString("moolabs_api_key")
        self.assertEqual(plan["api_key_accessor"], 'viper.GetString("moolabs_api_key")')

    def test_envconfig(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "go-envconfig",
                "file": "internal/config/config.go",
                "line_to_insert": 6,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_envconfig_field",
                                "field_template": "MoolabsAPIKey string `envconfig:\"MOOLABS_API_KEY\"`"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["settings_import_path"], "internal/config")
        # envconfig uses struct field — accessor via config.Get().MoolabsAPIKey
        self.assertEqual(plan["api_key_accessor"], "config.Get().MoolabsAPIKey")

    def test_go_os_getenv(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "go-os-getenv",
                "file": "internal/config/config.go",
                "line_to_insert": 5,
                "confidence": "medium",
                "stub_required": False,
                "wire_target": {"kind": "add_os_getenv_line",
                                "line_template": "MoolabsAPIKey := os.Getenv(\"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"], 'os.Getenv("MOOLABS_API_KEY")')
```

- [ ] **Step 2: Run — must FAIL (TS + Go branches fall through to stub)**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -8
```

Expected: TS + Go tests fail because the current implementation only handles `language == "python"`.

- [ ] **Step 3: Extend plan_service_env_wire**

In `config_wire.py`, ADD above `plan_service_env_wire`:

```python
_TS_PATTERN_ACCESSORS = {
    "ts-zod-env-schema":      "env.MOOLABS_API_KEY",
    "ts-process-env-direct":  "MOOLABS_API_KEY",
    "ts-env-var-library":     "MOOLABS_API_KEY",
}

_GO_PATTERN_ACCESSORS = {
    "go-viper":      'viper.GetString("moolabs_api_key")',
    "go-envconfig":  "config.Get().MoolabsAPIKey",
    "go-os-getenv":  'os.Getenv("MOOLABS_API_KEY")',
}


def _ts_settings_import_path(file_path: str) -> str:
    """Derive the TS import path. Convention: `@/<modulepath>` aliased to
    the source root (matches Next.js / many React app conventions).

      src/env.ts            → @/env
      src/config.ts         → @/config
      services/<svc>/src/env.ts → @/env  (service-relative)
    """
    parts = file_path.split("/")
    # Strip leading "services/<svc>/"
    if len(parts) >= 2 and parts[0] == "services":
        parts = parts[2:]
    # Strip leading "src/" — it's the TS source root
    if parts and parts[0] == "src":
        parts = parts[1:]
    # Drop .ts / .tsx / .mts
    if parts:
        last = parts[-1]
        for ext in (".ts", ".tsx", ".mts"):
            if last.endswith(ext):
                parts[-1] = last[: -len(ext)]
                break
    return "@/" + "/".join(parts) if parts else "@/env"


def _go_settings_import_path(file_path: str) -> str:
    """Derive the Go import path. Convention: drop the filename and emit
    the remaining package path as-is.

      internal/config/config.go  → internal/config
      services/<svc>/internal/config/config.go → internal/config
    """
    parts = file_path.split("/")
    if len(parts) >= 2 and parts[0] == "services":
        parts = parts[2:]
    # Drop the trailing filename if it ends in .go
    if parts and parts[-1].endswith(".go"):
        parts = parts[:-1]
    return "/".join(parts) if parts else "internal/config"
```

Then MODIFY the language dispatch block inside `plan_service_env_wire()`. Replace:

```python
    if language == "python":
        accessor = _PYTHON_PATTERN_ACCESSORS.get(pattern)
        if accessor is None:
            return {
                "service_slug": service.get("service_slug", ""),
                "mode": "stub",
                "settings_import_path": "",
                "api_key_accessor": "",
                "stub_emit_path": None,
            }
        import_path = _python_settings_import_path(app_config.get("file", ""))
        return {
            "service_slug": service.get("service_slug", ""),
            "mode": "modify",
            "settings_import_path": import_path,
            "api_key_accessor": accessor,
            "stub_emit_path": None,
        }

    # Other languages handled in Task 4.
    return {
        "service_slug": service.get("service_slug", ""),
        "mode": "stub",
        "settings_import_path": "",
        "api_key_accessor": "",
        "stub_emit_path": None,
    }
```

with:

```python
    accessor_map = {
        "python": _PYTHON_PATTERN_ACCESSORS,
        "typescript": _TS_PATTERN_ACCESSORS,
        "go": _GO_PATTERN_ACCESSORS,
    }.get(language)
    if accessor_map is None:
        return {
            "service_slug": service.get("service_slug", ""),
            "mode": "stub",
            "settings_import_path": "",
            "api_key_accessor": "",
            "stub_emit_path": None,
        }
    accessor = accessor_map.get(pattern)
    if accessor is None:
        return {
            "service_slug": service.get("service_slug", ""),
            "mode": "stub",
            "settings_import_path": "",
            "api_key_accessor": "",
            "stub_emit_path": None,
        }
    import_path = {
        "python": _python_settings_import_path,
        "typescript": _ts_settings_import_path,
        "go": _go_settings_import_path,
    }[language](app_config.get("file", ""))
    return {
        "service_slug": service.get("service_slug", ""),
        "mode": "modify",
        "settings_import_path": import_path,
        "api_key_accessor": accessor,
        "stub_emit_path": None,
    }
```

- [ ] **Step 4: Run — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/scripts/config_wire.py \
        skills/cost-billing/instrument/scripts/test_config_wire.py
git commit -m "feat(cost-billing/instrument): config_wire TS + Go wire-target dispatch

3 TS recognizers + 3 Go recognizers:
- ts-zod-env-schema     → env.MOOLABS_API_KEY (named export from @/env)
- ts-process-env-direct → MOOLABS_API_KEY     (module-level export)
- ts-env-var-library    → MOOLABS_API_KEY     (module-level export)
- go-viper              → viper.GetString(\"moolabs_api_key\")
- go-envconfig          → config.Get().MoolabsAPIKey
- go-os-getenv          → os.Getenv(\"MOOLABS_API_KEY\")

_ts_settings_import_path strips src/ + services/<svc>/ + extension → @/<path>.
_go_settings_import_path drops the trailing filename → package path.

Dispatch refactored from per-language if/elif chains to language→accessor-
map lookup (reduces duplication; easier to add a 4th language later)."
```

---

## Task 5: Stub mode + deployment-surface plan

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/test_config_wire.py`
- Modify: `skills/cost-billing/instrument/scripts/config_wire.py`

- [ ] **Step 1: Append failing tests**

Append to `test_config_wire.py`:

```python
class StubModeFallback(unittest.TestCase):
    def test_unrecognized_pattern_triggers_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "unrecognized",
                "confidence": "none",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        # Stub Settings file is emitted at a conventional service-relative path.
        self.assertEqual(plan["stub_emit_path"], "app/services/moolabs_settings.py")
        # Accessor reads from the stub.
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )
        self.assertEqual(plan["settings_import_path"], "app.services.moolabs_settings")

    def test_stub_required_true_overrides_recognized_pattern(self):
        """When confidence is low even for a recognized pattern, stub_required
        triggers the stub fallback to avoid wiring into an uncertain match."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-decouple",
                "file": "svc/app/config.py",
                "confidence": "low",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")

    def test_typescript_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["stub_emit_path"], "src/services/moolabs-settings.ts")

    def test_go_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["stub_emit_path"], "internal/moolabsconfig/settings.go")


class DeploymentSurfacePlan(unittest.TestCase):
    def test_per_surface_emit_paths(self):
        service = {
            "service_slug": "payments-api",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                {"kind": "terraform", "path": "infra/terraform/payments-api/variables.tf",
                 "insert_kind": "variable_block_append"},
                {"kind": "k8s", "path": "infra/k8s/payments-api/deployment.yaml",
                 "insert_kind": "secret_ref_checklist"},
                {"kind": "dotenv_example", "path": "services/payments-api/.env.example",
                 "insert_kind": "line_append"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        self.assertEqual(len(stubs), 3)
        kinds = {s["kind"] for s in stubs}
        self.assertEqual(kinds, {"terraform", "k8s", "dotenv_example"})
        terraform = next(s for s in stubs if s["kind"] == "terraform")
        # Stub file goes ALONGSIDE the existing infra (never modifying it).
        self.assertEqual(terraform["emit_path"],
                         "infra/terraform/payments-api/moolabs.tf")
        k8s = next(s for s in stubs if s["kind"] == "k8s")
        self.assertEqual(k8s["emit_path"],
                         "infra/k8s/payments-api/secret-moolabs.yaml")
        dotenv = next(s for s in stubs if s["kind"] == "dotenv_example")
        # .env.example gets a line APPENDED — same file, not a new one.
        self.assertEqual(dotenv["emit_path"],
                         "services/payments-api/.env.example")
        self.assertEqual(dotenv["mode"], "append")

    def test_dockerfile_emits_checklist_only(self):
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                {"kind": "dockerfile", "path": "services/svc/Dockerfile",
                 "insert_kind": "checklist_only"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        self.assertEqual(len(stubs), 1)
        self.assertEqual(stubs[0]["mode"], "checklist_only")
        self.assertNotIn("emit_path", stubs[0])
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -8
```

Expected: stub tests fail (stub_emit_path always None) AND deployment_stubs tests fail (key not in plan).

- [ ] **Step 3: Add stub + deployment-stubs derivation**

In `config_wire.py`, ADD ABOVE `plan_service_env_wire`:

```python
# Stub-mode emit paths per language. The customer merges this file into
# their own config layer (per the spec's "stub fallback" rule).
_STUB_EMIT_PATHS = {
    "python":     "app/services/moolabs_settings.py",
    "typescript": "src/services/moolabs-settings.ts",
    "go":         "internal/moolabsconfig/settings.go",
}

# Stub-mode accessor (helper imports get_settings from the stub).
_STUB_ACCESSORS = {
    "python":     "get_settings().moolabs_api_key.get_secret_value()",
    "typescript": "getSettings().MOOLABS_API_KEY",
    "go":         "config.Get().MoolabsAPIKey",
}

# Stub-mode import path per language.
_STUB_IMPORT_PATHS = {
    "python":     "app.services.moolabs_settings",
    "typescript": "@/services/moolabs-settings",
    "go":         "internal/moolabsconfig",
}


def _plan_deployment_stubs(surfaces: list[dict]) -> list[dict]:
    """Map each detected deployment surface to a stub-emit plan.

    Per the spec's "deployment-surface stubs" rule:
      - terraform / k8s: emit a NEW file alongside (never modify existing)
      - dotenv_example:  append a single line to the existing file
      - dockerfile:      checklist only (security smell — never auto-edit)
    """
    out: list[dict] = []
    for s in surfaces or []:
        kind = s.get("kind")
        path = s.get("path", "")
        if kind == "terraform":
            # Emit moolabs.tf alongside the detected variables.tf
            dir_path = path.rsplit("/", 1)[0] if "/" in path else "."
            out.append({
                "kind": "terraform",
                "source_path": path,
                "emit_path": f"{dir_path}/moolabs.tf" if dir_path != "." else "moolabs.tf",
                "mode": "new_file",
            })
        elif kind == "k8s":
            dir_path = path.rsplit("/", 1)[0] if "/" in path else "."
            out.append({
                "kind": "k8s",
                "source_path": path,
                "emit_path": f"{dir_path}/secret-moolabs.yaml" if dir_path != "." else "secret-moolabs.yaml",
                "mode": "new_file",
            })
        elif kind == "docker-compose":
            out.append({
                "kind": "docker-compose",
                "source_path": path,
                "emit_path": path,  # appending to existing
                "mode": "append",
            })
        elif kind == "dotenv_example":
            out.append({
                "kind": "dotenv_example",
                "source_path": path,
                "emit_path": path,  # appending to existing
                "mode": "append",
            })
        elif kind == "dockerfile":
            out.append({
                "kind": "dockerfile",
                "source_path": path,
                "mode": "checklist_only",
            })
    return out
```

Then MODIFY `plan_service_env_wire` to:
- Return stub fields when stub mode
- Always populate `deployment_stubs` regardless of mode

Replace the entire function body with:

```python
def plan_service_env_wire(service: dict, language: str) -> dict:
    """Derive the per-service env-wiring plan from an inventory entry."""
    app_config = service.get("app_config") or {}
    pattern = app_config.get("pattern", "unrecognized")
    stub_required = bool(app_config.get("stub_required", True))
    service_slug = service.get("service_slug", "")
    deployment_stubs = _plan_deployment_stubs(service.get("deployment_surfaces") or [])

    if stub_required or pattern == "unrecognized":
        return {
            "service_slug": service_slug,
            "mode": "stub",
            "settings_import_path": _STUB_IMPORT_PATHS.get(language, ""),
            "api_key_accessor": _STUB_ACCESSORS.get(language, ""),
            "stub_emit_path": _STUB_EMIT_PATHS.get(language),
            "deployment_stubs": deployment_stubs,
        }

    accessor_map = {
        "python": _PYTHON_PATTERN_ACCESSORS,
        "typescript": _TS_PATTERN_ACCESSORS,
        "go": _GO_PATTERN_ACCESSORS,
    }.get(language)
    accessor = accessor_map.get(pattern) if accessor_map else None
    if accessor is None:
        return {
            "service_slug": service_slug,
            "mode": "stub",
            "settings_import_path": _STUB_IMPORT_PATHS.get(language, ""),
            "api_key_accessor": _STUB_ACCESSORS.get(language, ""),
            "stub_emit_path": _STUB_EMIT_PATHS.get(language),
            "deployment_stubs": deployment_stubs,
        }
    import_path = {
        "python": _python_settings_import_path,
        "typescript": _ts_settings_import_path,
        "go": _go_settings_import_path,
    }[language](app_config.get("file", ""))
    return {
        "service_slug": service_slug,
        "mode": "modify",
        "settings_import_path": import_path,
        "api_key_accessor": accessor,
        "stub_emit_path": None,
        "deployment_stubs": deployment_stubs,
    }
```

- [ ] **Step 4: Run — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/scripts/config_wire.py \
        skills/cost-billing/instrument/scripts/test_config_wire.py
git commit -m "feat(cost-billing/instrument): config_wire stub mode + deployment-surface plan

Stub mode (when scanner couldn't recognize the customer's pattern OR
stub_required=true on a low-confidence match):
  - Python   → app/services/moolabs_settings.py
  - TS       → src/services/moolabs-settings.ts
  - Go       → internal/moolabsconfig/settings.go

Stub mode always uses a get_settings()-style accessor; the actual stub
Settings file gets emitted by helper templates in Tasks 8-10.

Deployment-stub plan (every mode, modify or stub) maps each detected
surface to:
  - terraform / k8s     → new file alongside existing infra (moolabs.tf,
                          secret-moolabs.yaml)
  - dotenv_example /    → append a line to the existing file
    docker-compose
  - dockerfile          → checklist only (never auto-edit; security smell)"
```

---

## Task 6: config_wire.py YAML emit + main + round-trip test

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/test_config_wire.py`
- Modify: `skills/cost-billing/instrument/scripts/config_wire.py`

- [ ] **Step 1: Append failing tests**

Append to `test_config_wire.py`:

```python
class BuildPlanFromInventory(unittest.TestCase):
    def test_build_plan_one_service(self):
        inventory = {
            "granularity": "per-service",
            "services": [
                {
                    "service_slug": "svc-a",
                    "app_config": {
                        "pattern": "python-pydantic-settings-v2",
                        "file": "svc-a/app/config.py",
                        "stub_required": False,
                    },
                    "deployment_surfaces": [],
                },
            ],
        }
        plan = cw.build_plan(inventory, services_languages={"svc-a": "python"})
        self.assertEqual(len(plan["services"]), 1)
        self.assertEqual(plan["services"][0]["mode"], "modify")

    def test_build_plan_unknown_language_falls_back_to_python(self):
        inventory = {
            "services": [
                {"service_slug": "svc", "app_config": {"pattern": "unrecognized"},
                 "deployment_surfaces": []},
            ],
        }
        plan = cw.build_plan(inventory, services_languages={})
        # No language declared → default to python (Phase A's
        # parse_services_and_granularity makes the same fallback).
        self.assertEqual(plan["services"][0]["mode"], "stub")
        self.assertEqual(
            plan["services"][0]["stub_emit_path"],
            "app/services/moolabs_settings.py",
        )


class YamlEmit(unittest.TestCase):
    def test_emit_yaml_roundtrips(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config-wiring-plan.yaml"
            plan = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "services": [
                    {
                        "service_slug": "svc",
                        "mode": "modify",
                        "settings_import_path": "app.config",
                        "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
                        "stub_emit_path": None,
                        "deployment_stubs": [
                            {"kind": "dotenv_example",
                             "source_path": ".env.example",
                             "emit_path": ".env.example",
                             "mode": "append"},
                        ],
                    },
                ],
            }
            cw.emit_config_wiring_plan_yaml(plan, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(parsed["services"][0]["service_slug"], "svc")
            self.assertEqual(parsed["services"][0]["mode"], "modify")
            self.assertEqual(
                parsed["services"][0]["api_key_accessor"],
                "get_settings().moolabs_api_key.get_secret_value()",
            )

    def test_emit_yaml_handles_backslash_in_accessor(self):
        """Defensive against the Phase A YAML escape bug class. Accessors
        could legitimately contain `\` (regex literals, escape sequences)."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.yaml"
            accessor_with_bs = r'get_settings()["api\nkey"]'
            plan = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "services": [{
                    "service_slug": "svc",
                    "mode": "modify",
                    "settings_import_path": "app.config",
                    "api_key_accessor": accessor_with_bs,
                    "stub_emit_path": None,
                    "deployment_stubs": [],
                }],
            }
            cw.emit_config_wiring_plan_yaml(plan, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["services"][0]["api_key_accessor"],
                accessor_with_bs,
            )
```

- [ ] **Step 2: Run — must FAIL**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -5
```

Expected: `AttributeError: module 'config_wire' has no attribute 'build_plan'`.

- [ ] **Step 3: Add build_plan + emit + flesh out main**

In `config_wire.py`, REPLACE the existing `main()` function and ADD the helpers above it:

```python
# ──────────────────────────────────────────────────────────────────────
# Plan build (orchestrator)
# ──────────────────────────────────────────────────────────────────────

def build_plan(inventory: dict, services_languages: dict[str, str]) -> dict:
    """Orchestrate per-service plan derivation across the whole inventory.

    services_languages: {service_slug: "python" | "typescript" | "go"}.
    When a service slug is absent, defaults to python (matches Phase A's
    parse_services_and_granularity fallback).
    """
    service_plans: list[dict] = []
    for svc in inventory.get("services") or []:
        slug = svc.get("service_slug", "")
        language = services_languages.get(slug, "python")
        service_plans.append(plan_service_env_wire(svc, language))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "services": service_plans,
    }


# ──────────────────────────────────────────────────────────────────────
# YAML emit (hand-rolled, matches Phase A convention; escapes \ AND " per
# the Phase A review bug-class fix)
# ──────────────────────────────────────────────────────────────────────

def _quote(value: str) -> str:
    """Escape backslash THEN double-quote (order matters; reverse would
    double-escape the quote escape). Returns the QUOTED scalar."""
    safe = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{safe}"'


def emit_config_wiring_plan_yaml(plan: dict, dest: Path) -> None:
    lines: list[str] = []
    lines.append(f"generated_at: {plan['generated_at']}")
    if not plan.get("services"):
        lines.append("services: []")
    else:
        lines.append("services:")
        for svc in plan["services"]:
            lines.append(f"  - service_slug: {svc['service_slug']}")
            lines.append(f"    mode: {svc['mode']}")
            lines.append(f"    settings_import_path: {_quote(svc['settings_import_path'])}")
            lines.append(f"    api_key_accessor: {_quote(svc['api_key_accessor'])}")
            if svc.get("stub_emit_path"):
                lines.append(f"    stub_emit_path: {_quote(svc['stub_emit_path'])}")
            else:
                lines.append(f"    stub_emit_path: null")
            stubs = svc.get("deployment_stubs", [])
            if not stubs:
                lines.append(f"    deployment_stubs: []")
            else:
                lines.append(f"    deployment_stubs:")
                for s in stubs:
                    lines.append(f"      - kind: {s['kind']}")
                    lines.append(f"        source_path: {_quote(s['source_path'])}")
                    if "emit_path" in s:
                        lines.append(f"        emit_path: {_quote(s['emit_path'])}")
                    lines.append(f"        mode: {s['mode']}")
    dest.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# Signed-yaml helper (read services + per-service language)
# ──────────────────────────────────────────────────────────────────────

def _read_services_languages(signed_yaml_path: Path) -> dict[str, str]:
    """Return {service_slug: language} from 04-final.signed.yaml.
    Empty dict if the file is missing or PyYAML is absent."""
    if not signed_yaml_path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(signed_yaml_path.read_text()) or {}
    except ImportError:
        return {}
    out: dict[str, str] = {}
    for s in (data.get("integration") or {}).get("services") or []:
        slug = s.get("slug") or s.get("service_slug") or ""
        lang = s.get("language") or "python"
        if slug:
            out[slug] = lang
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--env-routing-inventory",
        default=".moolabs/customer-context/env-routing-inventory.yaml",
    )
    ap.add_argument(
        "--signed-yaml",
        default=".moolabs/chain/04-final.signed.yaml",
        help="path to 04-final.signed.yaml for per-service language lookup",
    )
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    args = ap.parse_args(argv)

    inv = load_env_routing_inventory(Path(args.env_routing_inventory))
    languages = _read_services_languages(Path(args.signed_yaml))
    plan = build_plan(inv, languages)

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "config-wiring-plan.yaml"
    emit_config_wiring_plan_yaml(plan, out_path)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3
```

Expected: `OK` with all tests passing.

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/scripts/config_wire.py \
        skills/cost-billing/instrument/scripts/test_config_wire.py
git commit -m "feat(cost-billing/instrument): config_wire build_plan + YAML emit + CLI

build_plan() orchestrates per-service plan derivation across the whole
inventory. Reads per-service language from 04-final.signed.yaml (matches
Phase A's parse_services_and_granularity fallback to python).

emit_config_wiring_plan_yaml() hand-rolled YAML emitter with the Phase A
review's bug-class fix baked in: _quote() escapes BACKSLASH then quote
(order matters; reverse double-escapes the quote-escape). Two regression
tests guard the escape behavior — one round-trip, one explicit backslash
in accessor.

main() writes .moolabs/customer-context/config-wiring-plan.yaml. Phase B
Task 11 wires task_planner to consume it."
```

---

## Task 7: Python helper template — new get_settings() shape

**Files:**
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-client.py.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (Phase 7 fixture extended for new Jinja vars)

- [ ] **Step 1: Read existing helper template and identify the strategy-branched section**

The existing `_resolve_api_key()` (around lines 50-130) has branches for aws_secrets_manager / gcp_secret_manager / vault / onepassword / env_var / custom. Phase B replaces ALL of these with a single `get_settings()` call.

```bash
grep -n "_resolve_api_key\|sdk_key_location.strategy\|env_config" \
  skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-client.py.j2 | head -20
```

Note the line numbers.

- [ ] **Step 2: Replace the strategy-branched section with the new shape**

Use Edit. Find the existing block from `{% if sdk_key_location.strategy == "aws_secrets_manager" %}import boto3` through the end of `def _resolve_api_key()` (including the close `{% endif %}` for the strategy chain). REPLACE with:

```python
{# Phase 1.7 env-wire (NEW v0.3 env-routing migration):
   env_config.mode               — "modify" | "stub"
   env_config.settings_import_path — e.g. "app.config" or "app.services.moolabs_settings"
   env_config.api_key_accessor   — e.g. "get_settings().moolabs_api_key.get_secret_value()"

   The v0.2-era strategy-branched _resolve_api_key (boto3/hvac/google.cloud)
   is gone. The customer's Settings class is now RESPONSIBLE for resolving
   the secret from whatever store they use (Vault/AWS/etc) — the helper
   just reads `get_settings().moolabs_api_key`.
#}
import structlog
from moolabs import Moolabs

# Import the customer's settings layer (or the generated stub).
from {{ env_config.settings_import_path }} import get_settings

logger = structlog.get_logger(__name__)

# Env-gated strict/lax mode — same var name as the Go helper for cross-language
# parity. Non-empty -> raise (dev mode); unset -> log-rail swallow (prod).
_DEV_ENV_VAR = "SDK_DEVELOPMENT"

{% if telemetry.mode == "brownfield" %}
# Brownfield directive: {{ service_slug }} already has a live TracerProvider.
# Do NOT register a second provider — get spans via
# ``opentelemetry.trace.get_tracer(__name__)`` where needed.
{% endif %}


# ── API-key resolution ──────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _resolve_api_key() -> str:
    """Read MOOLABS_API_KEY from the customer's settings layer.

    The customer's Settings class (or the generated stub at
    {{ env_config.stub_emit_path if env_config.mode == "stub" else env_config.settings_import_path }})
    owns secret resolution — whether it pulls from Vault, AWS Secrets
    Manager, env vars, or a hardcoded value. This helper does not care.
    """
    return {{ env_config.api_key_accessor }}
```

This eliminates the strategy branches AND simplifies the helper.

- [ ] **Step 3: Update Phase 7 smoke fixture to include env_config**

In `skills/cost-billing/scripts/test-suite.sh`, locate the `helper_ctx` block (around line 197) and ADD a new `env_config` variable.

```bash
grep -n "helper_ctx = {" skills/cost-billing/scripts/test-suite.sh
```

Find the helper_ctx dict and ADD the following key/value before the closing brace:

```python
    "env_config": {
        "mode": "modify",
        "settings_import_path": "app.config",
        "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
        "stub_emit_path": None,
    },
```

- [ ] **Step 4: Update the Python helper assertion to verify new shape**

In test-suite.sh Phase 7's Python helper assertion block (after `if helper.startswith("python")`), ADD these checks ALONGSIDE the existing has_usage / has_cost / has_events / has_devgate / has_rail / no_tenant / no_legacy:

```python
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
```

And add to the `failed` accumulation:

```python
        if not has_get_settings:        failed.append("env_config get_settings import missing")
        if not no_strategy_branches:    failed.append("v0.2 strategy branch leaked (boto3/google/hvac/op)")
        if not no_direct_environ_resolve: failed.append("os.environ direct read leaked")
```

- [ ] **Step 5: Render the template + run smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
```

Expected: smoke passes. If it fails on the Python helper render, the template branch logic needs adjusting.

- [ ] **Step 6: Commit**

```bash
git add skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-client.py.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): Python helper reads via get_settings() (Phase 1.7)

The v0.2-era strategy-branched _resolve_api_key (boto3/hvac/google.cloud/
op CLI) is gone. The customer's Settings class now OWNS secret resolution
— whether it pulls from Vault, AWS Secrets Manager, env vars, or a
hardcoded value. The helper just reads
\`{{ env_config.api_key_accessor }}\`.

Two new Jinja variables consumed:
- env_config.settings_import_path  → \`from <path> import get_settings\`
- env_config.api_key_accessor      → the exact accessor expression

config_wire.py (Phase 1.7 orchestrator) derives both from the
env-routing-inventory.yaml's per-service wire_target.

Phase 7 smoke assertions added:
- has_get_settings (positive)
- no_strategy_branches (negative-leakage for boto3/google/hvac/subprocess)
- no_direct_environ_resolve (negative-leakage for os.environ direct)"
```

---

## Task 8: TypeScript helper template — new getSettings() shape

**Files:**
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/typescript-moolabs-client.ts.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (extend env_config + TS helper assertion)

- [ ] **Step 1: Replace the strategy-branched section in TS helper template**

Find the existing `resolveApiKey()` function in the TS template (it has if-branches for aws_secrets_manager / gcp_secret_manager / vault / env_var / custom). REPLACE the entire strategy-branched section with:

```typescript
{# Phase 1.7 env-wire — see Python template's docstring. Same contract. #}
import { Moolabs } from 'moolabs';
import { getSettings } from '{{ env_config.settings_import_path }}';

const DEV_ENV_VAR = 'SDK_DEVELOPMENT';

{% if telemetry.mode == "brownfield" %}
// Brownfield: {{ service_slug }} already has a live TracerProvider. Don't
// register another — get spans via `opentelemetry.trace.getTracer(...)`.
{% endif %}

let _client: Moolabs | null = null;

function resolveApiKey(): string {
  // The customer's Settings layer (or the generated stub at
  // {{ env_config.stub_emit_path if env_config.mode == "stub" else env_config.settings_import_path }})
  // owns secret resolution. This helper just reads the accessor.
  return {{ env_config.api_key_accessor }};
}
```

- [ ] **Step 2: Update test-suite.sh TS assertion block**

In the TS helper assertion section (after `else: # v0.3.0 TS ergonomic-method assertions`), ADD:

```python
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
```

And add to the `failed` block:

```python
        if not has_get_settings:           failed.append("env_config import path missing")
        if not has_settings_import:        failed.append("getSettings() not referenced")
        if not no_strategy_branches:       failed.append("v0.2 TS strategy branch leaked")
        if not no_direct_process_env_resolve: failed.append("process.env direct leaked")
```

- [ ] **Step 3: Render + smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
```

Expected: TS helper renders cleanly + assertions pass.

- [ ] **Step 4: Commit**

```bash
git add skills/cost-billing/instrument/assets/codemod-templates/typescript-moolabs-client.ts.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): TS helper reads via getSettings() (Phase 1.7)

Parallel to Python helper — same env_config Jinja variables; same
removal of v0.2 strategy branches (@aws-sdk/client-secrets-manager,
@google-cloud/secret-manager, node-vault). resolveApiKey() now just
returns the env_config.api_key_accessor expression.

Phase 7 TS assertions added: has_get_settings, has_settings_import,
no_strategy_branches, no_direct_process_env_resolve."
```

---

## Task 9: Go helper template — new config.Get() shape

**Files:**
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/go-moolabs-client.go.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (extend Go helper assertion)

- [ ] **Step 1: Replace the strategy-branched section in Go helper**

Find the Go helper's `resolveApiKey()` (it has strategy branches similar to Python/TS). REPLACE with:

```go
{# Phase 1.7 env-wire — see Python template's docstring. Same contract. #}
package moolabsclient

import (
    "context"
    "fmt"
    "os"
    "sync"

    moolabs "github.com/moolabs/moolabs-go"

    config "{{ env_config.settings_import_path }}"
)

const devEnvVar = "SDK_DEVELOPMENT"

var (
    _cli   *moolabs.Client
    _cliMu sync.Mutex
)

// resolveApiKey reads MOOLABS_API_KEY via the customer's config layer (or
// the generated stub at
// {{ env_config.stub_emit_path if env_config.mode == "stub" else env_config.settings_import_path }}).
// The customer's config struct owns secret resolution.
func resolveApiKey() string {
    return {{ env_config.api_key_accessor }}
}
```

- [ ] **Step 2: Update Go assertion block in test-suite.sh**

In the Go helper assertion block (after `# Go helper: v0.3.0-rc1 unified-ingest shape`), ADD:

```python
    # Phase 1.7 env-wire assertions for Go.
    has_config_import = 'import (' in r and 'config "' in r
    has_config_get = "config.Get()" in r or "viper.GetString" in r or "os.Getenv" in r
    no_aws_imports = (
        "aws-sdk-go" not in r and
        "secretsmanager" not in r and
        "hashicorp/vault" not in r
    )
```

And add to `failed`:

```python
    if not has_config_import: failed.append("env_config Go import missing")
    if not has_config_get:    failed.append("config accessor missing")
    if not no_aws_imports:    failed.append("v0.2 Go strategy import leaked")
```

- [ ] **Step 3: Smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Note: Go helper assertions use `helper_ctx`'s env_config — need to add a separate go_helper_ctx with Go-shaped values. In test-suite.sh, find where the Go helper renders and update the ctx OR overload helper_ctx:

```python
go_helper_ctx = {**helper_ctx, "env_config": {
    "mode": "modify",
    "settings_import_path": "internal/config",
    "api_key_accessor": "config.Get().MoolabsAPIKey",
    "stub_emit_path": None,
}}
```

Then use `go_helper_ctx` for the Go template render call.

- [ ] **Step 4: Commit**

```bash
git add skills/cost-billing/instrument/assets/codemod-templates/go-moolabs-client.go.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): Go helper reads via config.Get() (Phase 1.7)

Parallel to Python/TS — same env_config Jinja variables; same removal of
v0.2 strategy branches (aws-sdk-go, secretsmanager, hashicorp/vault).
resolveApiKey() returns env_config.api_key_accessor verbatim.

Phase 7 Go assertions added. go_helper_ctx overlay added because Go
uses internal/config import path style vs Python's app.config dotted path."
```

---

## Task 10: Stub Settings file templates (Python/TS/Go)

**Files:**
- Create: `skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-settings.py.j2`
- Create: `skills/cost-billing/instrument/assets/codemod-templates/typescript-moolabs-settings.ts.j2`
- Create: `skills/cost-billing/instrument/assets/codemod-templates/go-moolabs-settings.go.j2`

- [ ] **Step 1: Create Python stub template**

Create `python-moolabs-settings.py.j2`:

```python
{# Stub Moolabs Settings — generated when env_loader_scan.py did NOT
   recognize the customer's existing config pattern. The customer should
   merge this into their own Settings class, or accept it as-is for
   minimal MOOLABS_API_KEY-only routing.

   Variables expected:
     service_slug — for the docstring header
#}
"""Generated stub Settings for {{ service_slug }} Moolabs API key.

Phase A's env_loader_scan.py could not recognize an existing config
pattern (pydantic-settings, dotenv, etc.) in this service's source tree.
This file is the minimal stub to make the codemod-generated helper work:
the helper imports `get_settings()` from here and reads `moolabs_api_key`.

ACTION FOR THE ENGINEER: merge this into your real config layer, or
accept it as-is.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class _MoolabsSettings(BaseSettings):
    moolabs_api_key: SecretStr = Field(..., env="MOOLABS_API_KEY")


@lru_cache(maxsize=1)
def get_settings() -> _MoolabsSettings:
    return _MoolabsSettings()
```

- [ ] **Step 2: Create TS stub template**

Create `typescript-moolabs-settings.ts.j2`:

```typescript
/**
 * Generated stub Moolabs Settings for {{ service_slug }}.
 *
 * Phase A's env_loader_scan.py could not recognize an existing env-config
 * pattern (zod / process.env / env-var) in this service. Merge this into
 * your real config, or accept as-is.
 */

export const MOOLABS_API_KEY = process.env.MOOLABS_API_KEY ?? '';

export function getSettings(): { MOOLABS_API_KEY: string } {
  return { MOOLABS_API_KEY };
}
```

- [ ] **Step 3: Create Go stub template**

Create `go-moolabs-settings.go.j2`:

```go
// Package moolabsconfig — generated stub Settings for {{ service_slug }}.
//
// Phase A's env_loader_scan.py could not recognize an existing config
// pattern (viper / envconfig / os.Getenv) in this service. Merge into
// your real config package, or accept as-is.
package moolabsconfig

import (
	"os"
	"sync"
)

type Settings struct {
	MoolabsAPIKey string
}

var (
	_instance *Settings
	_once     sync.Once
)

// Get returns the singleton stub Settings — reads MOOLABS_API_KEY from
// env once and caches.
func Get() *Settings {
	_once.Do(func() {
		_instance = &Settings{
			MoolabsAPIKey: os.Getenv("MOOLABS_API_KEY"),
		}
	})
	return _instance
}
```

- [ ] **Step 4: Add smoke assertions verifying each renders cleanly**

In `test-suite.sh` Phase 7, add a new block (after the existing helper assertions) that renders each stub template and asserts:
- Python stub renders + ast.compiles
- TS stub renders + contains `getSettings` export
- Go stub renders + gofmt -e is clean (when gofmt available)

```python
# Stub Settings templates
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
```

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -5
git add skills/cost-billing/instrument/assets/codemod-templates/python-moolabs-settings.py.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/typescript-moolabs-settings.ts.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/go-moolabs-settings.go.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): stub Settings templates (Python/TS/Go)

Three Jinja templates emitted when env_loader_scan didn't recognize the
customer's pattern (env_config.mode == 'stub'):

  Python   → moolabs_settings.py    (pydantic-settings BaseSettings + lru_cache)
  TS       → moolabs-settings.ts    (process.env + getSettings wrapper)
  Go       → moolabsconfig/settings.go (sync.Once singleton + os.Getenv)

Each is the SIMPLEST possible Settings exposure for MOOLABS_API_KEY. The
customer can merge into their real config layer or accept as-is.

Phase 7 smoke renders + asserts each stub renders cleanly + Python
py-compiles + Go gofmt-clean (when gofmt available)."
```

---

## Task 11: Deployment-surface templates (`.env.example`, Terraform, k8s Secret)

**Files:**
- Create: `skills/cost-billing/instrument/assets/codemod-templates/dotenv-moolabs.env.j2`
- Create: `skills/cost-billing/instrument/assets/codemod-templates/terraform-moolabs.tf.j2`
- Create: `skills/cost-billing/instrument/assets/codemod-templates/k8s-secret-moolabs.yaml.j2`

- [ ] **Step 1: .env.example snippet**

Create `dotenv-moolabs.env.j2`:

```
# Moolabs SDK API key — required by the codemod-generated helper.
# Get from: https://moolabs.com/settings (or your team's secrets store).
MOOLABS_API_KEY=
```

- [ ] **Step 2: Terraform variable stub**

Create `terraform-moolabs.tf.j2`:

```hcl
# Generated by /cost-billing-instrument Phase 1.7. The Moolabs SDK API
# key for {{ service_slug }}. Wire this into your service module's
# environment block (e.g. ECS task definition, Lambda env vars).

variable "moolabs_api_key" {
  type        = string
  description = "Moolabs SDK API key for the {{ service_slug }} service. Required."
  sensitive   = true
}

# Example: an AWS SSM Parameter Store entry. Adapt to your secrets store.
# resource "aws_ssm_parameter" "moolabs_api_key" {
#   name      = "/${var.environment}/{{ service_slug }}/moolabs/api-key"
#   type      = "SecureString"
#   value     = var.moolabs_api_key
#   overwrite = true
# }
```

- [ ] **Step 3: k8s Secret manifest stub**

Create `k8s-secret-moolabs.yaml.j2`:

```yaml
# Generated by /cost-billing-instrument Phase 1.7. The Moolabs SDK API key
# Secret for {{ service_slug }}.
#
# REVIEWER CHECKLIST:
#   1. Replace `placeholder` with the real key (or wire via your secrets
#      operator: External Secrets Operator, Sealed Secrets, etc).
#   2. Reference this Secret from the {{ service_slug }} Deployment's
#      `envFrom: - secretRef: name: {{ service_slug }}-moolabs`.
apiVersion: v1
kind: Secret
metadata:
  name: {{ service_slug }}-moolabs
type: Opaque
stringData:
  MOOLABS_API_KEY: placeholder
```

- [ ] **Step 4: Smoke assertion for each renders**

In test-suite.sh Phase 7, ADD (after the stub Settings block):

```python
# Deployment-surface templates
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
```

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -5
git add skills/cost-billing/instrument/assets/codemod-templates/dotenv-moolabs.env.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/terraform-moolabs.tf.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/k8s-secret-moolabs.yaml.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): deployment-surface stub templates

Three new templates emitted alongside the per-service helper (never
modifying existing infra files):

  dotenv-moolabs.env.j2          → MOOLABS_API_KEY= line for .env.example
  terraform-moolabs.tf.j2        → variable + commented SSM stub
  k8s-secret-moolabs.yaml.j2     → Secret manifest with REVIEWER CHECKLIST
                                   comment + placeholder value

Phase 7 smoke renders each + sanity-checks content. k8s manifest also
YAML-parsed via yaml.safe_load."
```

---

## Task 12: task_planner.py reads config-wiring-plan.yaml + emits env-wire tasks

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/task_planner.py`

- [ ] **Step 1: Locate where task_planner emits per-callsite tasks**

```bash
grep -n "Task\|build_tasks\|class Task" skills/cost-billing/instrument/scripts/task_planner.py | head -10
```

Note the dataclass `Task` and the orchestrator that emits per-file tasks.

- [ ] **Step 2: Add a new EnvWireTask dataclass**

In `task_planner.py`, ADD a new dataclass near the existing `Task` definition:

```python
@dataclass
class EnvWireTask:
    """One env-wire task per service. Distinct from per-file callsite Tasks
    because env-wiring is service-scoped (one helper module per service,
    plus optional deployment stubs)."""
    task_id: str
    service_slug: str
    mode: str  # "modify" | "stub"
    settings_import_path: str
    api_key_accessor: str
    stub_emit_path: str | None
    deployment_stubs: list[dict]
```

- [ ] **Step 3: Add a loader for config-wiring-plan.yaml**

ADD:

```python
def _load_config_wiring_plan(path: Path) -> list[dict]:
    """Read config-wiring-plan.yaml from Phase 1.7. Returns the per-service
    plan list (empty if file missing or PyYAML absent)."""
    if not path.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except ImportError:
        return []
    return data.get("services") or []


def build_env_wire_tasks(config_wiring_path: Path) -> list[EnvWireTask]:
    services = _load_config_wiring_plan(config_wiring_path)
    out: list[EnvWireTask] = []
    for idx, svc in enumerate(services, start=1):
        out.append(EnvWireTask(
            task_id=f"env_wire_{idx:03d}_{svc.get('service_slug', '')}",
            service_slug=svc.get("service_slug", ""),
            mode=svc.get("mode", "stub"),
            settings_import_path=svc.get("settings_import_path", ""),
            api_key_accessor=svc.get("api_key_accessor", ""),
            stub_emit_path=svc.get("stub_emit_path"),
            deployment_stubs=svc.get("deployment_stubs") or [],
        ))
    return out
```

- [ ] **Step 4: Wire build_env_wire_tasks into main() + emit into tasks.yaml**

Find the existing main() function. ADD a `--config-wiring-plan` argument and emit env-wire tasks into `tasks.yaml` alongside the per-file Tasks.

Read the existing emit_tasks_yaml function and EXTEND it to render an `env_wire_tasks` block when env-wire tasks are present:

```python
def emit_tasks_yaml(tasks: list[Task], dest: Path, env_wire_tasks: list[EnvWireTask] | None = None) -> None:
    # ... existing implementation ...
    # AFTER the existing `lines.append("tasks:")` + per-task render:
    if env_wire_tasks:
        lines.append("env_wire_tasks:")
        for t in env_wire_tasks:
            lines.append(f"  - task_id: {t.task_id}")
            lines.append(f"    service_slug: {t.service_slug}")
            lines.append(f"    mode: {t.mode}")
            # Use the same _quote helper as config_wire.py
            safe_import = t.settings_import_path.replace('\\', '\\\\').replace('"', '\\"')
            safe_accessor = t.api_key_accessor.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'    settings_import_path: "{safe_import}"')
            lines.append(f'    api_key_accessor: "{safe_accessor}"')
            if t.stub_emit_path:
                safe_stub = t.stub_emit_path.replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'    stub_emit_path: "{safe_stub}"')
            else:
                lines.append(f"    stub_emit_path: null")
            if t.deployment_stubs:
                lines.append("    deployment_stubs:")
                for s in t.deployment_stubs:
                    lines.append(f"      - kind: {s['kind']}")
                    if "emit_path" in s:
                        safe_emit = str(s['emit_path']).replace('\\', '\\\\').replace('"', '\\"')
                        lines.append(f'        emit_path: "{safe_emit}"')
                    lines.append(f"        mode: {s['mode']}")
            else:
                lines.append("    deployment_stubs: []")
    # ... existing dest.write_text call ...
```

In `main()`, ADD:

```python
ap.add_argument("--config-wiring-plan",
                default=".moolabs/customer-context/config-wiring-plan.yaml")
# ... after existing arg parsing ...
env_wire_tasks = build_env_wire_tasks(Path(args.config_wiring_plan))
# ... pass env_wire_tasks to emit_tasks_yaml ...
emit_tasks_yaml(tasks, out_path, env_wire_tasks=env_wire_tasks)
```

- [ ] **Step 5: Smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: still passing (no test exercises env_wire_tasks emission yet — that's Phase D end-to-end).

- [ ] **Step 6: Commit**

```bash
git add skills/cost-billing/instrument/scripts/task_planner.py
git commit -m "feat(cost-billing/instrument): task_planner consumes config-wiring-plan.yaml

New EnvWireTask dataclass + build_env_wire_tasks loader + emit_tasks_yaml
extended to render an env_wire_tasks: block when present.

config-wiring-plan.yaml is now wired end-to-end through task_planner —
Phase 1.7 produces it, task_planner reads it, downstream codemod uses
the per-service env-wire entries. Per-file callsite Tasks are unchanged."
```

---

## Task 13: instrument/SKILL.md Phase 1.7 documentation

**Files:**
- Modify: `skills/cost-billing/instrument/SKILL.md`

- [ ] **Step 1: Locate the existing Phase 1.5 / 1.6 documentation**

```bash
grep -n "Phase 1.5\|Phase 1.6\|attribution_discovery\|sdk_snapshot" \
  skills/cost-billing/instrument/SKILL.md | head -10
```

- [ ] **Step 2: Insert Phase 1.7 section after Phase 1.6**

Use Edit. Add the following section after the Phase 1.6 (attribution-discovery) section ends and before the next major phase:

```markdown
### Phase 1.7: Env-wire orchestrator (NEW v0.3 env-routing migration)

Driven by `scripts/config_wire.py`. Reads
`.moolabs/customer-context/env-routing-inventory.yaml` (produced by Phase A
of cost-billing-discovery) and produces
`.moolabs/customer-context/config-wiring-plan.yaml` describing the per-service
env-wiring decisions.

For each service:

- **mode = "modify"** (scanner recognized the customer's pattern at
  medium+ confidence): the helper template imports `get_settings` from
  the customer's existing config module path and reads the API key via
  the language-specific accessor expression (e.g.
  `get_settings().moolabs_api_key.get_secret_value()` for pydantic-settings
  v2; `env.MOOLABS_API_KEY` for zod schemas; `config.Get().MoolabsAPIKey`
  for Go envconfig).

- **mode = "stub"** (scanner unrecognized OR low confidence): the helper
  template imports `get_settings` from a generated stub Settings file
  (`app/services/moolabs_settings.py` / `src/services/moolabs-settings.ts`
  / `internal/moolabsconfig/settings.go`). The stub is the SIMPLEST possible
  Settings exposure for MOOLABS_API_KEY only — the customer merges into
  their real config layer or accepts as-is.

Deployment-surface stubs are emitted per service alongside the helper
generation:

- `.env.example` — line `MOOLABS_API_KEY=` appended to existing file
- `<infra_dir>/moolabs.tf` — new Terraform variable + commented SSM stub
- `<infra_dir>/secret-moolabs.yaml` — new k8s Secret manifest with REVIEWER
  CHECKLIST comments and `placeholder` value
- `Dockerfile` — checklist comment only (never auto-edit ENV lines —
  baked-in secrets are a security smell)

`task_planner.py` reads `config-wiring-plan.yaml` and emits an
`env_wire_tasks:` block into the tasks.yaml output. The codemod consumes
both the per-file callsite Tasks and the per-service env-wire tasks.

The v0.2-era strategy-branched `_resolve_api_key()` (boto3 / google.cloud
secretmanager / hvac / op CLI) is GONE. The customer's Settings class owns
secret resolution; the helper just reads the accessor. Customers using
Vault for their other secrets already have their Settings class configured
to pull from Vault on construction — the helper doesn't need to know.
```

- [ ] **Step 3: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/SKILL.md
git commit -m "docs(cost-billing/instrument): document Phase 1.7 env-wire orchestrator

Describes config_wire.py's mode dispatch (modify vs stub), the per-
service accessor expressions per recognized pattern, deployment-surface
stub emission (.env.example / moolabs.tf / secret-moolabs.yaml /
Dockerfile checklist), and the v0.2 strategy-branch removal.

Phase B's customer-visible contract is now documented end-to-end in
the instrument SKILL.md alongside Phase 1.5 (sdk_snapshot) and Phase
1.6 (attribution_discovery)."
```

---

## Task 14: Final smoke + push + draft PR

**Files:** none (operational)

- [ ] **Step 1: Full smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
```

Expected: PASS count goes up (new test_config_wire + new template-render assertions).

- [ ] **Step 2: End-to-end CLI smoke against an empty inventory**

```bash
mkdir -p /tmp/phase-b-test/.moolabs/customer-context
echo "services: []" > /tmp/phase-b-test/inv.yaml
python3 skills/cost-billing/instrument/scripts/config_wire.py \
    --env-routing-inventory /tmp/phase-b-test/inv.yaml \
    --signed-yaml /dev/null \
    --customer-context-dir /tmp/phase-b-test/.moolabs/customer-context 2>&1 | tail -3
cat /tmp/phase-b-test/.moolabs/customer-context/config-wiring-plan.yaml
rm -rf /tmp/phase-b-test
```

Expected: empty `services: []` plan emitted; CLI exit 0.

- [ ] **Step 3: Commit log review**

```bash
git log --oneline main..HEAD
```

Expected: ~14 commits in conventional-commits format.

- [ ] **Step 4: Push**

```bash
source ../moolabs/.envrc && git push -u origin spec/cost-billing-phase-b-env-wire
```

- [ ] **Step 5: Open draft PR**

```bash
source ../moolabs/.envrc && gh pr create --base main \
    --head spec/cost-billing-phase-b-env-wire \
    --draft \
    --title "feat(cost-billing): env-routing + slugs Phase B (instrument env-wire)" \
    --body "## Summary

Phase B of the env-routing + event-slug constants migration. Phase A
(PR #3, merged) produced env-routing-inventory.yaml; Phase B consumes it.

### What landed

- \`instrument/scripts/config_wire.py\` (Phase 1.7) — orchestrator that
  reads env-routing-inventory and produces config-wiring-plan.yaml with
  per-service mode (modify | stub) + settings_import_path +
  api_key_accessor + deployment_stubs.
- Three helper templates rewritten to use \`get_settings()\` instead of
  v0.2 strategy branches (boto3 / google.cloud / hvac / op CLI removed).
- Three new stub Settings templates (Python/TS/Go) for unrecognized-
  pattern customers.
- Three new deployment-surface templates (.env.example / Terraform /
  k8s Secret).
- task_planner.py extended to consume config-wiring-plan and emit
  env_wire_tasks.
- instrument/SKILL.md Phase 1.7 documentation.

### Out of scope (Phase C/D)

- Phase C: per-product slugs module + framework callsite import-instead-
  of-literal updates.
- Phase D: e2e customer-repo fixture + adversarial-review tuning.

### Test plan

- [x] Smoke green throughout (Phase 7 assertions added per Python/TS/Go).
- [x] test_config_wire.py covers all Python/TS/Go pattern dispatch +
      stub fallback + deployment-stub plan + YAML round-trip + backslash
      regression.
- [x] CLI end-to-end against empty inventory produces empty plan.
- [ ] Phase D fixture: real customer repo with pydantic-settings → modify
      mode → helper renders against the customer's settings module.
"
```

---

## Spec coverage check

| Spec section | Implementing task(s) |
|---|---|
| Phase B emission target — config_wire.py | Tasks 2-6 |
| Helper template rewrites (Python/TS/Go) — get_settings() | Tasks 7-9 |
| Stub Settings file generation | Task 10 |
| Deployment-surface stubs | Task 11 |
| task_planner.py consumes env-wire plan | Task 12 |
| SKILL.md Phase 1.7 docs | Task 13 |
| Smoke assertions for new helper shape | Tasks 7-11 inline (Phase 7 fixture updates) |

All Phase B spec requirements have a task. Phase C/D explicitly out of scope.

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-06-env-routing-and-slugs-phase-b.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task; review between tasks; fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints.

Which approach?
