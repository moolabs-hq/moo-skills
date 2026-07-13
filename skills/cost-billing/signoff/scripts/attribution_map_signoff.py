#!/usr/bin/env python3
"""Create or verify an engineer-owned instrumentation-map signoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit


SCHEMA_ID = "https://moolabs.com/schemas/cost-billing-signoff/0.1.0"
STAGE = "engineer-attribution-map"
PHASE = "post-signoff-engineer-attribution-map"
ALLOWED_VERDICTS = {"clean", "clean-with-accepted-risks"}
SIGNED_METHODS = {
    "interactive-cli",
    "agent-mediated",
    "external-form",
    "manual-yaml-edit",
}
FULL_OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
TOP_LEVEL_FIELDS = {
    "$schema",
    "stage",
    "status",
    "generated_at",
    "signed_by",
    "adversarial_review",
    "artifact",
}
SIGNED_BY_REQUIRED = {"role", "name", "signed_at"}
SIGNED_BY_FIELDS = SIGNED_BY_REQUIRED | {"signed_method"}
REVIEW_FIELDS = {
    "phase",
    "verdict",
    "codegen_model",
    "reviewer_model",
    "review_evidence",
    "ran_at",
    "findings_total",
    "findings_human_accepted",
    "findings_resolved",
    "findings_rejected_as_false_positive",
    "cross_model_violated",
}
ARTIFACT_FIELDS = {"kind", "path", "sha256", "source_commit", "accepted_risks"}
MAP_FIELDS = {
    "schema_version",
    "scanner_version",
    "generated_at",
    "source_revision",
    "source_fingerprint",
    "discovery_projection",
    "services",
    "findings",
}
SERVICE_FIELDS = {
    "service_path",
    "frameworks",
    "ingress_state",
    "middleware_detected",
    "routes",
    "mounts",
    "resolver",
    "async_hops",
    "findings",
}
RESOLVER_FIELDS = {"state", "identity_kind", "expression", "template", "evidence"}
FINDING_FIELDS = {"code", "severity", "message", "evidence"}
LOCATION_FIELDS = {"file", "line"}
ROUTE_FIELDS = {
    "route_id",
    "framework",
    "method",
    "path_template",
    "confidence",
    "auth_scope",
    "evidence",
    "feature_proposal",
}
FEATURE_PROPOSAL_FIELDS = {"slug", "confidence", "requires_engineer_signoff"}
MOUNT_FIELDS = {"framework", "target", "prefix", "confidence", "evidence"}
ASYNC_HOP_FIELDS = {"kind", "propagation", "evidence"}
UNSAFE_FINDING_CODES = {"raw_identity_header"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _review_evidence(value: Any) -> bool:
    if not _nonempty_string(value) or any(character.isspace() for character in value):
        return False
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme in {"http", "https", "review"}:
        return bool(parsed.netloc)
    return re.fullmatch(r"(?:[A-Z][A-Z0-9]*-)+[0-9]+", normalized) is not None


def _complete_attribution_shape(signoff: dict[str, Any]) -> bool:
    if set(signoff) != TOP_LEVEL_FIELDS:
        return False
    signed_by = signoff.get("signed_by")
    review = signoff.get("adversarial_review")
    artifact = signoff.get("artifact")
    if not all(isinstance(value, dict) for value in (signed_by, review, artifact)):
        return False
    return (
        SIGNED_BY_REQUIRED.issubset(signed_by)
        and set(signed_by).issubset(SIGNED_BY_FIELDS)
        and set(review) == REVIEW_FIELDS
        and set(artifact) == ARTIFACT_FIELDS
    )


def _timestamp(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            value,
        )
        is None
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_map(map_path: Path) -> dict[str, Any]:
    try:
        document = json.loads(
            map_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "instrumentation map must be scanner-produced JSON-form YAML"
        ) from exc
    if not isinstance(document, dict):
        raise ValueError("instrumentation map must be an object")
    return document


def _location(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == LOCATION_FIELDS
        and _nonempty_string(value.get("file"))
        and isinstance(value.get("line"), int)
        and not isinstance(value.get("line"), bool)
        and value["line"] >= 1
    )


def _finding(value: Any, *, top_level: bool) -> bool:
    if not isinstance(value, dict):
        return False
    expected_fields = FINDING_FIELDS | ({"service_path"} if top_level else set())
    if set(value) != expected_fields:
        return False
    return (
        _nonempty_string(value.get("code"))
        and value.get("severity") in {"info", "warning", "high"}
        and _nonempty_string(value.get("message"))
        and (value.get("evidence") is None or _location(value.get("evidence")))
        and (not top_level or isinstance(value.get("service_path"), str))
    )


def _canonical_value(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _route(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != ROUTE_FIELDS:
        return False
    proposal = value.get("feature_proposal")
    return (
        _nonempty_string(value.get("route_id"))
        and _nonempty_string(value.get("framework"))
        and value.get("method")
        in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", None}
        and (
            value.get("path_template") is None
            or isinstance(value.get("path_template"), str)
        )
        and value.get("confidence") in {"high", "medium", "low"}
        and value.get("auth_scope") in {"global", "router", "handler", "unknown"}
        and _location(value.get("evidence"))
        and isinstance(proposal, dict)
        and set(proposal) == FEATURE_PROPOSAL_FIELDS
        and _nonempty_string(proposal.get("slug"))
        and proposal.get("confidence") in {"high", "medium", "low"}
        and proposal.get("requires_engineer_signoff") is True
    )


def _mount(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == MOUNT_FIELDS
        and _nonempty_string(value.get("framework"))
        and _nonempty_string(value.get("target"))
        and (value.get("prefix") is None or isinstance(value.get("prefix"), str))
        and value.get("confidence") in {"high", "medium", "low"}
        and _location(value.get("evidence"))
    )


def _async_hop(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == ASYNC_HOP_FIELDS
        and _nonempty_string(value.get("kind"))
        and value.get("propagation") in {"verified", "missing", "unknown"}
        and _location(value.get("evidence"))
    )


def _map_review(document: dict[str, Any]) -> tuple[int, tuple[str, ...]]:
    if set(document) != MAP_FIELDS or document.get("schema_version") != "1.0":
        raise ValueError("instrumentation map does not match the scanner contract")
    if (
        not isinstance(document.get("scanner_version"), str)
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", document["scanner_version"])
        is None
    ):
        raise ValueError("instrumentation map scanner_version is invalid")
    if not _nonempty_string(document.get("generated_at")):
        raise ValueError("instrumentation map generated_at is invalid")

    fingerprint = document.get("source_fingerprint")
    if (
        not isinstance(fingerprint, dict)
        or set(fingerprint) != {"algorithm", "value"}
        or fingerprint.get("algorithm") != "sha256"
        or not isinstance(fingerprint.get("value"), str)
        or re.fullmatch(r"[a-f0-9]{64}", fingerprint["value"]) is None
    ):
        raise ValueError("instrumentation map source_fingerprint is invalid")

    projection = document.get("discovery_projection")
    projection_fields = {
        "routes_discovered",
        "routes_statically_covered",
        "routes_unknown",
    }
    if (
        not isinstance(projection, dict)
        or set(projection) != projection_fields
        or not all(_count(projection.get(field)) for field in projection_fields)
        or projection["routes_statically_covered"] + projection["routes_unknown"]
        != projection["routes_discovered"]
    ):
        raise ValueError("instrumentation map discovery_projection is inconsistent")

    services = document.get("services")
    findings = document.get("findings")
    if not isinstance(services, list) or not isinstance(findings, list):
        raise ValueError("instrumentation map services and findings must be arrays")
    if not all(_finding(finding, top_level=True) for finding in findings):
        raise ValueError("instrumentation map contains malformed top-level findings")

    derived_findings: list[dict[str, Any]] = []
    unsafe: list[str] = []
    routes_discovered = 0
    for service in services:
        if not isinstance(service, dict) or set(service) != SERVICE_FIELDS:
            raise ValueError("instrumentation map contains a malformed service")
        service_path = service.get("service_path")
        if not isinstance(service_path, str):
            raise ValueError("instrumentation map service_path must be a string")
        if not isinstance(service.get("frameworks"), list) or not all(
            _nonempty_string(item) for item in service["frameworks"]
        ):
            raise ValueError("instrumentation map service frameworks must be an array")
        if service.get("ingress_state") not in {
            "http-ingress",
            "no-middleware-inherits-thread-id",
            "unknown",
        }:
            raise ValueError("instrumentation map service ingress_state is invalid")
        if not isinstance(service.get("middleware_detected"), bool):
            raise ValueError("instrumentation map middleware_detected must be boolean")
        for field in ("routes", "mounts", "async_hops", "findings"):
            if not isinstance(service.get(field), list):
                raise ValueError(
                    f"instrumentation map service {field} must be an array"
                )

        resolver = service.get("resolver")
        if (
            not isinstance(resolver, dict)
            or set(resolver) != RESOLVER_FIELDS
            or resolver.get("state") not in {"proposed", "unresolved"}
        ):
            raise ValueError("instrumentation map service resolver is malformed")
        if resolver["state"] == "proposed" and not (
            resolver.get("identity_kind") in {"moolabs_uuid", "external_key_crosswalk"}
            and _nonempty_string(resolver.get("expression"))
            and _nonempty_string(resolver.get("template"))
            and _location(resolver.get("evidence"))
        ):
            raise ValueError("instrumentation map proposed resolver is incomplete")
        if resolver["state"] == "unresolved" and any(
            resolver.get(field) is not None
            for field in ("identity_kind", "expression", "template", "evidence")
        ):
            raise ValueError("instrumentation map unresolved resolver is malformed")
        if resolver["state"] == "unresolved":
            unsafe.append(f"{service_path}:unresolved-resolver")

        service_findings = service["findings"]
        if not all(_finding(finding, top_level=False) for finding in service_findings):
            raise ValueError("instrumentation map contains malformed service findings")
        for finding in service_findings:
            derived_findings.append({**finding, "service_path": service_path})
            if finding["severity"] == "high" or finding["code"] in UNSAFE_FINDING_CODES:
                unsafe.append(f"{service_path}:high:{finding['code']}")

        if service["ingress_state"] == "unknown":
            unsafe.append(f"{service_path}:unknown-ingress")
        routes_discovered += len(service["routes"])
        for route in service["routes"]:
            if not _route(route):
                raise ValueError("instrumentation map contains a malformed route")
            if (
                route.get("method") is None
                or route.get("path_template") is None
                or route.get("auth_scope") == "unknown"
            ):
                unsafe.append(f"{service_path}:unresolved-route")
        if not all(_mount(mount) for mount in service["mounts"]):
            raise ValueError("instrumentation map contains a malformed mount")
        for hop in service["async_hops"]:
            if not _async_hop(hop):
                raise ValueError("instrumentation map contains a malformed async hop")
            if hop["propagation"] != "verified":
                unsafe.append(f"{service_path}:unresolved-async-propagation")

    actual_findings = sorted(_canonical_value(finding) for finding in findings)
    expected_findings = sorted(
        _canonical_value(finding) for finding in derived_findings
    )
    if actual_findings != expected_findings:
        raise ValueError(
            "instrumentation map top-level findings do not match service findings"
        )
    if projection["routes_discovered"] != routes_discovered:
        raise ValueError(
            "instrumentation map discovery_projection route total is inconsistent"
        )
    if projection["routes_unknown"]:
        unsafe.append("projection:routes-unknown")
    return len(findings), tuple(sorted(set(unsafe)))


def _derived_verdict(unsafe: Sequence[str], accepted_count: int) -> str:
    if unsafe:
        return "blocked"
    if accepted_count:
        return "clean-with-accepted-risks"
    return "clean"


def _commit_exists(repo: Path, commit: str) -> bool:
    run = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return run.returncode == 0


def _map_binding(repo: Path, map_path: Path) -> tuple[str, str, dict[str, Any]]:
    repo = repo.resolve()
    map_path = map_path.resolve()
    if not repo.is_dir():
        raise ValueError(f"repository not found: {repo}")
    if not map_path.is_file():
        raise FileNotFoundError(map_path)
    try:
        artifact_path = map_path.relative_to(repo).as_posix()
    except ValueError as exc:
        raise ValueError("instrumentation map must be inside the repository") from exc

    document = _load_map(map_path)
    revision = document.get("source_revision")
    if not isinstance(revision, dict) or set(revision) != {"git_commit", "state"}:
        raise ValueError(
            "instrumentation map must contain the scanner source_revision contract"
        )
    source_commit = revision.get("git_commit")
    if revision.get("state") != "clean" or not isinstance(source_commit, str):
        raise ValueError("block signoff requires a clean map source_revision")
    if FULL_OID.fullmatch(source_commit) is None:
        raise ValueError(
            "map source_revision git_commit must be a full lowercase Git object ID"
        )
    if not _commit_exists(repo, source_commit):
        raise ValueError(
            "map source_revision git_commit does not exist in the repository"
        )
    return artifact_path, source_commit, document


def build_signoff(
    map_path: Path,
    *,
    repo: Path,
    operator: str,
    codegen_model: str,
    reviewer_model: str,
    review_evidence: str,
    review_verdict: str,
    findings_resolved: int,
    findings_rejected_as_false_positive: int,
    accepted_risks: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a signoff bound to the canonical map, path, and source revision."""
    artifact_path, source_commit, document = _map_binding(repo, map_path)
    if not _nonempty_string(operator):
        raise ValueError("operator must be non-empty")
    if not _nonempty_string(codegen_model):
        raise ValueError("codegen model must be non-empty")
    if not _nonempty_string(reviewer_model):
        raise ValueError("reviewer model must be non-empty")
    if not _review_evidence(review_evidence):
        raise ValueError(
            "review evidence must be a review:// or HTTP(S) URL, or a structured evidence ID"
        )
    cross_model_violated = (
        codegen_model.strip().casefold() == reviewer_model.strip().casefold()
    )
    if cross_model_violated:
        raise ValueError("codegen and reviewer models must be distinct")
    if review_verdict not in ALLOWED_VERDICTS:
        raise ValueError(f"unsupported review verdict: {review_verdict}")

    raw_risks = accepted_risks or []
    if any(not isinstance(risk, str) or not risk.strip() for risk in raw_risks):
        raise ValueError("accepted risks must be non-empty strings")
    risks = [risk.strip() for risk in raw_risks]
    if len(set(risks)) != len(risks):
        raise ValueError("accepted risks must be unique")
    if review_verdict == "clean-with-accepted-risks" and not risks:
        raise ValueError("clean-with-accepted-risks requires an explicit accepted risk")
    if review_verdict == "clean" and risks:
        raise ValueError("accepted risks require clean-with-accepted-risks")
    if not _count(findings_resolved) or not _count(findings_rejected_as_false_positive):
        raise ValueError("review finding counts must be non-negative integers")

    findings_accepted = len(risks)
    caller_total = (
        findings_accepted + findings_resolved + findings_rejected_as_false_positive
    )
    findings_total, unsafe = _map_review(document)
    derived_verdict = _derived_verdict(unsafe, findings_accepted)
    if derived_verdict == "blocked":
        raise ValueError(
            "instrumentation map contains unsafe or unresolved content: "
            + ", ".join(unsafe)
        )
    if review_verdict != derived_verdict:
        raise ValueError("review verdict does not match instrumentation map findings")
    if caller_total != findings_total:
        raise ValueError(
            "review finding outcome counts do not match instrumentation map findings"
        )

    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    if not _timestamp(timestamp):
        raise ValueError("generated timestamp must be an RFC 3339 date-time")
    return {
        "$schema": SCHEMA_ID,
        "stage": STAGE,
        "status": "approved",
        "generated_at": timestamp,
        "signed_by": {
            "role": "team-engineer",
            "name": operator.strip(),
            "signed_at": timestamp,
            "signed_method": "agent-mediated",
        },
        "adversarial_review": {
            "phase": PHASE,
            "verdict": review_verdict,
            "codegen_model": codegen_model.strip(),
            "reviewer_model": reviewer_model.strip(),
            "review_evidence": review_evidence.strip(),
            "ran_at": timestamp,
            "findings_total": findings_total,
            "findings_human_accepted": findings_accepted,
            "findings_resolved": findings_resolved,
            "findings_rejected_as_false_positive": findings_rejected_as_false_positive,
            "cross_model_violated": cross_model_violated,
        },
        "artifact": {
            "kind": "attribution-instrumentation-map",
            "path": artifact_path,
            "sha256": _sha256(map_path),
            "source_commit": source_commit,
            "accepted_risks": risks,
        },
    }


