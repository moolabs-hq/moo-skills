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
