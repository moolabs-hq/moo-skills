"""task_planner.py — Phase 2c of /cost-billing-instrument.

Read the chain handoff YAMLs + inventories + Phase 1.5 snapshot; emit
`.moolabs/codemod/tasks.yaml` with one task per (file, [callsites in this
file]) tuple. Each task is self-contained — it carries ONLY the inventory
slice, the matching output-input-map edges, the helper template path, the
adapter binding, and the SDK snapshot capability flags relevant to its
callsites.

The dispatcher (Phase 2d) reads tasks.yaml, fires one focused Agent subagent
per task, and writes results to execution-log.yaml. Per-file granularity:
atomic commit boundary, single rendering pass per file (so a per-file
py_compile can verify the file before the task completes), parallelizable
across files. Per-callsite would over-fragment; per-service would re-introduce
the big-context problem.

Usage:
    python task_planner.py \
        --customer-context-dir .moolabs/customer-context \
        --inventory-dir .moolabs/inventory \
        --snapshot .moolabs/customer-context/sdk-surface-snapshot.yaml \
        --signed-yaml .moolabs/chain/04-final.signed.yaml \
        --output .moolabs/codemod/tasks.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# The de-hardcoded import-path rules live in the shared framework-capability
# tree (shared/scripts/strategies.py), exposed via IMPORT_RULES. The slugs-path
# helpers below reuse them — same sys.path-insert pattern as config_wire /
# env_loader_scan so all three layers resolve the same shared module.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))
import strategies  # noqa: E402

# Language → (IMPORT_RULES key, file extension) for the slugs-path derivation.
_SLUGS_LANG_RULE: dict[str, tuple[str, str]] = {
    "python": ("python_package", "py"),
    "typescript": ("ts_alias", "ts"),
    "go": ("go_module", "go"),
}

# Template selection per (language, framework). Stays aligned with the
# helper templates checked into assets/codemod-templates/.
# Go is P0 (Decision #2 reversed 2026-05-28): the go-stdlib.j2 callsite template
# is in progress; the Go SDK helper (go-moolabs-client.go.j2) has landed. Until
# the callsite template exists, the planner's existence check below skips Go
# files with a clear message rather than emitting a task against a missing file.
TEMPLATE_MAP: dict[tuple[str, str], str] = {
    ("python", "fastapi"): "assets/codemod-templates/python-fastapi.j2",
    ("python", "django"): "assets/codemod-templates/python-django.j2",
    ("python", "flask"): "assets/codemod-templates/python-flask.j2",
    ("typescript", "express"): "assets/codemod-templates/typescript-express.j2",
    ("typescript", "nestjs"): "assets/codemod-templates/typescript-nestjs.j2",
    ("typescript", "nextjs"): "assets/codemod-templates/typescript-nextjs.j2",
    ("go", "net-http-stdlib"): "assets/codemod-templates/go-stdlib.j2",
}

# Per-language helper import that the rendered insert relies on. v0.3.0-rc1
# exposes three helpers (one per pattern); the framework callsite template
# emits only the one it needs, so the per-file rendered imports are a subset
# of these. Listed in full so tasks.yaml documents the complete surface.
HELPER_IMPORT: dict[str, str] = {
    "python": "from app.services.moolabs_client import emit_usage_event_safe, emit_cost_event_safe, emit_event_safe",
    "typescript": 'import { emitUsageEventSafe, emitCostEventSafe, emitEventSafe } from "@/services/moolabs-client";',
    "go": 'import "internal/moolabsclient"',
}

# Template paths in TEMPLATE_MAP are relative to the instrument skill root; used
# to existence-check a resolved template before emitting a task against it.
_SKILL_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Insert:
    """One emission site within a file. Bound to exactly one inventory entry."""
    line: int
    pattern: str  # sibling-pair | usage-only | cost-only
    entry: dict[str, Any]
    attribution_keys: list[str]
    # Per-file attribution sources from Phase 1.6 (with overrides applied).
    # None for a key means "skip" — template omits the corresponding attribute.
    attribution_sources: dict[str, str | None] = field(default_factory=dict)


@dataclass
class Task:
    """One task = one file + N inserts. Self-contained context for a subagent."""
    task_id: str
    file: str
    service_slug: str
    framework: str
    language: str
    template: str
    helper_import: str
    snapshot_capabilities: dict[str, Any]
    inserts: list[Insert] = field(default_factory=list)
    audit: dict[str, str] = field(default_factory=dict)


@dataclass
class EnvWireTask:
    """One env-wire task per service. Distinct from per-file callsite Tasks
    because env-wiring is service-scoped (one helper module per service,
    plus optional deployment stubs).

    PR #531 follow-up:
      - infra_discovery_gap: True when Phase A scanner found no terraform/
        k8s/dockerfile at either scope. The execution agent reads this
        and emits a DEVELOPER ACTION REQUIRED section in the PR body
        asking where the customer's IaC lives (covers non-standard paths
        like iac/, cdk/, pulumi/).
      - deployment_stubs entries now carry scope (service | repo). The
        execution agent renders scope=repo entries as CHECKLIST text in
        the PR body (file path + 'wire MOOLABS_API_KEY here by hand')
        and renders scope=service entries as actual file emissions.
    """
    task_id: str
    service_slug: str
    mode: str  # "modify" | "stub"
    settings_import_path: str
    api_key_accessor: str
    stub_emit_path: str | None
    deployment_stubs: list[dict]
    infra_discovery_gap: bool = False


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
            infra_discovery_gap=bool(svc.get("infra_discovery_gap", False)),
        ))
    return out


def _shasum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _read_yaml(path: Path) -> Any:
    """Minimal YAML reader — avoid PyYAML dep for v1.

    Handles the subset we need: top-level keys, nested dicts, lists of dicts
    (block-style), and inline {k: v, k: v} dicts on a single line. Returns
    `None` if the file doesn't parse.
    """
    if not path.exists():
        return None
    try:
        import yaml  # pyyaml available in most customer environments

        return yaml.safe_load(path.read_text())
    except ImportError:
        # Fallback: indentation-based naive parse. Not exhaustive, but the
        # inventory YAML files we generate have a stable shape that fits it.
        return _naive_yaml(path.read_text())


def _naive_yaml(text: str) -> dict[str, Any]:
    """Bare-bones YAML parser for our generated inventory files.

    Limitations: no anchors, no merge keys, no block scalars. Sufficient for
    the inventory + snapshot shapes the cost-billing suite emits.
    """
    out: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(0, out)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and stack[-1][0] >= indent and not isinstance(stack[-1][1], list):
            if stack[-1][0] > indent:
                stack.pop()
            else:
                break
        container = stack[-1][1]
        if line.startswith("- "):
            inner = line[2:].strip()
            if isinstance(container, list):
                if ":" in inner:
                    k, _, v = inner.partition(":")
                    item: dict[str, Any] = {k.strip(): _coerce(v.strip())}
                    container.append(item)
                    stack.append((indent + 2, item))
                else:
                    container.append(_coerce(inner))
        else:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if isinstance(container, list):
                if container and isinstance(container[-1], dict):
                    container[-1][key] = _coerce(value) if value else {}
                    if not value:
                        stack.append((indent, container[-1][key] if isinstance(container[-1][key], dict) else container[-1]))
                continue
            if value == "":
                child: dict[str, Any] = {}
                container[key] = child
                stack.append((indent + 2, child))
            elif value == "[]":
                container[key] = []
                stack.append((indent + 2, container[key]))
            else:
                container[key] = _coerce(value)
    return out


def _coerce(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1]
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1]
        out: dict[str, Any] = {}
        for pair in inner.split(","):
            if ":" in pair:
                k, _, v = pair.partition(":")
                out[k.strip()] = _coerce(v.strip())
        return out
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip("'\"")


def _build_output_input_index(om: dict[str, Any]) -> dict[str, list[str]]:
    """Build a usage_workflow_id → [cost_workflow_id, ...] index."""
    index: dict[str, list[str]] = defaultdict(list)
    edges = om.get("edges") if isinstance(om, dict) else None
    if not edges:
        return index
    for edge in edges:
        u = edge.get("usage_workflow_id") or edge.get("output")
        c = edge.get("cost_workflow_id") or edge.get("input")
        if u and c:
            index[u].append(c)
    return index


def _attribution_keys_for(framework: str) -> list[str]:
    """Default attribution-key allowlist per framework (legacy — kept for
    back-compat). Real source of truth is `.moolabs/customer-context/
    attribution-bindings.yaml` from Phase 1.6.

    FR-3 (2026-06-05): `tenant_id` removed from every framework's default list.
    The v0.3.0-rc1 SDK derives tenant identity server-side from the API key;
    the helpers do not pass it on the wire and the templates do not render it.
    Carrying it in this legacy list created a spurious "valid attribution key"
    that callers downstream might surface as required. Dropped to keep the
    legacy defaults consistent with the v0.3 helper contract.
    """
    if framework == "fastapi":
        return ["customer_id", "feature_key", "request_id", "consumer_agent"]
    if framework in ("express", "nestjs", "nextjs"):
        return ["customer_id", "feature_key", "request_id"]
    if framework == "django":
        return ["customer_id", "feature_key", "request_id"]
    return ["request_id", "customer_id"]


def _load_attribution_bindings(path: Path) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
    """Read attribution-bindings.yaml. Returns (default_bindings, overrides[])."""
    if not path.exists():
        return {}, []
    data = _read_yaml(path) or {}
    defaults: dict[str, str | None] = {}
    for key, val in (data.get("bindings") or {}).items():
        if isinstance(val, dict):
            defaults[key] = val.get("source")
        else:
            defaults[key] = None
    overrides = data.get("overrides") or []
    if not isinstance(overrides, list):
        overrides = []
    return defaults, overrides


def _resolve_sources_for_file(
    file_path: str,
    defaults: dict[str, str | None],
    overrides: list[dict[str, Any]],
) -> dict[str, str | None]:
    """Apply per-file override on top of service-level defaults."""
    resolved = dict(defaults)
    for ov in overrides:
        if ov.get("file") != file_path:
            continue
        for key, val in (ov.get("bindings") or {}).items():
            if isinstance(val, dict):
                resolved[key] = val.get("source")
    return resolved


def _pattern_for(entry: dict[str, Any], output_input_index: dict[str, list[str]]) -> str:
    """Match the inventory entry's classification against pattern rules."""
    classification = entry.get("classification") or entry.get("pattern")
    if classification in ("sibling-pair", "sibling_pair", "sibling-pair-consumer"):
        return "sibling-pair"
    if classification in ("usage-only", "usage_only"):
        return "usage-only"
    if classification in ("cost-only", "cost_only"):
        return "cost-only"
    wf = entry.get("workflow_id", "")
    if wf and output_input_index.get(wf):
        return "sibling-pair"
    return "usage-only"


