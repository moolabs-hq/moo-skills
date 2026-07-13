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
SIGNED_METHODS = {"interactive-cli", "agent-mediated", "external-form", "manual-yaml-edit"}
FULL_OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
TOP_LEVEL_FIELDS = {
    "$schema", "stage", "status", "generated_at", "signed_by",
    "adversarial_review", "artifact",
}
SIGNED_BY_REQUIRED = {"role", "name", "signed_at"}
SIGNED_BY_FIELDS = SIGNED_BY_REQUIRED | {"signed_method"}
REVIEW_FIELDS = {
    "phase", "verdict", "codegen_model", "reviewer_model", "review_evidence",
    "ran_at", "findings_total", "findings_human_accepted", "findings_resolved",
    "findings_rejected_as_false_positive", "cross_model_violated",
}
ARTIFACT_FIELDS = {"kind", "path", "sha256", "source_commit", "accepted_risks"}


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
    if not isinstance(value, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _load_map(map_path: Path) -> dict[str, Any]:
    try:
        document = json.loads(map_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("instrumentation map must be scanner-produced JSON-form YAML") from exc
    if not isinstance(document, dict):
        raise ValueError("instrumentation map must be an object")
    return document


def _commit_exists(repo: Path, commit: str) -> bool:
    run = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return run.returncode == 0


def _map_binding(repo: Path, map_path: Path) -> tuple[str, str]:
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

    revision = _load_map(map_path).get("source_revision")
    if not isinstance(revision, dict) or set(revision) != {"git_commit", "state"}:
        raise ValueError("instrumentation map must contain the scanner source_revision contract")
    source_commit = revision.get("git_commit")
    if revision.get("state") != "clean" or not isinstance(source_commit, str):
        raise ValueError("block signoff requires a clean map source_revision")
    if FULL_OID.fullmatch(source_commit) is None:
        raise ValueError("map source_revision git_commit must be a full lowercase Git object ID")
    if not _commit_exists(repo, source_commit):
        raise ValueError("map source_revision git_commit does not exist in the repository")
    return artifact_path, source_commit


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
    """Build a signoff bound to the map bytes, path, and clean source revision."""
    artifact_path, source_commit = _map_binding(repo, map_path)
    if not _nonempty_string(operator):
        raise ValueError("operator must be non-empty")
    if not _nonempty_string(codegen_model):
        raise ValueError("codegen model must be non-empty")
    if not _nonempty_string(reviewer_model):
        raise ValueError("reviewer model must be non-empty")
    if not _review_evidence(review_evidence):
        raise ValueError("review evidence must be a review:// or HTTP(S) URL, or a structured evidence ID")
    cross_model_violated = codegen_model.strip().casefold() == reviewer_model.strip().casefold()
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
    findings_total = (
        findings_accepted
        + findings_resolved
        + findings_rejected_as_false_positive
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
    """Return whether ``signoff`` approves the exact current map and source revision."""
    try:
        artifact_path, source_commit = _map_binding(repo, map_path)
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
        if total != accepted + resolved + rejected:
            return False

        cross_model_violated = (
            review["codegen_model"].strip().casefold()
            == review["reviewer_model"].strip().casefold()
        )
        if review["cross_model_violated"] is not cross_model_violated or cross_model_violated:
            return False

        risks = artifact["accepted_risks"]
        if (
            not isinstance(risks, list)
            or any(not isinstance(risk, str) or not risk.strip() for risk in risks)
            or len({risk.strip() for risk in risks}) != len(risks)
            or len(risks) != accepted
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
    except (AttributeError, FileNotFoundError, KeyError, OSError, TypeError, ValueError):
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
    create.add_argument("--review-verdict", choices=sorted(ALLOWED_VERDICTS), required=True)
    create.add_argument("--findings-resolved", type=int, required=True)
    create.add_argument("--findings-rejected-as-false-positive", type=int, required=True)
    create.add_argument("--accepted-risk", action="append", default=[])

    verify = subparsers.add_parser("verify", help="verify a signoff against current map bytes")
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
    if not isinstance(payload, dict) or not verify_signoff(args.repo, args.map_path, payload):
        print("instrumentation-map signoff does not match current artifact", file=sys.stderr)
        return 1
    print("instrumentation-map signoff verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
