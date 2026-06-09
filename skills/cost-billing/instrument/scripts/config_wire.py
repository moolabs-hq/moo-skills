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

# Framework-capability tree. The registry is the single source of truth for
# per-node wiring (mode + accessor). Locate the shared dir across BOTH the
# SOURCE monorepo (cost-billing/shared) and the INSTALLED layout (shared ships
# as the sibling skill `cost-billing-shared`) — a hardcoded parents[2]/"shared"
# crashed installed users with ModuleNotFoundError (2026-06-08 dogfood fix).
def _locate_shared_base() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        for name in ("shared", "cost-billing-shared"):
            base = parent / name
            if (base / "scripts" / "strategies.py").is_file():
                return base
    return None


_SHARED_BASE = _locate_shared_base()
if _SHARED_BASE is not None:
    sys.path.insert(0, str(_SHARED_BASE / "scripts"))
import framework_registry  # noqa: E402
import secret_exemplar  # noqa: E402

_FRAMEWORKS_DIR = (
    (_SHARED_BASE / "assets" / "frameworks")
    if _SHARED_BASE is not None
    else Path(__file__).resolve().parents[2] / "shared" / "assets" / "frameworks"
)


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


# ──────────────────────────────────────────────────────────────────────
# Per-service plan derivation
# ──────────────────────────────────────────────────────────────────────

def _python_settings_import_path(file_path: str, service_slug: str = "") -> str:
    """Derive the Python import path for the customer's settings module.

    Convention:
      services/<svc>/<pkg>/config.py   → <pkg>.config
      <slug>/app/settings.py           → app.settings  (slug prefix stripped)
      packages/config/settings.py      → packages.config.settings
    """
    parts = file_path.split("/")
    # Strip leading "services/<svc>/" if present (two segments)
    if len(parts) >= 2 and parts[0] == "services":
        parts = parts[2:]
    # Strip leading service-slug segment when it matches
    elif service_slug and parts and parts[0] == service_slug:
        parts = parts[1:]
    # Drop the .py extension from the final segment
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _ts_settings_import_path(file_path: str, service_slug: str = "") -> str:
    """Derive the TS import path. Convention: `@/<modulepath>` aliased to
    the source root (matches Next.js / many React app conventions).

      src/env.ts                          → @/env
      src/config.ts                       → @/config
      services/<svc>/src/env.ts           → @/env  (service-relative)
      <svc>/src/env.ts (bare slug prefix) → @/env
    """
    parts = file_path.split("/")
    # Strip leading "services/<svc>/"
    if len(parts) >= 2 and parts[0] == "services":
        parts = parts[2:]
    # Strip bare-slug prefix (matches Task 3 helper convention)
    elif service_slug and parts and parts[0] == service_slug:
        parts = parts[1:]
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


def _go_settings_import_path(file_path: str, service_slug: str = "") -> str:
    """Derive the Go import path. Convention: drop the filename and emit
    the remaining package path as-is.

      internal/config/config.go            → internal/config
      services/<svc>/internal/config/config.go → internal/config
      <svc>/internal/config/config.go (bare slug) → internal/config
    """
    parts = file_path.split("/")
    if len(parts) >= 2 and parts[0] == "services":
        parts = parts[2:]
    elif service_slug and parts and parts[0] == service_slug:
        parts = parts[1:]
    # Drop the trailing filename if it ends in .go
    if parts and parts[-1].endswith(".go"):
        parts = parts[:-1]
    return "/".join(parts) if parts else "internal/config"


# Stub-mode accessor body per language. The stub artifact ALWAYS exposes a
# get_settings()/getSettings()/config.Get() entry point exporting
# moolabs_api_key, so this accessor is a constant — it is NOT a per-pattern
# choice. (The stub's emit_path/import_path now come from the inventory,
# derived from the customer's real layout in Phase A.)
_STUB_ACCESSORS = {
    "python":     "get_settings().moolabs_api_key.get_secret_value()",
    "typescript": "getSettings().MOOLABS_API_KEY",
    "go":         "config.Get().MoolabsAPIKey",
}