def load_slug_inventory(path: Path) -> dict:
    """Read slug-inventory.yaml (Phase A's slug_inventory.py output).
    Returns {"products": []} on missing file, absent PyYAML, OR malformed
    YAML — degrades gracefully so a corrupted inventory doesn't crash the
    planner. PR #5 review I-1 fix."""
    if not path.exists():
        return {"products": []}
    try:
        import yaml
    except ImportError:
        return {"products": []}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        sys.stderr.write(
            f"WARN: slug-inventory.yaml at {path} is malformed "
            f"({type(exc).__name__}); degrading to empty inventory\n"
        )
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


def _default_product_slug(index: dict[str, dict[str, dict[str, str]]]) -> str:
    """Fallback product when an inventory entry carries no ``product_slug``: the
    sole product in the slug index (the common single-product case). Returns ""
    when the index has zero or >=2 products (multi-product requires an explicit
    per-entry product_slug). Fixes the v0.3 slug-resolution-returns-None gap where
    discovery emits entries without product_slug -> "" -> every lookup misses."""
    return next(iter(index)) if len(index) == 1 else ""


def _product_owning_value(
    index: dict[str, dict[str, dict[str, str]]],
    category: str,
    value: str | None,
) -> str:
    """Return the product_slug whose ``category`` bucket contains ``value``.

    Slug values (event_type / workflow_id) are globally-unique, namespaced
    identifiers (e.g. ``arc.shared.llmport-call``, ``meter.ingest.batch``), so
    a value identifies its owning product without needing the product_slug
    join key. On ambiguity (same value in >1 product — should be impossible
    given slug_inventory's duplicate guard) WARN and take the first. Returns
    "" when no product owns the value."""
    if not value:
        return ""
    owners = [p for p, cats in index.items() if value in (cats.get(category) or {})]
    if not owners:
        return ""
    if len(owners) > 1:
        sys.stderr.write(
            f"WARN: slug value {value!r} found in multiple products {owners}; "
            f"using {owners[0]!r}\n"
        )
    return owners[0]


