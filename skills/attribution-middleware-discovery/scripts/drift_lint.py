#!/usr/bin/env python3
"""Compare static attribution discovery to a baseline without mutating source."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from attribution_scan import DiscoveryError, discover, load_document, validate_map


def _policy(repo: Path) -> str:
    policy = repo / ".moolabs" / "attribution-policy.yaml"
    if not policy.exists():
        return "warn"
    active_lines = [
        line
        for line in policy.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(active_lines) != 1:
        raise DiscoveryError("policy must contain exactly one top-level enforcement key")
    match = re.fullmatch(r"enforcement:[ \t]+(warn|block)[ \t]*(?:#.*)?", active_lines[0])
    if match is None:
        raise DiscoveryError("policy must be exactly: enforcement: warn|block")
    return match.group(1)


def _route_index(document: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for service in document.get("services", []):
        for route in service.get("routes", []):
            result.setdefault(route["route_id"], {"service_path": service["service_path"], **route})
    return result


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _load_signoff_verifier():
    skills_root = Path(__file__).resolve().parents[2]
    candidates = (
        skills_root / "cost-billing" / "signoff" / "scripts" / "attribution_map_signoff.py",
        skills_root / "cost-billing-signoff" / "scripts" / "attribution_map_signoff.py",
        skills_root / "signoff" / "scripts" / "attribution_map_signoff.py",
    )
    script = next((candidate for candidate in candidates if candidate.is_file()), None)
    if script is None:
        raise DiscoveryError("block enforcement requires the cost-billing signoff verifier")
    spec = importlib.util.spec_from_file_location("cost_billing_attribution_signoff", script)
    if spec is None or spec.loader is None:
        raise DiscoveryError(f"unable to load attribution signoff verifier: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require_block_signoff(repo: Path, baseline_path: Path, baseline: dict) -> None:
    signoff_path = repo / ".moolabs" / "attribution" / "instrumentation-map-signoff.yaml"
    if not signoff_path.is_file():
        raise DiscoveryError(f"block enforcement requires instrumentation-map signoff: {signoff_path}")
    signoff = load_document(signoff_path)
    if not _load_signoff_verifier().verify_signoff(
        repo,
        baseline_path,
        signoff,
        require_current_source=False,
    ):
        raise DiscoveryError("block enforcement signoff does not approve the exact baseline map")


def compare(baseline: dict, current: dict) -> list[dict]:
    old_routes, new_routes = _route_index(baseline), _route_index(current)
    findings = []
    if baseline.get("source_fingerprint") != current.get("source_fingerprint"):
        findings.append({"code": "source_fingerprint_changed", "severity": "warning", "evidence": None})
    if baseline.get("discovery_projection") != current.get("discovery_projection"):
        findings.append({"code": "projected_coverage_changed", "severity": "warning", "evidence": None})
    for label, document in (("baseline", baseline), ("current", current)):
        service_paths = [service["service_path"] for service in document.get("services", [])]
        for service_path in _duplicates(service_paths):
            findings.append({"code": "duplicate_service_path", "severity": "warning", "service_path": service_path, "document": label, "evidence": None})
        route_ids = [route["route_id"] for service in document.get("services", []) for route in service.get("routes", [])]
        for route_id in _duplicates(route_ids):
            findings.append({"code": "duplicate_route_id", "severity": "warning", "route_id": route_id, "document": label, "evidence": None})
    for route_id in sorted(new_routes.keys() - old_routes.keys()):
        findings.append({"code": "route_added", "severity": "warning", "route_id": route_id, "evidence": new_routes[route_id]["evidence"]})
    for route_id in sorted(old_routes.keys() - new_routes.keys()):
        findings.append({"code": "route_removed", "severity": "warning", "route_id": route_id, "evidence": old_routes[route_id]["evidence"]})
    old_middleware = {service["service_path"]: service["middleware_detected"] for service in baseline.get("services", [])}
    old_services = {service["service_path"]: service for service in baseline.get("services", [])}
    for service in current.get("services", []):
        service_path = service["service_path"]
        old_service = old_services.get(service_path)
        if old_middleware.get(service_path) and not service["middleware_detected"]:
            findings.append({"code": "middleware_removed", "severity": "warning", "service_path": service_path, "evidence": None})
        if old_service is None:
            continue
        if old_service.get("resolver") != service.get("resolver"):
            findings.append({"code": "resolver_changed", "severity": "warning", "service_path": service_path, "evidence": None})
        if old_service.get("async_hops") != service.get("async_hops"):
            findings.append({"code": "async_propagation_changed", "severity": "warning", "service_path": service_path, "evidence": None})
        if old_service.get("findings") != service.get("findings"):
            findings.append({"code": "findings_changed", "severity": "warning", "service_path": service_path, "evidence": None})
    for route_id in sorted(old_routes.keys() & new_routes.keys()):
        if old_routes[route_id].get("auth_scope") != new_routes[route_id].get("auth_scope"):
            findings.append({"code": "auth_changed", "severity": "warning", "route_id": route_id, "evidence": new_routes[route_id]["evidence"]})
        if old_routes[route_id].get("feature_proposal") != new_routes[route_id].get("feature_proposal"):
            findings.append({"code": "feature_proposal_changed", "severity": "warning", "route_id": route_id, "evidence": new_routes[route_id]["evidence"]})
    if baseline.get("findings") != current.get("findings") and not any(item["code"] == "findings_changed" for item in findings):
        findings.append({"code": "findings_changed", "severity": "warning", "evidence": None})
    return sorted(findings, key=lambda item: (item["code"], item.get("service_path", ""), item.get("route_id", ""), item.get("document", "")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--service")
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    baseline_path = Path(args.baseline).resolve()
    generated_at = args.generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        baseline = load_document(baseline_path)
        validate_map(baseline)
        current = discover(repo, generated_at, args.service)
        findings = compare(baseline, current)
        enforcement = _policy(repo)
        if enforcement == "block":
            _require_block_signoff(repo, baseline_path, baseline)
    except (DiscoveryError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"findings": findings}, indent=2, sort_keys=True))
    return 1 if findings and enforcement == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
