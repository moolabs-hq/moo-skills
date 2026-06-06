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

# Maps each pattern_id → (mode-when-recognized, accessor template).
# Accessor template uses {{settings_call}} as a placeholder for the language-
# specific get_settings() call site.
_PYTHON_PATTERN_ACCESSORS = {
    "python-pydantic-settings-v2": "get_settings().moolabs_api_key.get_secret_value()",
    "python-pydantic-v1-settings": "get_settings().moolabs_api_key.get_secret_value()",
    "python-decouple":            "MOOLABS_API_KEY",
    "python-dotenv-os-getenv":    "MOOLABS_API_KEY",
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
    service_slug = service.get("service_slug", "")
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
    }


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