def _effective_product_slug(
    index: dict[str, dict[str, dict[str, str]]],
    declared: str,
    event_type: str | None,
    workflow_id: str | None,
) -> str:
    """Determine the product to resolve slug constants against.

    Dogfood #5 (architectural): the earlier `_default_product_slug` fallback
    only works when every product collapses into ONE bucket — which happens
    *because* discovery drops per-entry product_slug. The moment discovery is
    fixed to emit real product_slug, a multi-product index has >=2 buckets,
    `_default_product_slug` returns "" and resolution silently breaks. That
    couples the downstream fix to the upstream bug.

    Resolution order (robust to 1 or N products, with or without product_slug):
      1. declared product_slug, if it exists in the index (fast path) ;
      2. the product that OWNS the event_type / workflow_id slug value
         (value-based — works for multi-product without the join key) ;
      3. `_default_product_slug` (sole-product fallback / "").
    """
    if declared and index.get(declared):
        return declared
    for category, value in (("EVENT_TYPE", event_type), ("METER_SLUG", workflow_id)):
        owner = _product_owning_value(index, category, value)
        if owner:
            return owner
    return declared or _default_product_slug(index)


@dataclass
class SlugsEmitTask:
    """One slugs-emit task per product. Renders slugs-<lang>.j2 to
    <slugs_emit_path> based on the inventory's per-product constants.

    slugs_emit_path (repo-relative) is derived from the SERVICE's stub anchor
    via slugs_paths_from_stub (a sibling of the env-wire stub module), so the
    slugs file lands in the SAME package as the customer's real config — NOT a
    hardcoded app/services/moolabs/ dir. None when no single stub anchor is
    available (modify mode / multi-service ambiguity); render_artifacts then
    falls back to its legacy convention."""
    task_id: str
    product_slug: str
    constants: dict          # per-category {name, value} lists from slug-inventory
    generated_at: str        # from slug-inventory.yaml
    slugs_emit_path: str | None = None


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
        # provider isn't carried per-callsite in this codepath; discovered
        # per-cost-event but not joined here. Future work.
        "provider_const": _lookup("PROVIDER", None),
        "span_type_const": _lookup("SPAN_TYPE", cost_kind),
    }


def stub_anchor(env_wire_tasks: list[EnvWireTask] | None) -> tuple[str, str] | None:
    """Pick the (stub_emit_path, settings_import_path) anchor that products'
    slugs modules hang off of — a REAL customer location, never a hardcode.

    The inventory carries NO product→service edge, so we can't map a product to
    the specific service whose package its slugs belong in. Returns:
      - (stub_emit_path, settings_import_path) of the FIRST stub-mode env-wire
        task with a non-null stub_emit_path ;
      - None ONLY when there are zero stubs (every service is modify mode → no
        stub file to anchor on).

    MULTI-SERVICE LIMITATION (documented, NOT a hardcode): when 2+ services are
    stubbed, all products' slugs anchor on the FIRST service's stub package.
    This is a customer-derived best-effort — strictly better than the old
    `app/services/moolabs` literal (PR #11 review F2). A product whose callsites
    live in a DIFFERENT service imports slugs from the first service's package,
    which only resolves if that package is importable repo-wide. The correct fix
    is a product→service edge so each product anchors on ITS service's stub;
    tracked as a follow-up. Only the zero-stub (all-modify) case falls through to
    the legacy convention.
    """
    if not env_wire_tasks:
        return None
    for t in env_wire_tasks:
        if t.mode == "stub" and t.stub_emit_path:
            return (t.stub_emit_path, t.settings_import_path or "")
    return None


