# Cost-billing env-routing + slugs — Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Phase A's slug-inventory.yaml into the actual codemod emission. Generate per-product slugs modules (`slugs_<product>.{py,ts,go}`) with all five categories of constants; update existing 6 framework callsite templates to import constants from those modules instead of inlining `event_type="..."` / `meter_slug="..."` string literals; extend `task_planner.py` to resolve per-callsite constant names from the slug-inventory.

**Architecture:** Three new Jinja templates render the per-product slugs modules. Six existing framework callsite templates (fastapi/django/flask/express/nestjs/nextjs) get `from {slugs_import_path} import {EVENT_TYPE_X}, {METER_SLUG_X}, ...` imports at the top + constant references inside `emit_*_safe(...)` calls. `task_planner.py` reads `slug-inventory.yaml` and builds a per-product `{value_string: constant_name}` index, then resolves per-callsite Jinja variables (`event_type_const`, `meter_slug_const`, etc.) plus a `slugs_import_path` per task. Phase 7 smoke asserts: constants present in rendered output; NO string-literal `event_type="..."` leaks; slugs templates py-compile/gofmt-clean.

**Tech Stack:** Python 3.10+ (existing); stdlib `unittest` (existing); Jinja2 (existing); PyYAML for inventory reads, hand-rolled emit not needed in Phase C (no new outputs — slugs modules go through Jinja templates directly).

**Spec:** `docs/superpowers/specs/2026-06-06-cost-billing-env-routing-and-slugs-design.md`