def verify_signoff(repo: Path, map_path: Path, signoff: dict[str, Any]) -> bool:
    """Return whether ``signoff`` approves the exact map bytes and source revision."""
    try:
        artifact_path, source_commit, document = _map_binding(repo, map_path)
        map_findings_total, unsafe = _map_review(document)
        if unsafe:
            return False
        if not isinstance(signoff, dict) or not _complete_attribution_shape(signoff):
            return False

        signed_by = signoff["signed_by"]
        review = signoff["adversarial_review"]
        artifact = signoff["artifact"]

        if (
            signoff["$schema"] != SCHEMA_ID
            or signoff["stage"] != STAGE
            or signoff["status"] != "approved"
            or not _timestamp(signoff["generated_at"])
            or signed_by["role"] != "team-engineer"
            or not _nonempty_string(signed_by["name"])
            or not _timestamp(signed_by["signed_at"])
            or (
                "signed_method" in signed_by
                and signed_by["signed_method"] not in SIGNED_METHODS
            )
            or review["phase"] != PHASE
            or review["verdict"] not in ALLOWED_VERDICTS
            or not _nonempty_string(review["codegen_model"])
            or not _nonempty_string(review["reviewer_model"])
            or not _review_evidence(review["review_evidence"])
            or not _timestamp(review["ran_at"])
        ):
            return False

        counts = (
            review["findings_total"],
            review["findings_human_accepted"],
            review["findings_resolved"],
            review["findings_rejected_as_false_positive"],
        )
        if not all(_count(value) for value in counts):
            return False
        total, accepted, resolved, rejected = counts
        if total != accepted + resolved + rejected or total != map_findings_total:
            return False

        cross_model_violated = (
            review["codegen_model"].strip().casefold()
            == review["reviewer_model"].strip().casefold()
        )
        if (
            review["cross_model_violated"] is not cross_model_violated
            or cross_model_violated
        ):
            return False

        risks = artifact["accepted_risks"]
        if (
            not isinstance(risks, list)
            or any(not isinstance(risk, str) or not risk.strip() for risk in risks)
            or len({risk.strip() for risk in risks}) != len(risks)
            or len(risks) != accepted
            or review["verdict"] != _derived_verdict(unsafe, accepted)
            or (review["verdict"] == "clean" and accepted != 0)
            or (review["verdict"] == "clean-with-accepted-risks" and accepted == 0)
        ):
            return False

        return (
            artifact["kind"] == "attribution-instrumentation-map"
            and artifact["path"] == artifact_path
            and isinstance(artifact["sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", artifact["sha256"]) is not None
            and artifact["sha256"] == _sha256(map_path)
            and artifact["source_commit"] == source_commit
        )
    except (
        AttributeError,
        FileNotFoundError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML and keeps this portable helper free of PyYAML.
    rendered = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    path.write_text(rendered, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create an artifact-bound signoff")
    create.add_argument("map_path", type=Path)
    create.add_argument("--repo", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--operator", required=True)
    create.add_argument("--codegen-model", required=True)
    create.add_argument("--reviewer-model", required=True)
    create.add_argument("--review-evidence", required=True)
    create.add_argument(
        "--review-verdict", choices=sorted(ALLOWED_VERDICTS), required=True
    )
    create.add_argument("--findings-resolved", type=int, required=True)
    create.add_argument(
        "--findings-rejected-as-false-positive", type=int, required=True
    )
    create.add_argument("--accepted-risk", action="append", default=[])

    verify = subparsers.add_parser(
        "verify", help="verify a signoff against current canonical map content"
    )
    verify.add_argument("map_path", type=Path)
    verify.add_argument("signoff_path", type=Path)
    verify.add_argument("--repo", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "create":
        try:
            signoff = build_signoff(
                args.map_path,
                repo=args.repo,
                operator=args.operator,
                codegen_model=args.codegen_model,
                reviewer_model=args.reviewer_model,
                review_evidence=args.review_evidence,
                review_verdict=args.review_verdict,
                findings_resolved=args.findings_resolved,
                findings_rejected_as_false_positive=(
                    args.findings_rejected_as_false_positive
                ),
                accepted_risks=args.accepted_risk,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        _write_yaml(args.output, signoff)
        print(f"Wrote {args.output}")
        return 0

    try:
        payload = json.loads(args.signoff_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict) or not verify_signoff(
        args.repo, args.map_path, payload
    ):
        print(
            "instrumentation-map signoff does not match current artifact",
            file=sys.stderr,
        )
        return 1
    print("instrumentation-map signoff verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
