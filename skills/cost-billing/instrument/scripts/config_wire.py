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


# ──────────────────────────────────────────────────────────────────────
# Per-service plan derivation
# ──────────────────────────────────────────────────────────────────────

# Maps each pattern_id → accessor expression. Only patterns whose accessor
# composes with the helper template's `from <import_path> import get_settings`
# contract are listed here. Patterns absent from this map route to stub mode
# via `accessor_map.get(pattern) is None` in plan_service_env_wire.
#
# python-decouple and python-dotenv-os-getenv are intentionally absent — they
# are flat module-level patterns with no get_settings() class. The helper
# template's import shape is incompatible; these route to stub mode (which
# emits a pydantic-settings BaseSettings stub the customer can adopt). Future
# enhancement: pattern-aware template variants for direct-export patterns.
_PYTHON_PATTERN_ACCESSORS = {
    "python-pydantic-settings-v2": "get_settings().moolabs_api_key.get_secret_value()",
    "python-pydantic-v1-settings": "get_settings().moolabs_api_key.get_secret_value()",
}


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


# All three TS patterns are intentionally absent — the helper template imports
# `getSettings` (typescript-moolabs-client.ts.j2:44) but none of the recognized
# patterns export a getSettings function:
#   - ts-zod-env-schema: exports `env` (a zod-parsed object)
#   - ts-process-env-direct: exports a const `MOOLABS_API_KEY`
#   - ts-env-var-library: exports a const `MOOLABS_API_KEY`
# All three route to stub mode (which emits a getSettings() wrapper template
# the customer can adopt). Future enhancement: pattern-aware template variants
# for direct-export patterns.
_TS_PATTERN_ACCESSORS: dict[str, str] = {}

# go-viper requires importing viper as a separate dependency (not the
# customer's `config` package the helper template imports), so it doesn't
# compose with the template. go-os-getenv uses stdlib `os` directly but
# leaves the `config` import unused — Go's `imported and not used` is a
# compile error. Both route to stub mode. go-envconfig works because it
# uses the customer's config package via the imported `config` alias.
_GO_PATTERN_ACCESSORS = {
    "go-envconfig":  "config.Get().MoolabsAPIKey",
}


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


def plan_service_env_wire(service: dict, language: str) -> dict:
    """Derive the per-service env-wiring plan from an inventory entry."""
    app_config = service.get("app_config") or {}
    pattern = app_config.get("pattern", "unrecognized")
    stub_required = bool(app_config.get("stub_required", True))
    service_slug = service.get("service_slug", "")
    deployment_stubs = _plan_deployment_stubs(service.get("deployment_surfaces") or [])
    # PR #531 gap-detection: when Phase A's scanner found no terraform/k8s/
    # dockerfile at any scope, the inventory flags it. Carry it forward so
    # the instrument layer can surface a DEVELOPER ACTION REQUIRED checklist
    # in the PR body asking where the customer's IaC actually lives.
    infra_discovery_gap = bool(service.get("infra_discovery_gap", False))

    if stub_required or pattern == "unrecognized":
        return {
            "service_slug": service_slug,
            "mode": "stub",
            "settings_import_path": _STUB_IMPORT_PATHS.get(language, ""),
            "api_key_accessor": _STUB_ACCESSORS.get(language, ""),
            "stub_emit_path": _STUB_EMIT_PATHS.get(language),
            "deployment_stubs": deployment_stubs,
            "infra_discovery_gap": infra_discovery_gap,
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
            "infra_discovery_gap": infra_discovery_gap,
        }
    import_path = {
        "python": _python_settings_import_path,
        "typescript": _ts_settings_import_path,
        "go": _go_settings_import_path,
    }[language](app_config.get("file", ""), service_slug)
    return {
        "service_slug": service_slug,
        "mode": "modify",
        "settings_import_path": import_path,
        "api_key_accessor": accessor,
        "stub_emit_path": None,
        "deployment_stubs": deployment_stubs,
        "infra_discovery_gap": infra_discovery_gap,
    }


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
            lines.append(f"    settings_import_path: {_quote(svc['settings_import_path'])}")
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
