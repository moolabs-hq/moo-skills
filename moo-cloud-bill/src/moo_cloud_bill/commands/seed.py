"""`seed` — POST human-approved (decision=map) findings to Acute's
resource_service_map so untagged spend becomes attributable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..findings import load_findings


@dataclass
class SeedSummary:
    ok: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


def seed_findings(findings, client, *, out=print) -> SeedSummary:
    summary = SeedSummary()
    for f in findings:
        if not f.is_seedable():
            summary.skipped += 1
            continue
        result = client.upsert_resource_map(f.to_resource_map_body())
        if result.ok:
            summary.ok += 1
            out(f"mapped {f.resource_id} → {f.service_name} ({result.status_code})")
        else:
            summary.failed += 1
            out(f"FAILED {f.resource_id}: {result.status_code} — {result.body}")
    out(f"Seeded {summary.ok}, skipped {summary.skipped}, failed {summary.failed}.")
    return summary


def run_seed(path: Path, client, *, out=print) -> int:
    findings = load_findings(Path(path))
    return seed_findings(findings, client, out=out).exit_code
