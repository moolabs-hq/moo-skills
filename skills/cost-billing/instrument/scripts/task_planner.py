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
    plus optional deployment stubs)."""
    task_id: str
    service_slug: str
    mode: str  # "modify" | "stub"
    settings_import_path: str
    api_key_accessor: str
    stub_emit_path: str | None
    deployment_stubs: list[dict]


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


def build_tasks(
    cost_inventory: dict[str, Any],
    usage_inventory: dict[str, Any],
    output_input_map: dict[str, Any],
    snapshot: dict[str, Any],
    signed: dict[str, Any],
    repo_profile: dict[str, Any],
    attribution_defaults: dict[str, str | None] | None = None,
    attribution_overrides: list[dict[str, Any]] | None = None,
) -> list[Task]:
    """Group inventory entries by file, emit one task per file."""
    output_input_index = _build_output_input_index(output_input_map)
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
            enriched_entry = {**entry, "cost_workflow_ids": cost_workflow_ids}
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


def emit_tasks_yaml(tasks: list[Task], dest: Path, env_wire_tasks: list[EnvWireTask] | None = None) -> None:
    lines: list[str] = []
    lines.append(f"generated_at: {datetime.now(timezone.utc).isoformat()}")
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
            lines.append(f"  - task_id: {t.task_id}")
            lines.append(f"    service_slug: {t.service_slug}")
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
    dest.write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--inventory-dir", default=".moolabs/inventory")
    ap.add_argument("--snapshot", default=".moolabs/customer-context/sdk-surface-snapshot.yaml")
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
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

    tasks = build_tasks(
        cost_inv, usage_inv, omap, snapshot, signed, repo_profile,
        attribution_defaults=attribution_defaults,
        attribution_overrides=attribution_overrides,
    )
    if not tasks:
        sys.stderr.write("no tasks built — check inventory files exist + non-empty\n")
        return 1

    env_wire_tasks = build_env_wire_tasks(Path(args.config_wiring_plan))
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    emit_tasks_yaml(tasks, out_path, env_wire_tasks)
    print(f"wrote {out_path} ({len(tasks)} tasks, {sum(len(t.inserts) for t in tasks)} inserts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