def build_slugs_emit_tasks(
    inventory: dict,
    language: str = "python",
    anchor: tuple[str, str] | None = None,
) -> list[SlugsEmitTask]:
    """One slugs-emit task per product in the inventory.

    When ``anchor`` (the service's stub (emit_path, import_path)) is provided
    AND the language is python, each task's ``slugs_emit_path`` is derived as a
    SIBLING of the stub via ``slugs_paths_from_stub`` — landing the slugs file in
    the customer's real config package. Otherwise ``slugs_emit_path`` stays None
    and render_artifacts falls back to its legacy convention.

    The python gate MUST match the import gate in ``_slugs_import_for_entry``
    (PR #11 review IMP-1): the basename/dotted swap is python-shaped and would
    corrupt TS ``@/`` aliases / Go slash paths, so TS/Go keep the legacy path on
    BOTH the emit and the import side — emitting anchor-derived here while the
    import stays legacy wrote the slugs module where no callsite imports it.
    Anchor-derived TS/Go paths are a follow-up (needs ts_alias/go_module
    emit+import derivation through strategies.IMPORT_RULES). ``language`` selects
    the file extension for the swapped basename."""
    out: list[SlugsEmitTask] = []
    generated_at = inventory.get("generated_at", "")
    _rule, ext = _SLUGS_LANG_RULE.get(language, _SLUGS_LANG_RULE["python"])
    for idx, product in enumerate(inventory.get("products") or [], start=1):
        slug = product.get("product_slug", "")
        if not slug:
            continue
        slugs_emit_path: str | None = None
        if anchor is not None and language == "python":
            stub_emit, stub_import = anchor
            slugs_emit_path, _imp = slugs_paths_from_stub(
                stub_emit, stub_import, slug, ext
            )
        out.append(SlugsEmitTask(
            task_id=f"slugs_emit_{idx:03d}_{slug}",
            product_slug=slug,
            constants=product.get("constants") or {},
            generated_at=generated_at,
            slugs_emit_path=slugs_emit_path,
        ))
    return out


def _slugs_import_path_for(language: str, product_slug: str) -> str:
    """Return the language-appropriate import path for a per-product slugs
    module. Conventions match Phase C SKILL.md Phase 1.8:
      - python: app.services.moolabs.slugs_<product>
      - typescript: @/services/moolabs/slugs_<product>
      - go: internal/moolabsclient/slugs_<product>
    Defaults to the python convention when language is unknown."""
    safe = (product_slug or "").replace("-", "_")
    if language == "typescript":
        return f"@/services/moolabs/slugs_{safe}"
    if language == "go":
        return f"internal/moolabsclient/slugs_{safe}"
    return f"app.services.moolabs.slugs_{safe}"


def slugs_import_path(language: str, product: str, anchor_dir: str) -> str:
    """Derive the per-product slugs import path from a SERVICE-RELATIVE anchor
    directory (the dir holding the service's detected config/stub module).

    The slugs module lives in that same directory under basename
    `slugs_<product_safe>`. We build a pseudo config-file path in that dir and
    run the shared `strategies.IMPORT_RULES[<rule>]` so the import path follows
    the exact same convention as the env-wire stub placement (src/ stripping,
    nested-package dotting, etc.) — no duplicated path logic here.
    """
    safe = (product or "").replace("-", "_")
    rule_name, ext = _SLUGS_LANG_RULE.get(language, _SLUGS_LANG_RULE["python"])
    basename = f"slugs_{safe}"
    pseudo = f"{anchor_dir}/{basename}.{ext}" if anchor_dir else f"{basename}.{ext}"
    _emit_dir, import_path = strategies.IMPORT_RULES[rule_name](pseudo, basename, product)
    return import_path


def slugs_paths_from_stub(
    stub_emit_path: str, stub_import_path: str, product: str, ext: str,
) -> tuple[str, str]:
    """Derive a product's slugs (emit_path, import_path) as a sibling of the
    service's stub module — a BASENAME SWAP, not a re-derivation.

    - emit_path: same directory as ``stub_emit_path`` with basename
      ``slugs_<product_safe>.<ext>`` (e.g.
      ``services/svc/src/myapp/moolabs_settings.py`` ->
      ``services/svc/src/myapp/slugs_billing.py``).
    - import_path: ``stub_import_path`` with its LAST dotted segment replaced by
      ``slugs_<product_safe>`` (e.g. ``myapp.moolabs_settings`` ->
      ``myapp.slugs_billing``; a bare ``moolabs_settings`` -> ``slugs_billing``).
    """
    safe = (product or "").replace("-", "_")
    basename = f"slugs_{safe}"
    emit_p = (stub_emit_path or "").replace("\\", "/")
    parts = emit_p.split("/")
    parts[-1] = f"{basename}.{ext}"
    emit_path = "/".join(parts)

    dotted = (stub_import_path or "").split(".")
    dotted[-1] = basename
    import_path = ".".join(dotted)
    return emit_path, import_path


