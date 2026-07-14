#!/usr/bin/env python3
"""Generate a deterministic attribution instrumentation map without executing source."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from attribution_scan import DiscoveryError, atomic_write, discover, dump_document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--service")
    parser.add_argument("--output")
    parser.add_argument("--generated-at", help="Deterministic timestamp override for tests")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    default_output = args.output is None
    output = Path(args.output).resolve() if args.output else repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
    generated_at = args.generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        document = discover(repo, generated_at, args.service)
        atomic_write(
            output,
            dump_document(document),
            containment_root=repo if default_output else None,
        )
    except (DiscoveryError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
