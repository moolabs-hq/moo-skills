"""`review` — interactive front-end over the findings YAML. Walks each finding,
records the decision (map/absorb/ignore), and writes the SAME file so the mapping
persists, is reviewable, and is reused next period.
"""
from __future__ import annotations

from pathlib import Path

from ..findings import load_findings, save_findings

_DECISIONS = ["map", "absorb", "ignore", "skip"]


def review_findings(findings, ui, *, seed_client=None, out=print):
    total = len(findings)
    for idx, f in enumerate(findings, 1):
        ui.say(f"[{idx}/{total}] {f.service}  {f.resource_id}")
        ui.say(f"        ${f.monthly_cost_estimate_usd}/mo · {f.untagged_share_pct}% untagged · {f.severity}")
        ui.say(f"        suggested: {f.suggested_service_mapping}")
        choice = ui.choose("Decision?", ["map", "absorb", "ignore", "skip"])
        decision = _DECISIONS[choice]
        if decision == "skip":
            continue
        f.decision = decision
        f.approved = True
        if decision == "map":
            f.service_name = ui.ask("service_name", default=f.suggested_service_mapping)
            team = ui.ask("team (optional)", default="")
            f.team_name = team or None

    if seed_client is not None:
        from .seed import seed_findings

        seed_findings(findings, seed_client, out=out)
    return findings


def run_review(path: Path, ui, *, seed_client=None, out=print) -> int:
    path = Path(path)
    findings = load_findings(path)
    review_findings(findings, ui, seed_client=seed_client, out=out)
    save_findings(findings, path)
    out(f"Updated {path}.")
    return 0