def _slugs_import_for_entry(
    language: str, product_slug: str, anchor: tuple[str, str] | None,
) -> str | None:
    """Per-callsite slugs import path. Returns None when product_slug is empty
    (the import block is gated off downstream). When a stub ``anchor`` is
    available AND the language is python, derive the import as a sibling of the
    stub module (its real package); otherwise fall back to the legacy hardcoded
    convention. The dotted last-segment swap is python-shaped — it corrupts TS
    ``@/`` aliases and Go slash paths — so TS/Go always use the legacy path."""
    if not product_slug:
        return None
    if anchor is not None and language == "python":
        _emit, imp = slugs_paths_from_stub(anchor[0], anchor[1], product_slug, "py")
        return imp
    return _slugs_import_path_for(language, product_slug)


def build_tasks(
    cost_inventory: dict[str, Any],
    usage_inventory: dict[str, Any],
    output_input_map: dict[str, Any],
    snapshot: dict[str, Any],
    signed: dict[str, Any],
    repo_profile: dict[str, Any],
    attribution_defaults: dict[str, str | None] | None = None,
    attribution_overrides: list[dict[str, Any]] | None = None,
    slug_inventory: dict[str, Any] | None = None,
    anchor: tuple[str, str] | None = None,
) -> list[Task]:
    """Group inventory entries by file, emit one task per file.

    Phase C (PR #5 review CRIT fix): when slug_inventory is provided, build
    the per-product slug index and resolve per-callsite constant names +
    slugs_import_path for each Insert.entry. The framework callsite
    templates read these to render `event_type=EVENT_TYPE_X` (bare
    identifier) instead of `event_type="x.y"` (string literal). Without
    this wiring, the constants are never set and every callsite falls
    back to the literal — defeating the slugs-as-source-of-truth contract.

    Task 12: the per-callsite slugs_import_path is derived from the SERVICE's
    stub ``anchor`` (its (emit_path, import_path)) via a basename swap, so the
    import targets the slugs module that actually gets emitted beside the
    customer's real config. Stub-anchored derivation is Python-only — a dotted
    last-segment swap is wrong for TS (``@/`` aliases) and Go (slash paths), so
    those languages, and the no-anchor case, fall back to the legacy
    convention in ``_slugs_import_path_for``.
    """
    output_input_index = _build_output_input_index(output_input_map)
    slug_index = build_slug_index(slug_inventory or {"products": []})
    capabilities = snapshot.get("capabilities", {}) if snapshot else {}
    service_slug = signed.get("service_slug", "unknown") if signed else "unknown"
    repo = signed.get("repo", {}) if signed else {}
    languages = repo.get("languages", []) if isinstance(repo, dict) else []
    frameworks = repo.get("frameworks", []) if isinstance(repo, dict) else []
    primary_lang = (languages[0] if languages else (repo_profile.get("language", "python") if isinstance(repo_profile, dict) else "python"))
    primary_fw = (frameworks[0] if frameworks else (repo_profile.get("framework", "fastapi") if isinstance(repo_profile, dict) else "fastapi"))

    # Group entries by file across both inventories.
    file_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in (usage_inventory.get("entries") if isinstance(usage_inventory, dict) else []) or []:
        if not isinstance(entry, dict):
            continue
        f = entry.get("file") or entry.get("path")
        if f:
            file_buckets[f].append(entry)
    for entry in (cost_inventory.get("entries") if isinstance(cost_inventory, dict) else []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("instrument") is False:
            continue
        f = entry.get("file") or entry.get("path")
        if f:
            file_buckets[f].append(entry)

    tasks: list[Task] = []
    for idx, (file_path, entries) in enumerate(sorted(file_buckets.items()), start=1):
        template = TEMPLATE_MAP.get((primary_lang, primary_fw), TEMPLATE_MAP.get((primary_lang, "fastapi"), ""))
        if not template:
            sys.stderr.write(
                f"WARN: no template for ({primary_lang}, {primary_fw}); skipping {file_path}\n"
            )
            continue
        if not (_SKILL_ROOT / template).exists():
            sys.stderr.write(
                f"WARN: template {template} for ({primary_lang}, {primary_fw}) not yet "
                f"implemented; skipping {file_path} (no broken task emitted)\n"
            )
            continue
        sources_for_file = _resolve_sources_for_file(
            file_path, attribution_defaults or {}, attribution_overrides or []
        )
        inserts: list[Insert] = []
        for entry in entries:
            pattern = _pattern_for(entry, output_input_index)
            wf = entry.get("workflow_id", "")
            cost_workflow_ids = output_input_index.get(wf, [])
            # Phase C (PR #5 review CRIT fix): resolve per-callsite slug
            # constants from the slug-inventory + index. Each entry carries
            # its product_slug from Phase A; the resolver returns
            # event_type_const / meter_slug_const / feature_key_const /
            # span_type_const + slugs_import_path so the framework callsite
            # templates can render `event_type=EVENT_TYPE_X` instead of
            # `event_type="x.y"`. None values trigger the template's
            # inline-literal fallback path.
            # Dogfood #5: resolve the effective product robustly — declared
            # product_slug wins, else find the product owning the event_type/
            # workflow_id slug value (works for multi-product without the join
            # key), else sole-product fallback. Decoupled from the single-bucket
            # accident so it survives the upstream discovery fix that will emit
            # real per-entry product_slug.
            product_slug = _effective_product_slug(
                slug_index,
                declared=entry.get("product_slug") or "",
                event_type=entry.get("event_type"),
                workflow_id=wf,
            )
            slug_consts = resolve_slug_constants(
                slug_index,
                product_slug=product_slug,
                event_type=entry.get("event_type"),
                workflow_id=wf,
                cost_kind=entry.get("cost_kind"),
            )
            enriched_entry = {
                **entry,
                "cost_workflow_ids": cost_workflow_ids,
                **slug_consts,
                # Task 12: derive the per-callsite slugs import path from the
                # SERVICE's stub anchor (basename swap on the stub's import
                # path) when one is available + python; else the legacy
                # convention. Empty product_slug -> None (the import block is
                # gated off downstream, so this is purely a tasks.yaml
                # data-quality choice — rendered source is unaffected because
                # all _const keys are None too).
                "slugs_import_path": _slugs_import_for_entry(
                    primary_lang, product_slug, anchor
                ),
            }
            inserts.append(
                Insert(
                    line=int(entry.get("line", 0) or 0),
                    pattern=pattern,
                    entry=enriched_entry,
                    attribution_keys=_attribution_keys_for(primary_fw),
                    attribution_sources=sources_for_file,
                )
            )
        # stable task id
        task_id = f"tsk_{idx:03d}_{_shasum(file_path)}"
        tasks.append(
            Task(
                task_id=task_id,
                file=file_path,
                service_slug=service_slug,
                framework=primary_fw,
                language=primary_lang,
                template=template,
                helper_import=HELPER_IMPORT.get(primary_lang, ""),
                # v0.3.0-rc1 capability flags. Helpers no longer branch on
                # these (they unconditionally call the ergonomic methods);
                # carried into tasks.yaml as a forensic record so downstream
                # audit / debugging can confirm which SDK shape was pinned.
                snapshot_capabilities={
                    "unified_ingest_present": capabilities.get("unified_ingest_present", False),
                    "usage_ergonomic_ingest": capabilities.get("usage_ergonomic_ingest", False),
                    "cost_ergonomic_ingest": capabilities.get("cost_ergonomic_ingest", False),
                    "events_unified_namespace": capabilities.get("events_unified_namespace", False),
                    "usage_method_path": capabilities.get("usage_method_path"),
                    "cost_method_path": capabilities.get("cost_method_path"),
                    "events_method_path": capabilities.get("events_method_path"),
                },
                inserts=sorted(inserts, key=lambda i: i.line),
                audit={
                    "cost_inventory_sha": _shasum(str(cost_inventory)),
                    "output_input_map_sha": _shasum(str(output_input_map)),
                    "snapshot_sha": _shasum(str(snapshot)),
                },
            )
        )
    return tasks


def emit_tasks_yaml(
    tasks: list[Task],
    dest: Path,
    env_wire_tasks: list[EnvWireTask] | None = None,
    slugs_emit_tasks: list[SlugsEmitTask] | None = None,
) -> None:
    lines: list[str] = []
    # Quote generated_at so PyYAML safe_load keeps it a STRING. Unquoted
    # ISO-8601 is auto-coerced to a datetime.datetime — a downstream consumer
    # doing string ops then crashes (dogfood #3). ISO timestamps never contain
    # `"` or `\`, so a bare double-quote wrap is sufficient. The
    # slugs_emit_tasks generated_at was already quoted; this top-level one was
    # the one commit 42d51a8 missed.
    lines.append(f'generated_at: "{datetime.now(timezone.utc).isoformat()}"')
    lines.append(f"total_tasks: {len(tasks)}")
    lines.append(f"total_inserts: {sum(len(t.inserts) for t in tasks)}")
    lines.append("tasks:")
    for t in tasks:
        lines.append(f"  - task_id: {t.task_id}")
        lines.append(f"    file: {t.file}")
        lines.append(f"    service_slug: {t.service_slug}")
        lines.append(f"    framework: {t.framework}")
        lines.append(f"    language: {t.language}")
        lines.append(f"    template: {t.template}")
        lines.append(f'    helper_import: "{t.helper_import}"')
        lines.append("    snapshot_capabilities:")
        for k, v in t.snapshot_capabilities.items():
            if v is None:
                lines.append(f"      {k}: null")
            elif isinstance(v, bool):
                lines.append(f"      {k}: {str(v).lower()}")
            else:
                lines.append(f'      {k}: "{v}"')
        lines.append("    inserts:")
        for ins in t.inserts:
            lines.append(f"      - line: {ins.line}")
            lines.append(f"        pattern: {ins.pattern}")
            lines.append(f"        attribution_keys: [{', '.join(ins.attribution_keys)}]")
            lines.append("        attribution_sources:")
            for k, v in ins.attribution_sources.items():
                if v is None:
                    lines.append(f"          {k}: null")
                else:
                    lines.append(f'          {k}: "{v}"')
            lines.append("        entry:")
            for k, v in ins.entry.items():
                if isinstance(v, dict):
                    lines.append(f"          {k}:")
                    for sk, sv in v.items():
                        lines.append(f"            {sk}: {sv!r}" if isinstance(sv, str) else f"            {sk}: {sv}")
                elif isinstance(v, list):
                    inline = ", ".join(repr(x) if isinstance(x, str) else str(x) for x in v)
                    lines.append(f"          {k}: [{inline}]")
                elif isinstance(v, str):
                    lines.append(f"          {k}: {v!r}")
                else:
                    lines.append(f"          {k}: {v}")
        lines.append("    audit:")
        for k, v in t.audit.items():
            lines.append(f"      {k}: {v}")
    # Phase 1.7 env-wire tasks (one per service). Backslash + quote escape
    # follows Phase A's YAML emit-bug-class fix.
    if env_wire_tasks:
        lines.append("env_wire_tasks:")
        for t in env_wire_tasks:
            # task_id and service_slug are derived from customer-authored
            # service slugs from the Phase A inventory; quote them to defend
            # against YAML metacharacters. mode is a hardcoded enum.
            safe_tid = t.task_id.replace('\\', '\\\\').replace('"', '\\"')
            safe_slug = t.service_slug.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'  - task_id: "{safe_tid}"')
            lines.append(f'    service_slug: "{safe_slug}"')
            lines.append(f"    mode: {t.mode}")
            safe_import = t.settings_import_path.replace('\\', '\\\\').replace('"', '\\"')
            safe_accessor = t.api_key_accessor.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'    settings_import_path: "{safe_import}"')
            lines.append(f'    api_key_accessor: "{safe_accessor}"')
            if t.stub_emit_path:
                safe_stub = t.stub_emit_path.replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'    stub_emit_path: "{safe_stub}"')
            else:
                lines.append(f"    stub_emit_path: null")
            # PR #531 gap-detection: execution agent reads this and emits
            # a DEVELOPER ACTION REQUIRED block in the PR body when True.
            lines.append(f"    infra_discovery_gap: {str(t.infra_discovery_gap).lower()}")
            if t.deployment_stubs:
                lines.append("    deployment_stubs:")
                for s in t.deployment_stubs:
                    lines.append(f"      - kind: {s['kind']}")
                    # source_path was added in PR #531 fix so the execution
                    # agent knows which existing file to reference in the
                    # CHECKLIST for repo-scope (centralized infra) entries.
                    if "source_path" in s:
                        safe_src = str(s['source_path']).replace('\\', '\\\\').replace('"', '\\"')
                        lines.append(f'        source_path: "{safe_src}"')
                    if "emit_path" in s:
                        safe_emit = str(s['emit_path']).replace('\\', '\\\\').replace('"', '\\"')
                        lines.append(f'        emit_path: "{safe_emit}"')
                    lines.append(f"        mode: {s['mode']}")
                    # scope: service (auto-emit safe) | repo (checklist only)
                    lines.append(f"        scope: {s.get('scope', 'service')}")
            else:
                lines.append("    deployment_stubs: []")
    # Phase 1.8 slugs-emit tasks (one per product). Same escape pattern.
    if slugs_emit_tasks:
        lines.append("slugs_emit_tasks:")
        for st in slugs_emit_tasks:
            lines.append(f"  - task_id: {st.task_id}")
            lines.append(f"    product_slug: {st.product_slug}")
            safe_gen_at = str(st.generated_at).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    generated_at: "{safe_gen_at}"')
            # Task 12: repo-relative emit path of the slugs module, derived
            # from the service's stub anchor (sibling of the customer's real
            # config). null when no single stub anchor was available — Task 13
            # (render_artifacts) then falls back to its legacy convention.
            if st.slugs_emit_path:
                safe_emit = str(st.slugs_emit_path).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'    slugs_emit_path: "{safe_emit}"')
            else:
                lines.append("    slugs_emit_path: null")
            # The constants block is rendered as a nested mapping.
            lines.append("    constants:")
            for category in ("EVENT_TYPE", "METER_SLUG", "FEATURE_KEY",
                             "PROVIDER", "SPAN_TYPE"):
                entries = st.constants.get(category, []) or []
                if not entries:
                    lines.append(f"      {category}: []")
                    continue
                lines.append(f"      {category}:")
                for e in entries:
                    safe_name = str(e.get("name", "")).replace("\\", "\\\\").replace('"', '\\"')
                    safe_value = str(e.get("value", "")).replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'        - name: "{safe_name}"')
                    lines.append(f'          value: "{safe_value}"')
    dest.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--inventory-dir", default=".moolabs/inventory")
    ap.add_argument("--snapshot", default=".moolabs/customer-context/sdk-surface-snapshot.yaml")
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
    ap.add_argument(
        "--slug-inventory",
        default=".moolabs/customer-context/slug-inventory.yaml",
    )
    ap.add_argument("--output", default=".moolabs/codemod/tasks.yaml")
    ap.add_argument("--config-wiring-plan", default=".moolabs/customer-context/config-wiring-plan.yaml")
    args = ap.parse_args(argv)

    inv_dir = Path(args.inventory_dir)
    cost_inv = _read_yaml(inv_dir / "cost-events-inventory.yaml") or {}
    usage_inv = _read_yaml(inv_dir / "usage-events-inventory.yaml") or {}
    omap = _read_yaml(inv_dir / "output-input-map.yaml") or {}
    snapshot = _read_yaml(Path(args.snapshot)) or {}
    signed = _read_yaml(Path(args.signed_yaml)) or {}
    repo_profile = _read_yaml(Path(args.customer_context_dir) / "repo-info.yaml") or {}

    bindings_path = Path(args.customer_context_dir) / "attribution-bindings.yaml"
    attribution_defaults, attribution_overrides = _load_attribution_bindings(bindings_path)
    if not attribution_defaults:
        sys.stderr.write(
            f"REFUSING TO RUN: attribution bindings not found at {bindings_path}\n"
            f"  Run Phase 1.6 first:\n"
            f"    python scripts/attribution_discovery.py --service-root <path> --framework <name>\n"
            f"  Templates cannot emit without confirmed attribution sources.\n"
        )
        return 2
    # NOTE: feature_key is NOT required — per-callsite templates derive it from
    # entry.workflow_id at render time. Customers don't need a middleware-set source.
    #
    # FR-3: tenant_id is NOT required. The v0.3.0-rc1 SDK derives tenant identity
    # server-side from the API key; the helpers do not pass it on the wire and the
    # planner doesn't need it bound to render correctly. Discovery may still emit
    # the tenant_id binding for forensic / audit purposes — accepted but optional.
    #
    # consumer_agent is OPTIONAL metadata — a customer may legitimately have no
    # binding for it (the example fixture sets source: null with confidence n_a).
    # It is not required.
    #
    # Two keys are required AND must be bound to a non-null source expression:
    # customer_id (billing identity) and request_id (entity_id threading key).
    # A null binding for either silently degrades downstream data quality —
    # customer_id null buckets every emission under a literal "unknown" customer;
    # request_id null defeats sibling-pair cross-lane joins. The gate must catch
    # both "key absent" AND "key present but source is null" cases.
    required = ["customer_id", "request_id"]
    missing_or_null = [
        k for k in required
        if attribution_defaults.get(k) is None  # covers both missing-key and source: null
    ]
    if missing_or_null:
        sys.stderr.write(
            f"REFUSING TO RUN: attribution-bindings.yaml is missing or null for required keys: {missing_or_null}\n"
            f"  Each key must be bound to a non-null source expression (e.g. 'request.state.customer_id').\n"
            f"  A source: null binding is treated as 'not bound' — re-run Phase 1.6 to confirm.\n"
        )
        return 2

    # Phase C (PR #5 review CRIT fix): load slug-inventory BEFORE build_tasks
    # so build_tasks can resolve per-callsite slug constants for each Insert.
    # Missing file / malformed YAML / absent PyYAML all degrade to empty
    # inventory (load_slug_inventory handles all three). Without this,
    # entry.event_type_const stays None and templates fall back to inline
    # string literals — defeating Phase C's slugs-as-source-of-truth contract.
    slug_inv = load_slug_inventory(Path(args.slug_inventory))

    # Task 12: build env-wire tasks FIRST (was after build_tasks) so the
    # service's stub anchor — its (emit_path, import_path) — is available to
    # both the per-callsite slugs import path (build_tasks) and the per-product
    # slugs_emit_path (build_slugs_emit_tasks). Both derive from the SAME anchor
    # so the emitted slugs file's package matches the import that references it.
    env_wire_tasks = build_env_wire_tasks(Path(args.config_wiring_plan))
    anchor = stub_anchor(env_wire_tasks)

    # Mirror build_tasks' primary-language selection so build_slugs_emit_tasks
    # picks the right file extension (py/ts/go) for the swapped slugs basename.
    repo = signed.get("repo", {}) if isinstance(signed, dict) else {}
    languages = repo.get("languages", []) if isinstance(repo, dict) else []
    primary_lang = (
        languages[0] if languages
        else (repo_profile.get("language", "python")
              if isinstance(repo_profile, dict) else "python")
    )

    tasks = build_tasks(
        cost_inv, usage_inv, omap, snapshot, signed, repo_profile,
        attribution_defaults=attribution_defaults,
        attribution_overrides=attribution_overrides,
        slug_inventory=slug_inv,
        anchor=anchor,
    )
    if not tasks:
        sys.stderr.write("no tasks built — check inventory files exist + non-empty\n")
        return 1

    # Phase 1.8 slugs-emit tasks (Phase C). slug_inv was loaded BEFORE
    # build_tasks above so build_tasks could resolve per-callsite slug
    # constants — reuse the same inventory here. Task 12: pass the language +
    # stub anchor so each task's slugs_emit_path lands beside the customer's
    # real config (None when no single anchor — render_artifacts falls back).
    slugs_emit_tasks = build_slugs_emit_tasks(slug_inv, primary_lang, anchor)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    emit_tasks_yaml(
        tasks, out_path,
        env_wire_tasks=env_wire_tasks,
        slugs_emit_tasks=slugs_emit_tasks,
    )
    print(
        f"wrote {out_path} ({len(tasks)} tasks, "
        f"{sum(len(t.inserts) for t in tasks)} inserts, "
        f"{len(env_wire_tasks)} env-wire tasks, "
        f"{len(slugs_emit_tasks)} slugs-emit tasks)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