def _plan_deployment_stubs(surfaces: list[dict]) -> list[dict]:
    """Map each detected deployment surface to a stub-emit plan.

    Per the spec's "deployment-surface stubs" rule:
      - terraform / k8s: emit a NEW file alongside (never modify existing)
      - dotenv_example:  append a single line to the existing file
      - dockerfile:      checklist only (security smell — never auto-edit)

    Scope handling (PR-#531 fix):
      - scope="service" — auto-modify-safe; emit the stub as normal.
      - scope="repo"    — CENTRALIZED infra (one Terraform tree shared by
        every service). Auto-modifying it would affect ALL services
        simultaneously with cross-service blast radius. Downgrade to
        "checklist_only" mode regardless of kind — the customer must wire
        MOOLABS_API_KEY into the shared module by hand (typically into the
        ECS task-definition's `secrets:` block in modules/ecs-service/).
    """
    out: list[dict] = []
    for s in surfaces or []:
        kind = s.get("kind")
        path = s.get("path", "")
        scope = s.get("scope", "service")

        if scope == "repo":
            # Centralized infra: NEVER auto-emit a new file or append.
            # Instead, surface a CHECKLIST entry naming the file the
            # developer must edit. The instrument layer renders this into
            # the PR body so the developer knows exactly where MOOLABS_API_KEY
            # needs to be added in their existing modules.
            out.append({
                "kind": kind,
                "source_path": path,
                "mode": "checklist_only",
                "scope": "repo",
            })
            continue

        if kind == "terraform":
            # Emit moolabs.tf alongside the detected variables.tf
            dir_path = path.rsplit("/", 1)[0] if "/" in path else "."
            out.append({
                "kind": "terraform",
                "source_path": path,
                "emit_path": f"{dir_path}/moolabs.tf" if dir_path != "." else "moolabs.tf",
                "mode": "new_file",
                "scope": "service",
            })
        elif kind == "k8s":
            dir_path = path.rsplit("/", 1)[0] if "/" in path else "."
            out.append({
                "kind": "k8s",
                "source_path": path,
                "emit_path": f"{dir_path}/secret-moolabs.yaml" if dir_path != "." else "secret-moolabs.yaml",
                "mode": "new_file",
                "scope": "service",
            })
        elif kind == "docker-compose":
            out.append({
                "kind": "docker-compose",
                "source_path": path,
                "emit_path": path,  # appending to existing
                "mode": "append",
                "scope": "service",
            })
        elif kind == "dotenv_example":
            out.append({
                "kind": "dotenv_example",
                "source_path": path,
                "emit_path": path,  # appending to existing
                "mode": "append",
                "scope": "service",
            })
        elif kind == "dockerfile":
            out.append({
                "kind": "dockerfile",
                "source_path": path,
                "mode": "checklist_only",
                "scope": "service",
            })
    return out


_REGISTRY_CACHE: dict[str, dict[str, framework_registry.Node]] | None = None