**Branch:** `spec/cost-billing-phase-c-slugs` (off `main` at PR #3's merge commit).

**Phase A artifacts already on main** (consumed by Phase C):
- `discovery/scripts/slug_inventory.py` (produces `slug-inventory.yaml`)
- 67/67 smoke baseline on main

**Phase B status:** PR #4 (draft, env-wire emission). Phase C ships independently — it does NOT depend on Phase B because slugs emission is orthogonal to env-wire emission. If PR #4 merges first, Phase C rebases; if Phase C merges first, PR #4 rebases. The two PRs touch disjoint regions of the framework callsite templates (Phase B touches helpers; Phase C touches callsites).

**Out of scope for Phase C:**
- Phase D: e2e fixture + adversarial-review tuning
- Pre-existing backslash YAML escape bugs in attribution_discovery.py / task_planner.py / sdk_snapshot.py (Phase A sibling-search finding; separate follow-up PR)

---

## File Structure (Phase C)

**Create:**
- `skills/cost-billing/instrument/assets/codemod-templates/slugs-python.j2` — per-product slugs module (Python)
- `skills/cost-billing/instrument/assets/codemod-templates/slugs-typescript.j2` — per-product slugs module (TS)
- `skills/cost-billing/instrument/assets/codemod-templates/slugs-go.j2` — per-product slugs module (Go)

**Modify:**
- `skills/cost-billing/instrument/scripts/task_planner.py` — load slug-inventory + build SlugIndex + resolve per-callsite constant names + emit slugs_emit_tasks block
- `skills/cost-billing/instrument/scripts/test_task_planner.py` — NEW (sibling test if not present) OR extend existing test file
- `skills/cost-billing/instrument/assets/codemod-templates/python-fastapi.j2` — import constants, use them in emit calls
- `skills/cost-billing/instrument/assets/codemod-templates/python-django.j2` — same
- `skills/cost-billing/instrument/assets/codemod-templates/python-flask.j2` — same
- `skills/cost-billing/instrument/assets/codemod-templates/typescript-express.j2` — same (TS naming)
- `skills/cost-billing/instrument/assets/codemod-templates/typescript-nestjs.j2` — same
- `skills/cost-billing/instrument/assets/codemod-templates/typescript-nextjs.j2` — same
- `skills/cost-billing/instrument/SKILL.md` — Phase 1.8 documentation
- `skills/cost-billing/scripts/test-suite.sh` — Phase 7 fixture additions + Phase C assertions

---

## Task 1: Branch + baseline

**Files:** none (operational)

- [ ] **Step 1: Switch to main + pull**

```bash
git checkout main
git pull origin main
```

- [ ] **Step 2: Branch for Phase C**

```bash
git checkout -b spec/cost-billing-phase-c-slugs
```

- [ ] **Step 3: Confirm smoke baseline**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
```

Expected: `PASS: 67    FAIL: 0`.

- [ ] **Step 4: Confirm Phase A slug_inventory artifact present**

```bash
ls skills/cost-billing/discovery/scripts/slug_inventory.py
python3 -c "import sys; sys.path.insert(0, 'skills/cost-billing/discovery/scripts'); from slug_inventory import to_constant_name; print(to_constant_name('checkout.recommendation.delivered'))"
```

Expected: file exists; prints `CHECKOUT_RECOMMENDATION_DELIVERED`.

---

## Task 2: Python slugs module template

**Files:**
- Create: `skills/cost-billing/instrument/assets/codemod-templates/slugs-python.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (Phase 7 render assertion)

- [ ] **Step 1: Create the slugs Python template**

Create `skills/cost-billing/instrument/assets/codemod-templates/slugs-python.j2` with EXACTLY:

```python
{# Auto-generated per-product slugs module — DO NOT EDIT.
   Generated by /cost-billing-instrument Phase 1.8 from
   .moolabs/customer-context/slug-inventory.yaml.

   Variables expected:
     product_slug                — e.g. "billing", "analytics"
     constants                   — dict of category -> [{name, value}, ...]
       EVENT_TYPE   — per-feature canonical event identifiers
       METER_SLUG   — per-feature billing routing keys
       FEATURE_KEY  — per-feature short identifiers
       PROVIDER     — vendor identifiers from provider-catalog
       SPAN_TYPE    — span-kind identifiers from cost_kind values
     generated_at                — ISO-8601 timestamp from slug-inventory.yaml

   The customer's emission callsites import the relevant constants from
   this module; the framework callsite templates render the imports +
   constant-reference instead of inline string literals. Re-running the
   codemod against an updated slug-inventory.yaml regenerates this file.
#}
"""Auto-generated Moolabs event slugs for product `{{ product_slug }}`.

DO NOT EDIT — regenerated by /cost-billing-instrument from
.moolabs/customer-context/slug-inventory.yaml (Phase A discovery).

Source generated_at: {{ generated_at }}.
"""

from __future__ import annotations

{% for category in ["EVENT_TYPE", "METER_SLUG", "FEATURE_KEY", "PROVIDER", "SPAN_TYPE"] %}
# ── {{ category }} ──────────────────────────────────────────────────────────
{% for c in constants.get(category, []) %}
{{ category }}_{{ c.name | replace(category + "_", "") if c.name.startswith(category + "_") else c.name }}: str = "{{ c.value }}"
{% endfor %}

{% endfor %}
```

Wait, the constant names already include the category prefix (per Phase A's slug_inventory). For example, the EVENT_TYPE category has entries like `{name: "SEAT_ASSIGNED", value: "seat.assigned"}` — the rendered constant is `EVENT_TYPE_SEAT_ASSIGNED`. Let me re-read Phase A's design.

Actually re-reading: per the spec, the rendered constants ARE prefixed (e.g. `EVENT_TYPE_SEAT_ASSIGNED`). The inventory's `name` field IS that constant name (`SEAT_ASSIGNED`). The template needs to render `EVENT_TYPE_SEAT_ASSIGNED = "seat.assigned"`.

Simpler version — use just `<CATEGORY>_<NAME>` directly:

REPLACE the template content with:

```python
{# Auto-generated per-product slugs module — DO NOT EDIT.
   Generated by /cost-billing-instrument Phase 1.8 from
   .moolabs/customer-context/slug-inventory.yaml.

   Variables expected:
     product_slug                — e.g. "billing", "analytics"
     constants                   — dict of category -> [{name, value}, ...]
       EVENT_TYPE   — per-feature canonical event identifiers
       METER_SLUG   — per-feature billing routing keys
       FEATURE_KEY  — per-feature short identifiers
       PROVIDER     — vendor identifiers from provider-catalog
       SPAN_TYPE    — span-kind identifiers from cost_kind values
     generated_at                — ISO-8601 timestamp from slug-inventory.yaml
#}
"""Auto-generated Moolabs event slugs for product `{{ product_slug }}`.

DO NOT EDIT — regenerated by /cost-billing-instrument from
.moolabs/customer-context/slug-inventory.yaml (Phase A discovery).

Source generated_at: {{ generated_at }}.
"""

from __future__ import annotations

{% for category in ["EVENT_TYPE", "METER_SLUG", "FEATURE_KEY", "PROVIDER", "SPAN_TYPE"] %}
# ── {{ category }} ──────────────────────────────────────────────────────────
{% for c in constants.get(category, []) %}
{{ category }}_{{ c.name }}: str = "{{ c.value }}"
{% endfor %}

{% endfor %}
```

- [ ] **Step 2: Add Phase 7 smoke assertion for slugs-python template**

In `skills/cost-billing/scripts/test-suite.sh`, ADD a new block AFTER the existing helper / stub / deployment template assertions (before the Codex regression check). Use this as the slug-inventory fixture for testing:

```python
# Slugs module templates (Phase 1.8)
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
```

- [ ] **Step 3: Smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
```

Expected: passes; new `PASS slugs slugs-python.j2: ...` line.

- [ ] **Step 4: Commit**

```bash
git add skills/cost-billing/instrument/assets/codemod-templates/slugs-python.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): slugs-python.j2 — per-product slugs module

Auto-generated DO NOT EDIT header + 5 category sections (EVENT_TYPE,
METER_SLUG, FEATURE_KEY, PROVIDER, SPAN_TYPE). Each entry renders as
\`<CATEGORY>_<NAME>: str = \"<value>\"\`.

Phase 7 smoke renders with a 2-entry fixture per category + asserts
py-compile clean + each of the 5 category constants present.

Phase 1.8 (slugs emission) uses this template once per discovered
product (CPO-stage bootstrap product list)."
```

---

## Task 3: TypeScript slugs module template

**Files:**
- Create: `skills/cost-billing/instrument/assets/codemod-templates/slugs-typescript.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (Phase 7 TS slugs render assertion)

- [ ] **Step 1: Create the slugs TS template**

Create `slugs-typescript.j2`:

```typescript
{# Auto-generated per-product slugs module — DO NOT EDIT. See slugs-python.j2
   for the variable contract. TS-specific naming: const exports. #}
/**
 * Auto-generated Moolabs event slugs for product `{{ product_slug }}`.
 *
 * DO NOT EDIT — regenerated by /cost-billing-instrument from
 * .moolabs/customer-context/slug-inventory.yaml (Phase A discovery).
 *
 * Source generated_at: {{ generated_at }}.
 */

{% for category in ["EVENT_TYPE", "METER_SLUG", "FEATURE_KEY", "PROVIDER", "SPAN_TYPE"] %}
// ── {{ category }} ──────────────────────────────────────────────────────────
{% for c in constants.get(category, []) %}
export const {{ category }}_{{ c.name }} = "{{ c.value }}" as const;
{% endfor %}

{% endfor %}
```

- [ ] **Step 2: Add Phase 7 TS slugs assertion**

In test-suite.sh, ADD after the Python slugs block:

```python
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
```

- [ ] **Step 3: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -5
git add skills/cost-billing/instrument/assets/codemod-templates/slugs-typescript.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): slugs-typescript.j2 — per-product slugs module

Parallel to slugs-python.j2. TS-specific: \`export const X = \"value\" as const;\`
annotations give TS literal types (consumers can narrow).

Phase 7 smoke renders + asserts 5 categories present + as-const annotation."
```

---

## Task 4: Go slugs module template

**Files:**
- Create: `skills/cost-billing/instrument/assets/codemod-templates/slugs-go.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (Phase 7 Go slugs render assertion + gofmt)

- [ ] **Step 1: Create the slugs Go template**

Create `slugs-go.j2`:

```go
{# Auto-generated per-product slugs module — DO NOT EDIT. See slugs-python.j2
   for the variable contract. Go-specific: constants in a typed string block,
   one block per category for readability + gofmt-cleanliness. #}
// Package moolabsslugs_{{ product_slug }} — auto-generated Moolabs event slugs
// for product `{{ product_slug }}`.
//
// DO NOT EDIT — regenerated by /cost-billing-instrument from
// .moolabs/customer-context/slug-inventory.yaml (Phase A discovery).
//
// Source generated_at: {{ generated_at }}.
package moolabsslugs_{{ product_slug }}

{% for category in ["EVENT_TYPE", "METER_SLUG", "FEATURE_KEY", "PROVIDER", "SPAN_TYPE"] %}
{% if constants.get(category) %}
// {{ category }} constants.
const (
{% for c in constants.get(category, []) %}
	{{ category }}_{{ c.name }} = "{{ c.value }}"
{% endfor %}
)

{% endif %}
{% endfor %}
```

- [ ] **Step 2: Add Phase 7 Go slugs assertion (with gofmt -e)**

In test-suite.sh, ADD after the TS slugs block:

```python
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
```

- [ ] **Step 3: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -5
git add skills/cost-billing/instrument/assets/codemod-templates/slugs-go.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): slugs-go.j2 — per-product slugs module

Parallel to slugs-python.j2 + slugs-typescript.j2. Go-specific:
- Package name: moolabsslugs_<product_slug>
- One \`const ( ... )\` block per non-empty category
- Empty categories rendered with no block (gofmt expects this)

Phase 7 smoke renders + 5 categories + gofmt -e clean."
```

---

## Task 5: task_planner.py — load slug-inventory.yaml + build SlugIndex

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/task_planner.py`
- Create or modify: `skills/cost-billing/instrument/scripts/test_task_planner.py` (sibling test if it doesn't exist)

- [ ] **Step 1: Check whether `test_task_planner.py` exists**

```bash
ls skills/cost-billing/instrument/scripts/test_task_planner.py
```

If absent, create with this initial structure:

```python
#!/usr/bin/env python3
"""Unit tests for task_planner.py.

Stdlib unittest; runs in the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_planner as tp  # noqa: E402


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Append failing slug-inventory load + index tests**

Append to `test_task_planner.py` (before `if __name__`):

```python
class LoadSlugInventory(unittest.TestCase):
    def test_load_basic_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "slug-inventory.yaml"
            inv.write_text(
                "generated_at: 2026-06-06T00:00:00+00:00\n"
                "products:\n"
                "  - product_slug: billing\n"
                "    constants:\n"
                "      EVENT_TYPE:\n"
                "        - name: SEAT_ASSIGNED\n"
                "          value: \"seat.assigned\"\n"
                "      METER_SLUG:\n"
                "        - name: SEAT_ASSIGNED\n"
                "          value: \"seat.assigned\"\n"
                "      FEATURE_KEY: []\n"
                "      PROVIDER: []\n"
                "      SPAN_TYPE: []\n"
            )
            data = tp.load_slug_inventory(inv)
            self.assertEqual(len(data["products"]), 1)
            self.assertEqual(data["products"][0]["product_slug"], "billing")

    def test_load_missing_file_returns_empty(self):
        data = tp.load_slug_inventory(Path("/nonexistent/path.yaml"))
        self.assertEqual(data, {"products": []})


class SlugIndex(unittest.TestCase):
    def test_index_builds_value_to_constant_lookup(self):
        inventory = {
            "products": [
                {
                    "product_slug": "billing",
                    "constants": {
                        "EVENT_TYPE": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                            {"name": "CHECKOUT_DELIVERED",
                             "value": "checkout.delivered"},
                        ],
                        "METER_SLUG": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                        ],
                        "FEATURE_KEY": [],
                        "PROVIDER": [],
                        "SPAN_TYPE": [
                            {"name": "LLM_TOKENS", "value": "llm-tokens"},
                        ],
                    },
                },
            ],
        }
        index = tp.build_slug_index(inventory)
        # Index is keyed by product_slug -> category -> {value: constant_name}
        self.assertIn("billing", index)
        self.assertEqual(
            index["billing"]["EVENT_TYPE"]["seat.assigned"],
            "EVENT_TYPE_SEAT_ASSIGNED",
        )
        self.assertEqual(
            index["billing"]["EVENT_TYPE"]["checkout.delivered"],
            "EVENT_TYPE_CHECKOUT_DELIVERED",
        )
        self.assertEqual(
            index["billing"]["SPAN_TYPE"]["llm-tokens"],
            "SPAN_TYPE_LLM_TOKENS",
        )

    def test_empty_inventory_yields_empty_index(self):
        index = tp.build_slug_index({"products": []})
        self.assertEqual(index, {})
```

- [ ] **Step 3: Run — must FAIL**

```bash
python3 skills/cost-billing/instrument/scripts/test_task_planner.py 2>&1 | tail -5
```

Expected: `AttributeError: module 'task_planner' has no attribute 'load_slug_inventory'`.

- [ ] **Step 4: Implement load_slug_inventory + build_slug_index in task_planner.py**

In `task_planner.py`, ADD (near the existing helpers, e.g. after `_load_config_wiring_plan` from Phase B):

```python
def load_slug_inventory(path: Path) -> dict:
    """Read slug-inventory.yaml (Phase A's slug_inventory.py output).
    Returns {"products": []} on missing file or absent PyYAML.
    """
    if not path.exists():
        return {"products": []}
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except ImportError:
        return {"products": []}
    data.setdefault("products", [])
    return data


def build_slug_index(inventory: dict) -> dict[str, dict[str, dict[str, str]]]:
    """Build a per-product / per-category value-to-constant-name lookup.

    Phase A's slug_inventory.py emits constants with `name` already in
    canonical UPPER_SNAKE_CASE (e.g. `SEAT_ASSIGNED`). Phase C's framework
    callsite templates need to render `EVENT_TYPE_SEAT_ASSIGNED` —
    so the lookup value here is the CATEGORY-prefixed constant name.
    """
    index: dict[str, dict[str, dict[str, str]]] = {}
    for product in inventory.get("products") or []:
        slug = product.get("product_slug", "")
        if not slug:
            continue
        product_index: dict[str, dict[str, str]] = {}
        for category, entries in (product.get("constants") or {}).items():
            value_to_const: dict[str, str] = {}
            for e in entries or []:
                if e.get("name") and e.get("value") is not None:
                    value_to_const[e["value"]] = f"{category}_{e['name']}"
            product_index[category] = value_to_const
        index[slug] = product_index
    return index
```

- [ ] **Step 5: Run — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_task_planner.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 6: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/scripts/task_planner.py \
        skills/cost-billing/instrument/scripts/test_task_planner.py
git commit -m "feat(cost-billing/instrument): task_planner loads slug-inventory + builds index

load_slug_inventory() reads Phase A's slug-inventory.yaml. Returns
{\"products\": []} on missing file or absent PyYAML.

build_slug_index() builds a per-product / per-category value -> constant
lookup. The constant names are CATEGORY-prefixed (e.g. EVENT_TYPE_SEAT_ASSIGNED)
which matches Phase C's slugs Jinja template output.

Phase C Task 6 wires this into per-callsite constant resolution."
```

---

## Task 6: task_planner.py — per-callsite slug resolution + slugs_emit_tasks block

**Files:**
- Modify: `skills/cost-billing/instrument/scripts/task_planner.py`
- Modify: `skills/cost-billing/instrument/scripts/test_task_planner.py`

- [ ] **Step 1: Append failing tests for per-callsite resolution + slugs emit**

Append to `test_task_planner.py`:

```python
class SlugConstantResolver(unittest.TestCase):
    def setUp(self):
        self.inventory = {
            "products": [
                {
                    "product_slug": "billing",
                    "constants": {
                        "EVENT_TYPE": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                        ],
                        "METER_SLUG": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                        ],
                        "FEATURE_KEY": [
                            {"name": "ASSIGNED", "value": "assigned"},
                        ],
                        "PROVIDER": [],
                        "SPAN_TYPE": [
                            {"name": "LLM_TOKENS", "value": "llm-tokens"},
                        ],
                    },
                },
            ],
        }
        self.index = tp.build_slug_index(self.inventory)

    def test_resolve_event_type_constant(self):
        consts = tp.resolve_slug_constants(
            self.index, product_slug="billing",
            event_type="seat.assigned", workflow_id="seat.assigned",
            cost_kind="llm-tokens",
        )
        self.assertEqual(consts["event_type_const"], "EVENT_TYPE_SEAT_ASSIGNED")
        self.assertEqual(consts["meter_slug_const"], "METER_SLUG_SEAT_ASSIGNED")
        self.assertEqual(consts["feature_key_const"], "FEATURE_KEY_ASSIGNED")
        self.assertEqual(consts["span_type_const"], "SPAN_TYPE_LLM_TOKENS")
        self.assertIsNone(consts["provider_const"])

    def test_unknown_product_returns_none_consts(self):
        consts = tp.resolve_slug_constants(
            self.index, product_slug="unknown",
            event_type="x.y", workflow_id="x.y", cost_kind="z",
        )
        self.assertIsNone(consts["event_type_const"])
        self.assertIsNone(consts["meter_slug_const"])


class BuildSlugsEmitTasks(unittest.TestCase):
    def test_one_task_per_product(self):
        inventory = {
            "products": [
                {"product_slug": "billing", "constants": {}},
                {"product_slug": "analytics", "constants": {}},
            ],
        }
        tasks = tp.build_slugs_emit_tasks(inventory)
        self.assertEqual(len(tasks), 2)
        slugs = {t.product_slug for t in tasks}
        self.assertEqual(slugs, {"billing", "analytics"})

    def test_empty_inventory_yields_no_tasks(self):
        tasks = tp.build_slugs_emit_tasks({"products": []})
        self.assertEqual(tasks, [])
```

- [ ] **Step 2: Run — must FAIL**

Expected: `AttributeError: module 'task_planner' has no attribute 'resolve_slug_constants'`.

- [ ] **Step 3: Add SlugsEmitTask dataclass + resolver + builder**

In `task_planner.py`, ADD near the `EnvWireTask` dataclass (if Phase B has landed) or wherever dataclasses are defined:

```python
@dataclass
class SlugsEmitTask:
    """One slugs-emit task per product. Renders slugs-<lang>.j2 to
    <slugs_emit_path> based on the inventory's per-product constants."""
    task_id: str
    product_slug: str
    constants: dict          # per-category {name, value} lists from slug-inventory
    generated_at: str        # from slug-inventory.yaml


def resolve_slug_constants(
    index: dict[str, dict[str, dict[str, str]]],
    product_slug: str,
    event_type: str | None,
    workflow_id: str | None,
    cost_kind: str | None,
) -> dict[str, str | None]:
    """Look up the per-callsite constant names from the index.

    Phase C framework callsite templates use these to render
    `event_type=EVENT_TYPE_X` instead of `event_type="x.y"`.

    Returns a dict with keys: event_type_const, meter_slug_const,
    feature_key_const, provider_const, span_type_const. Each value is
    the CATEGORY-prefixed constant name (e.g. `EVENT_TYPE_SEAT_ASSIGNED`)
    or None if the lookup misses.
    """
    product_index = index.get(product_slug) or {}

    def _lookup(category: str, value: str | None) -> str | None:
        if not value:
            return None
        return (product_index.get(category) or {}).get(value)

    # feature_key is derived from workflow_id's second dotted segment
    # (matches slug_inventory.py's _feature_key_for convention)
    feature_key_value: str | None = None
    if workflow_id:
        parts = workflow_id.split(".")
        feature_key_value = parts[1] if len(parts) >= 2 else workflow_id

    return {
        "event_type_const": _lookup("EVENT_TYPE", event_type),
        "meter_slug_const": _lookup("METER_SLUG", workflow_id),
        "feature_key_const": _lookup("FEATURE_KEY", feature_key_value),
        "provider_const": _lookup("PROVIDER", None),  # provider per-entry — None for now
        "span_type_const": _lookup("SPAN_TYPE", cost_kind),
    }


def build_slugs_emit_tasks(inventory: dict) -> list[SlugsEmitTask]:
    """One slugs-emit task per product in the inventory."""
    out: list[SlugsEmitTask] = []
    generated_at = inventory.get("generated_at", "")
    for idx, product in enumerate(inventory.get("products") or [], start=1):
        slug = product.get("product_slug", "")
        if not slug:
            continue
        out.append(SlugsEmitTask(
            task_id=f"slugs_emit_{idx:03d}_{slug}",
            product_slug=slug,
            constants=product.get("constants") or {},
            generated_at=generated_at,
        ))
    return out
```

- [ ] **Step 4: Extend emit_tasks_yaml to render slugs_emit_tasks block**

In `task_planner.py`, MODIFY `emit_tasks_yaml`. UPDATE the signature:

```python
def emit_tasks_yaml(
    tasks: list[Task],
    dest: Path,
    env_wire_tasks: list[EnvWireTask] | None = None,
    slugs_emit_tasks: list[SlugsEmitTask] | None = None,
) -> None:
```

After the existing env_wire_tasks rendering block (added in Phase B), ADD:

```python
    if slugs_emit_tasks:
        lines.append("slugs_emit_tasks:")
        for t in slugs_emit_tasks:
            lines.append(f"  - task_id: {t.task_id}")
            lines.append(f"    product_slug: {t.product_slug}")
            safe_gen_at = t.generated_at.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    generated_at: "{safe_gen_at}"')
            # The constants block is rendered as a nested mapping.
            lines.append(f"    constants:")
            for category in ("EVENT_TYPE", "METER_SLUG", "FEATURE_KEY",
                             "PROVIDER", "SPAN_TYPE"):
                entries = t.constants.get(category, [])
                if not entries:
                    lines.append(f"      {category}: []")
                    continue
                lines.append(f"      {category}:")
                for e in entries:
                    safe_name = str(e.get("name", "")).replace("\\", "\\\\").replace('"', '\\"')
                    safe_value = str(e.get("value", "")).replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'        - name: "{safe_name}"')
                    lines.append(f'          value: "{safe_value}"')
```

In `main()`:
- ADD `ap.add_argument("--slug-inventory", default=".moolabs/customer-context/slug-inventory.yaml")`
- After `env_wire_tasks` is built, ADD:
  ```python
  slug_inv = load_slug_inventory(Path(args.slug_inventory))
  slugs_emit_tasks = build_slugs_emit_tasks(slug_inv)
  ```
- UPDATE the `emit_tasks_yaml(...)` call to pass `slugs_emit_tasks=slugs_emit_tasks`.

- [ ] **Step 5: Run tests — must PASS**

```bash
python3 skills/cost-billing/instrument/scripts/test_task_planner.py 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 6: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/scripts/task_planner.py \
        skills/cost-billing/instrument/scripts/test_task_planner.py
git commit -m "feat(cost-billing/instrument): task_planner per-callsite slug resolution + slugs_emit_tasks

- SlugsEmitTask dataclass (one per product)
- resolve_slug_constants() looks up event_type / meter_slug / feature_key /
  span_type / provider constant names for a per-callsite render. None when
  the lookup misses (template falls back to literal — guarded by Phase C
  Task 7's negative-leakage assertion).
- build_slugs_emit_tasks() emits one task per product.
- emit_tasks_yaml() renders slugs_emit_tasks: block with escape-safe
  YAML emit (backslash + quote per Phase A's lesson).
- main() reads --slug-inventory and passes the tasks list through.

Phase C Task 7-8 wires the per-callsite resolver into the framework
callsite template rendering."
```

---

## Task 7: Update Python framework callsite templates

**Files:**
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/python-fastapi.j2`
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/python-django.j2`
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/python-flask.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (Phase 7 fixture + assertions)

- [ ] **Step 1: Identify the literal-string render lines in each template**

```bash
grep -nE '"\{\{ entry\.event_type \}\}"|"\{\{ entry\.workflow_id \}\}"|"\{\{ entry\.cost_kind \}\}"' \
  skills/cost-billing/instrument/assets/codemod-templates/python-fastapi.j2
```

The existing templates render literals like `event_type="{{ entry.event_type }}"`. Phase C replaces these with constant references `event_type={{ entry.event_type_const }}` (no quotes — the constant is a bare identifier).

- [ ] **Step 2: Update python-fastapi.j2 sibling-pair, usage-only, cost-only branches**

Each pattern's `emit_*_safe(...)` block needs:

a) An import block at the top:
```python
from {{ entry.slugs_import_path }} import (
{% if entry.event_type_const %}    {{ entry.event_type_const }},
{% endif %}{% if entry.meter_slug_const %}    {{ entry.meter_slug_const }},
{% endif %}{% if entry.feature_key_const %}    {{ entry.feature_key_const }},
{% endif %}{% if entry.span_type_const %}    {{ entry.span_type_const }},
{% endif %})
```

b) The emit-call kwargs change:
- `event_type="{{ entry.event_type }}"` → `event_type={{ entry.event_type_const if entry.event_type_const else '"' ~ entry.event_type ~ '"' }}`
- `meter_slug="{{ entry.workflow_id }}"` → `meter_slug={{ entry.meter_slug_const if entry.meter_slug_const else '"' ~ entry.workflow_id ~ '"' }}`
- The spans entry's `kind: "{{ entry.cost_kind }}"` → `kind: {{ entry.span_type_const if entry.span_type_const else '"' ~ entry.cost_kind ~ '"' }}`

Use Edit to apply these changes per pattern. Read the current file first to identify exact boundaries.

DETAILED INSTRUCTION for the implementer: in each pattern's emit-call block, replace each string-literal kwarg with a Jinja conditional that uses the constant when available, falling back to the inline literal when not. The fallback is important because some entries may lack a corresponding slug-inventory entry (e.g. discovered late; not yet propagated).

ALSO: add the slugs-import block right after the helper import. For sibling-pair, the order is:
1. `from app.services.moolabs_client import emit_event_safe`
2. (NEW) `from {{ slugs_import_path }} import ...`
3. `import uuid as _moolabs_uuid`
4. Existing local var declarations
5. The emit call with constant references

- [ ] **Step 3: Update python-django.j2 same way**

- [ ] **Step 4: Update python-flask.j2 same way**

- [ ] **Step 5: Extend smoke fixture entry_base to include slug constants**

In test-suite.sh, MODIFY `entry_base` to ADD these keys:

```python
entry_base = {
    # ... existing keys ...
    "slugs_import_path": "app.services.moolabs.slugs_billing",
    "event_type_const": "EVENT_TYPE_COMPLETION_DELIVERED",
    "meter_slug_const": "METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED",
    "feature_key_const": "FEATURE_KEY_RECOMMENDATION",
    "span_type_const": "SPAN_TYPE_LLM_TOKENS",
    "provider_const": None,
}
```

- [ ] **Step 6: Add Phase 7 Phase-C assertions to the Python per-callsite block**

Find the Python per-callsite assertion block (where `assertIn(..., r)` and friends check `emit_event_safe(`, etc.) and ADD:

```python
            # Phase C slugs: assert constants present + no string-literal event_type/meter_slug
            has_slugs_import = "from app.services.moolabs.slugs_billing import" in r
            has_event_type_const = "event_type=EVENT_TYPE_COMPLETION_DELIVERED" in r
            has_meter_slug_const = ("meter_slug=METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED" in r
                                    or pat == "cost-only")
            no_event_type_literal = ('event_type="completion.delivered"' not in r)
            no_meter_slug_literal = ('meter_slug="checkout.recommendation.delivered"' not in r)
            if not has_slugs_import:
                print(f"  FAIL  {tpl}[{pat}]: Phase C slugs import missing")
                fail_count += 1; continue
            if not has_event_type_const:
                print(f"  FAIL  {tpl}[{pat}]: event_type_const not rendered")
                fail_count += 1; continue
            if not has_meter_slug_const:
                print(f"  FAIL  {tpl}[{pat}]: meter_slug_const not rendered (sibling-pair / usage-only)")
                fail_count += 1; continue
            if not no_event_type_literal:
                print(f"  FAIL  {tpl}[{pat}]: event_type STRING LITERAL leaked")
                fail_count += 1; continue
            if not no_meter_slug_literal:
                print(f"  FAIL  {tpl}[{pat}]: meter_slug STRING LITERAL leaked")
                fail_count += 1; continue
```

- [ ] **Step 7: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
git add skills/cost-billing/instrument/assets/codemod-templates/python-fastapi.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/python-django.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/python-flask.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): Python framework callsites import slug constants

3 templates updated (fastapi/django/flask) — each pattern (sibling-pair,
usage-only, cost-only) now:
1. Imports the relevant constants from {{ slugs_import_path }}.
2. Uses event_type=EVENT_TYPE_X / meter_slug=METER_SLUG_X / spans=[{kind:
   SPAN_TYPE_X}] instead of string literals.
3. Falls back to inline literal when the slug-inventory lookup misses
   (entry.event_type_const is None) — guarded by Phase 7 negative-leakage
   assertion for the default fixture.

Phase 7 assertions added per Python pattern:
- has_slugs_import
- has_event_type_const / has_meter_slug_const
- no_event_type_literal / no_meter_slug_literal (negative-leakage)"
```

---

## Task 8: Update TypeScript framework callsite templates

**Files:**
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/typescript-express.j2`
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/typescript-nestjs.j2`
- Modify: `skills/cost-billing/instrument/assets/codemod-templates/typescript-nextjs.j2`
- Modify: `skills/cost-billing/scripts/test-suite.sh` (Phase 7 TS assertions)

- [ ] **Step 1: Update typescript-express.j2 (and nestjs / nextjs same way)**

Same pattern as Python (Task 7) but TS naming.

a) Import block (sibling-pair example):
```typescript
import {
{% if entry.event_type_const %}  {{ entry.event_type_const }},
{% endif %}{% if entry.meter_slug_const %}  {{ entry.meter_slug_const }},
{% endif %}{% if entry.feature_key_const %}  {{ entry.feature_key_const }},
{% endif %}{% if entry.span_type_const %}  {{ entry.span_type_const }},
{% endif %}} from '{{ entry.slugs_import_path }}';
```

b) Emit-call kwargs change:
- `eventType: "{{ entry.event_type }}",` → `eventType: {{ entry.event_type_const if entry.event_type_const else "'" ~ entry.event_type ~ "'" }},`
- `meterSlug: "{{ entry.workflow_id }}",` → `meterSlug: {{ entry.meter_slug_const if entry.meter_slug_const else "'" ~ entry.workflow_id ~ "'" }},`
- `kind: "{{ entry.cost_kind }}",` → `kind: {{ entry.span_type_const if entry.span_type_const else "'" ~ entry.cost_kind ~ "'" }},`

- [ ] **Step 2: Apply same changes to typescript-nestjs.j2 and typescript-nextjs.j2**

- [ ] **Step 3: Update entry_base in test-suite.sh — TS uses same constant names**

Already done in Task 7 (entry_base is shared). Just add TS-specific slugs_import_path override for TS templates:

In the existing per-template assertion block, BEFORE rendering TS templates, override slugs_import_path:

```python
ts_entry = dict(entry)
ts_entry["slugs_import_path"] = "@/services/moolabs/slugs_billing"
```

Then use `ts_entry` when rendering TS templates.

- [ ] **Step 4: Add Phase 7 TS Phase-C assertions**

In the TS per-callsite assertion block:

```python
            # Phase C TS slugs
            has_slugs_import = "from '@/services/moolabs/slugs_billing'" in r
            has_event_type_const = "eventType: EVENT_TYPE_COMPLETION_DELIVERED" in r
            no_event_type_literal = ("eventType: 'completion.delivered'" not in r
                                     and 'eventType: "completion.delivered"' not in r)
            if not has_slugs_import:
                print(f"  FAIL  {tpl}[{pat}]: Phase C TS slugs import missing")
                fail_count += 1; continue
            if not has_event_type_const:
                print(f"  FAIL  {tpl}[{pat}]: TS event_type_const not rendered")
                fail_count += 1; continue
            if not no_event_type_literal:
                print(f"  FAIL  {tpl}[{pat}]: TS event_type STRING LITERAL leaked")
                fail_count += 1; continue
```

- [ ] **Step 5: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -10
git add skills/cost-billing/instrument/assets/codemod-templates/typescript-express.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/typescript-nestjs.j2 \
        skills/cost-billing/instrument/assets/codemod-templates/typescript-nextjs.j2 \
        skills/cost-billing/scripts/test-suite.sh
git commit -m "feat(cost-billing/instrument): TS framework callsites import slug constants

3 templates updated (express/nestjs/nextjs) — parallel to Python (Task 7):
- ESM import block at the top of each pattern
- eventType / meterSlug / kind use bare identifiers (no quotes) when
  the slug-inventory lookup hits; fall back to inline string when not

Phase 7 assertions parallel to Python:
- has_slugs_import (TS '@/' aliased path)
- has_event_type_const (no quotes around constant name)
- no_event_type_literal (negative-leakage for both single + double
  quotes around the string literal)"
```

---

## Task 9: SKILL.md Phase 1.8 documentation

**Files:**
- Modify: `skills/cost-billing/instrument/SKILL.md`

- [ ] **Step 1: Locate Phase 1.7 in SKILL.md**

```bash
grep -n "Phase 1.7\|Phase 1.8\|slugs_emit\|slug_inventory" \
  skills/cost-billing/instrument/SKILL.md | head -10
```

If Phase B (PR #4) is already merged to main when starting Phase C, Phase 1.7 is documented. If not, Phase 1.7 is in the Phase B branch only — note this and insert Phase 1.8 after Phase 1.6 (or wherever the previous phase ends).

- [ ] **Step 2: Insert Phase 1.8 section**

Insert this section (after Phase 1.7 if present; otherwise after Phase 1.6):

```markdown
### Phase 1.8: Slugs emission (NEW v0.3 event-slug constants migration)

Driven by `task_planner.py`'s `build_slugs_emit_tasks()`. Reads
`.moolabs/customer-context/slug-inventory.yaml` (produced by Phase A
of cost-billing-discovery) and emits one slugs module per discovered
product.

For each product in the inventory, the codemod renders one of:

- `slugs-python.j2` → `app/services/moolabs/slugs_<product_slug>.py`
- `slugs-typescript.j2` → `src/services/moolabs/slugs_<product_slug>.ts`
- `slugs-go.j2` → `internal/moolabsclient/slugs_<product_slug>/slugs.go`

The choice of language follows the per-service language declared in
`04-final.signed.yaml > integration.services[].language`. For polyglot
customers (one Python service + one Go service), the codemod emits a
slugs module per language per product.

Each slugs module contains 5 categories of constants:

- `EVENT_TYPE_*` — per-feature canonical event identifiers
- `METER_SLUG_*` — per-feature billing routing keys
- `FEATURE_KEY_*` — per-feature short identifiers
- `PROVIDER_*` — vendor identifiers from provider-catalog
- `SPAN_TYPE_*` — span-kind identifiers from cost_kind values

Constant naming convention: `<CATEGORY>_<NAME>` where `<NAME>` is the
UPPER_SNAKE_CASE conversion of the source value (handled by Phase A's
`slug_inventory.py`).

The framework callsite templates (fastapi / django / flask / express /
nestjs / nextjs) IMPORT the relevant constants from the slugs module
and render them at the callsite instead of inlining string literals:

```python
# Before (v0.2 / Phase A):
emit_event_safe(
    event_type="checkout.recommendation.delivered",
    meter_slug="checkout.recommendation.delivered",
    ...
)

# After (Phase C):
from app.services.moolabs.slugs_billing import (
    EVENT_TYPE_CHECKOUT_RECOMMENDATION_DELIVERED,
    METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED,
)

emit_event_safe(
    event_type=EVENT_TYPE_CHECKOUT_RECOMMENDATION_DELIVERED,
    meter_slug=METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED,
    ...
)
```

Per-callsite resolution from string value → constant name is done by
`task_planner.py`'s `resolve_slug_constants()` using the index built
by `build_slug_index()`. When the lookup misses (e.g. a discovered
callsite whose event_type isn't in the inventory), the template falls
back to the inline literal — and Phase 7 smoke's negative-leakage
assertion ensures the canonical fixture exercises the constant path.

The slugs modules are AUTO-GENERATED with a `DO NOT EDIT` header.
Re-running the codemod against an updated `slug-inventory.yaml`
regenerates them.
```

- [ ] **Step 3: Smoke + commit**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3
git add skills/cost-billing/instrument/SKILL.md
git commit -m "docs(cost-billing/instrument): document Phase 1.8 slugs emission

Describes the slugs-<lang>.j2 templates, per-product module emission,
5 categories of constants, the framework callsite import-instead-of-
literal pattern, and per-callsite resolution via task_planner.py.

Phase C's customer-visible contract is now documented end-to-end in the
instrument SKILL.md alongside Phase 1.5/1.6 (and Phase 1.7 if Phase B
has been merged)."
```

---

## Task 10: Final smoke + push + draft PR

**Files:** none (operational)

- [ ] **Step 1: Full smoke**

```bash
bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -15
```

Expected: pass count goes up from 67 (3 new slugs template renders + Phase 7 Phase-C per-callsite assertions).

- [ ] **Step 2: End-to-end CLI smoke (slug-inventory → task_planner → tasks.yaml)**

```bash
TMPDIR=$(mktemp -d /tmp/phase-c-e2e-XXXX)
mkdir -p "$TMPDIR/.moolabs/customer-context" "$TMPDIR/.moolabs/codemod"
cat > "$TMPDIR/.moolabs/customer-context/slug-inventory.yaml" << 'YAML'
generated_at: 2026-06-06T00:00:00+00:00
products:
  - product_slug: billing
    constants:
      EVENT_TYPE:
        - name: SEAT_ASSIGNED
          value: "seat.assigned"
      METER_SLUG: []
      FEATURE_KEY: []
      PROVIDER: []
      SPAN_TYPE: []
YAML
# task_planner CLI will need other inventories; pass /dev/null where possible
# (any missing input degrades gracefully per Phase A pattern).
python3 skills/cost-billing/instrument/scripts/task_planner.py \
    --cost-inventory /dev/null \
    --usage-inventory /dev/null \
    --output-input-map /dev/null \
    --attribution-bindings /dev/null \
    --signed-yaml /dev/null \
    --config-wiring-plan /dev/null \
    --slug-inventory "$TMPDIR/.moolabs/customer-context/slug-inventory.yaml" \
    --output "$TMPDIR/.moolabs/codemod/tasks.yaml" 2>&1 | tail -3
echo "--- tasks.yaml (slugs_emit_tasks block) ---"
grep -A 20 "slugs_emit_tasks:" "$TMPDIR/.moolabs/codemod/tasks.yaml" 2>/dev/null || \
    echo "(tasks.yaml not produced — check task_planner CLI args)"
rm -rf "$TMPDIR"
```

Expected: `tasks.yaml` is produced with a `slugs_emit_tasks:` block containing one entry for `billing`.

NOTE: task_planner CLI argument names may differ from what's shown here. Adapt to whatever the actual CLI accepts — the goal is to confirm that the slugs_emit_tasks block lands in tasks.yaml.

- [ ] **Step 3: Commits review**

```bash
git log --oneline main..HEAD | nl
```

Expected: ~10 commits.

- [ ] **Step 4: Push**

```bash
source ../moolabs/.envrc && git push -u origin spec/cost-billing-phase-c-slugs
```

- [ ] **Step 5: Open draft PR**

```bash
source ../moolabs/.envrc && gh pr create --base main \
    --head spec/cost-billing-phase-c-slugs \
    --draft \
    --title "feat(cost-billing): env-routing + slugs Phase C (instrument slugs)" \
    --body "## Summary

Phase C of the env-routing + event-slug-constants migration. Phase A
(merged) produces slug-inventory.yaml; Phase C consumes it and emits
per-product slugs modules + updates framework callsite templates to
import constants instead of inlining string literals.

### What landed

- Three new Jinja templates (slugs-{python,typescript,go}.j2) — render
  per-product slugs modules with 5 categories of constants (EVENT_TYPE,
  METER_SLUG, FEATURE_KEY, PROVIDER, SPAN_TYPE).
- task_planner.py extended with load_slug_inventory, build_slug_index,
  resolve_slug_constants, SlugsEmitTask, build_slugs_emit_tasks, and
  emit_tasks_yaml writes slugs_emit_tasks: block. Backslash + quote
  escape pattern matches Phase A's bug-class fix.
- Six framework callsite templates updated (fastapi/django/flask/express/
  nestjs/nextjs) — import constants + use them in emit_*_safe(...) kwargs
  instead of inlining string literals. Falls back to inline literal when
  slug-inventory lookup misses.
- instrument/SKILL.md Phase 1.8 documentation.

### Out of scope

- Phase D: e2e customer-repo fixture + adversarial-review tuning
- Pre-existing backslash YAML escape bugs in attribution_discovery.py /
  sdk_snapshot.py (Phase A sibling-search finding; separate follow-up PR)

### Coordination with Phase B (PR #4)

Phase B (env-wire) and Phase C (slugs) touch DISJOINT regions of the
framework callsite templates. Phase B's customer-visible change is the
helper template's _resolve_api_key shape; Phase C's is the callsites'
imports + constant references. The two PRs can merge in either order.
If Phase B merges first, this PR rebases; if this PR merges first, PR
#4 rebases.

### Test plan

- [x] Smoke green throughout (~75/75 after all Phase C tasks).
- [x] task_planner unit tests cover slug-inventory load + index + per-
      callsite resolution + slugs_emit_tasks emission.
- [x] Slugs template renders + py-compile / gofmt clean for all 3
      languages + 5 categories.
- [x] Framework callsite templates: positive (constants present) +
      negative-leakage (no string-literal event_type / meter_slug).
- [x] End-to-end CLI smoke: slug-inventory.yaml → task_planner → tasks.yaml
      with slugs_emit_tasks block.
- [ ] Phase D fixture: real customer repo → discovery → slug-inventory →
      Phase C emit → rendered slugs module + callsites importing from it."
```

---

## Spec coverage check

| Spec section | Implementing task(s) |
|---|---|
| Phase C emission target — slugs Jinja templates | Tasks 2-4 |
| task_planner extension for slugs_emit_tasks | Tasks 5-6 |
| Framework callsite import-instead-of-literal updates | Tasks 7-8 |
| Phase 1.8 SKILL.md docs | Task 9 |
| Phase 7 smoke assertions (positive + negative-leakage) | Tasks 2-4, 7-8 inline |

All Phase C spec requirements have a task. Phase D out of scope.

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-06-env-routing-and-slugs-phase-c.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task; review between tasks; fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints.

Which approach?