def _registry() -> dict[str, dict[str, framework_registry.Node]]:
    """Load the framework-capability tree once and cache it. load_registry
    re-globs the filesystem each call, so we memoize for the per-service loop."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = framework_registry.load_registry(_FRAMEWORKS_DIR)
    return _REGISTRY_CACHE


def _resolve_node(
    node_key: str, language: str
) -> framework_registry.Node | None:
    """Find the Node whose .id == node_key within the language's framework set.

    reg[language] is keyed by FRAMEWORK NAME, not node id (e.g. key 'envconfig'
    holds the node whose id is 'go-envconfig'), so we scan values by .id rather
    than dict-get by node_key. Returns None when node_key is empty or no node
    matches (unrecognized service) → caller treats as stub."""
    if not node_key:
        return None
    for node in _registry().get(language, {}).values():
        if node.id == node_key:
            return node
    return None


def _detect_access_idiom(app_config: dict, repo_root, language: str):
    """SEARCH the customer's config for HOW it is read — a factory (`get_settings()`)
    vs a module singleton (`settings`). Blame finds the exemplar FIELD; only this finds
    the ACCESS expression, the dimension the get_settings()-hardcoded helper broke on
    (moo-arc is a singleton). Python only (ts/go keep their node accessor); best-effort
    — returns None on no repo_root / unreadable file / unknown idiom (caller defaults to
    the factory shape, the back-compatible behavior)."""
    if language != "python" or repo_root is None:
        return None
    rel = app_config.get("file")
    if not rel:
        return None
    try:
        src = (Path(repo_root) / rel).read_text(encoding="utf-8")
    except OSError:
        return None
    idiom = secret_exemplar.detect_access_idiom(src)
    return idiom if idiom.kind in ("singleton", "factory") else None


def plan_service_env_wire(service: dict, language: str, repo_root=None) -> dict:
    """Derive the per-service env-wiring plan from an inventory entry.

    Node-driven: the winning framework node (app_config.node_id) decides the
    wiring mode + accessor. Stub paths come from the inventory (emit_path/
    import_path, derived from the customer's real layout in Phase A); modify
    paths come from the customer's existing config module (file-derived). When
    `repo_root` is given, the modify accessor + imported symbol MIRROR the
    customer's access idiom (singleton vs factory) instead of assuming a factory."""
    app_config = service.get("app_config") or {}
    node_key = app_config.get("node_id") or app_config.get("pattern") or ""
    service_slug = service.get("service_slug", "")
    deployment_stubs = _plan_deployment_stubs(service.get("deployment_surfaces") or [])
    # PR #531 gap-detection: when Phase A's scanner found no terraform/k8s/
    # dockerfile at any scope, the inventory flags it. Carry it forward so
    # the instrument layer can surface a DEVELOPER ACTION REQUIRED checklist
    # in the PR body asking where the customer's IaC actually lives.
    infra_discovery_gap = bool(service.get("infra_discovery_gap", False))

    node = _resolve_node(node_key, language)
    # No node (unrecognized service or empty node_key) → stub mode.
    mode = node.wiring.get("mode", "stub") if node is not None else "stub"

    if mode == "stub":
        # Stub paths are inventory-derived (NOT a hardcode) — Phase A derived
        # emit_path/import_path from the customer's real config layout. The stub
        # artifact always exposes get_settings(), so the accessor is constant.
        return {
            "service_slug": service_slug,
            "mode": "stub",
            "settings_import_path": app_config.get("import_path"),
            # the stub artifact always exposes a get_settings() factory.
            "settings_import_name": "get_settings",
            "api_key_accessor": _STUB_ACCESSORS.get(language, ""),
            "stub_emit_path": app_config.get("emit_path"),
            "deployment_stubs": deployment_stubs,
            "infra_discovery_gap": infra_discovery_gap,
        }

    # modify mode: wire into the customer's EXISTING config module. The import
    # path is file-derived (their real module); the accessor comes from the
    # node's declared wiring, not a per-pattern hardcode.
    import_path = {
        "python": _python_settings_import_path,
        "typescript": _ts_settings_import_path,
        "go": _go_settings_import_path,
    }[language](app_config.get("file", ""), service_slug)
    # Mirror the access idiom: singleton -> import `settings` + `settings.X`; factory
    # (or undetected) -> import `get_settings` + `get_settings().X` (the node default,
    # back-compatible). The accessor reads OUR moolabs_api_key (a SecretStr).
    idiom = _detect_access_idiom(app_config, repo_root, language)
    if idiom is not None:
        import_name = idiom.import_name
        accessor = idiom.read("moolabs_api_key") + ".get_secret_value()"
    else:
        import_name = "get_settings"
        accessor = node.wiring.get("accessor")
    return {
        "service_slug": service_slug,
        "mode": "modify",
        "settings_import_path": import_path,
        "settings_import_name": import_name,
        "api_key_accessor": accessor,
        "stub_emit_path": None,
        "deployment_stubs": deployment_stubs,
        "infra_discovery_gap": infra_discovery_gap,
    }


# ──────────────────────────────────────────────────────────────────────
# Plan build (orchestrator)
# ──────────────────────────────────────────────────────────────────────

def build_plan(inventory: dict, services_languages: dict[str, str], repo_root=None) -> dict:
    """Orchestrate per-service plan derivation across the whole inventory.

    services_languages: {service_slug: "python" | "typescript" | "go"}.
    When a service slug is absent, defaults to python (matches Phase A's
    parse_services_and_granularity fallback). `repo_root` (the customer repo) lets
    modify-mode mirror each service's config access idiom; omitted -> factory default.
    """
    service_plans: list[dict] = []
    for svc in inventory.get("services") or []:
        slug = svc.get("service_slug", "")
        language = services_languages.get(slug, "python")
        service_plans.append(plan_service_env_wire(svc, language, repo_root))

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
    # Quote generated_at so PyYAML safe_load keeps it as a string (unquoted
    # ISO-8601 is auto-coerced to a datetime object — latent for now since no
    # consumer reads this field, but quoting future-proofs round-trip equality).
    lines.append(f"generated_at: {_quote(plan['generated_at'])}")
    if not plan.get("services"):
        lines.append("services: []")
    else:
        lines.append("services:")
        for svc in plan["services"]:
            # service_slug is customer-authored input (from Phase A inventory);
            # quote it to defend against slugs containing YAML metacharacters
            # like `:` or `#`. mode is a hardcoded enum, safe unquoted.
            lines.append(f"  - service_slug: {_quote(svc['service_slug'])}")
            lines.append(f"    mode: {svc['mode']}")
            # settings_import_path is inventory-derived in stub mode and may be
            # None for a degenerate stub with no inventory path — emit null
            # rather than the literal string "None".
            if svc.get("settings_import_path"):
                lines.append(
                    f"    settings_import_path: {_quote(svc['settings_import_path'])}"
                )
            else:
                lines.append("    settings_import_path: null")
            # the symbol the helper imports — `settings` (singleton) | `get_settings`
            # (factory). Default factory so older plans without the field still render.
            lines.append(
                f"    settings_import_name: {_quote(svc.get('settings_import_name') or 'get_settings')}"
            )
            lines.append(f"    api_key_accessor: {_quote(svc['api_key_accessor'])}")
            if svc.get("stub_emit_path"):
                lines.append(f"    stub_emit_path: {_quote(svc['stub_emit_path'])}")
            else:
                lines.append(f"    stub_emit_path: null")
            # PR #531 gap-detection passthrough.
            lines.append(
                f"    infra_discovery_gap: "
                f"{str(svc.get('infra_discovery_gap', False)).lower()}"
            )
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
                    # scope: service (auto-modifiable) | repo (checklist only;
                    # centralized infra has cross-service blast radius)
                    lines.append(f"        scope: {s.get('scope', 'service')}")
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


if __name__ == "__main__":
    sys.exit(main())
