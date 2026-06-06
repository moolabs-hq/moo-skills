#!/usr/bin/env python3
"""Phase 1.7-scan — env-loader pattern detection for /cost-billing-discovery.

Scans each service's source tree for recognized env-loading patterns
(pydantic-settings, dotenv, viper, etc.). Produces
.moolabs/customer-context/env-routing-inventory.yaml describing the
recognized pattern (or stub_required=true when none found) and the
deployment-surface insertion points (Terraform, k8s, docker-compose,
.env.example).

Pattern catalog lives at shared/assets/env-loader-patterns.yaml.

Usage:
    python env_loader_scan.py \\
        --signed-yaml .moolabs/chain/04-final.signed.yaml \\
        --customer-context-dir .moolabs/customer-context \\
        --catalog skills/cost-billing/shared/assets/env-loader-patterns.yaml \\
        [--repo-root .]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Catalog loading
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    id: str
    language: str
    detection_signal: str
    import_signals: list[str]
    structural_signals: list[str]
    wire_target: dict[str, str]
    priority: int


def load_pattern_catalog(path: Path) -> list[Pattern]:
    """Load env-loader-patterns.yaml. Uses PyYAML when available, falls back
    to a minimal hand-rolled parser otherwise (matches sdk_snapshot.py's
    no-runtime-dep approach for the codemod environment).
    """
    try:
        import yaml
        data = yaml.safe_load(path.read_text())
    except ImportError:
        data = _hand_rolled_yaml_load(path)

    out: list[Pattern] = []
    for entry in data.get("patterns", []):
        out.append(Pattern(
            id=entry["id"],
            language=entry["language"],
            detection_signal=entry["detection_signal"],
            import_signals=list(entry.get("import_signals", []) or []),
            structural_signals=list(entry.get("structural_signals", []) or []),
            wire_target=entry["wire_target"],
            priority=int(entry.get("priority", 50)),
        ))
    return out


def group_patterns_by_language(patterns: list[Pattern]) -> dict[str, list[Pattern]]:
    """Group patterns by language, sorted by priority descending within each."""
    by_lang: dict[str, list[Pattern]] = {"python": [], "typescript": [], "go": []}
    for p in patterns:
        by_lang.setdefault(p.language, []).append(p)
    for lang in by_lang:
        by_lang[lang].sort(key=lambda p: -p.priority)
    return by_lang


def _hand_rolled_yaml_load(path: Path) -> dict:
    """Minimal YAML reader for the pattern catalog when PyYAML is absent.
    Only supports the catalog's known shape; not a general YAML parser.
    """
    # Phase A leans on PyYAML being available in the test environment (smoke
    # already imports yaml). This fallback is here so the script can run in
    # a customer's codemod environment that lacks PyYAML — Phase B's instrument
    # side will revisit this.
    raise NotImplementedError(
        "PyYAML required for env_loader_scan.py in Phase A. "
        "Install pyyaml or run from the smoke environment."
    )


# ──────────────────────────────────────────────────────────────────────
# Per-file scan
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    """Result of scanning one file against the relevant patterns."""
    pattern_id: str
    file: str
    line_to_insert: int
    confidence: str  # "high" | "medium" | "low"
    confidence_score: float  # 0.0 to 1.0
    evidence: list[str] = field(default_factory=list)
    wire_target: dict[str, str] = field(default_factory=dict)


# Confidence-band thresholds. Matches catalog comment.
_HIGH_THRESHOLD = 0.85
_MEDIUM_THRESHOLD = 0.50
_LOW_THRESHOLD = 0.30


def _band(score: float) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _signal_score(text: str, regexes: list[str]) -> tuple[float, list[str]]:
    """Return (signal_strength, matched_evidence_lines). 1.0 if any regex
    matches, 0.0 if none. Evidence is the first matching line for each regex.
    """
    if not regexes:
        return 0.0, []
    evidence: list[str] = []
    matched = False
    for rx in regexes:
        for i, line in enumerate(text.splitlines(), start=1):
            if re.search(rx, line):
                evidence.append(f"line {i}: {line.strip()[:120]}")
                matched = True
                break
    return (1.0 if matched else 0.0), evidence


def _python_insert_line(text: str, class_pattern: str) -> int:
    """For Python class-based patterns: return the last field line of the
    first matching class (1-indexed). For non-class patterns: return the
    last non-blank, non-import line + 1.
    """
    lines = text.splitlines()

    # Try to find the class block
    in_class = False
    class_re = re.compile(class_pattern)
    last_field_line = 0
    class_start = 0
    for i, line in enumerate(lines, start=1):
        if not in_class and class_re.search(line):
            in_class = True
            class_start = i
            last_field_line = i
            continue
        if in_class:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped == "":
                continue
            # Dedented back to module level → end of class
            if indent == 0 and stripped:
                break
            # A class body line — track as last field if it looks like a field
            if ":" in stripped or "=" in stripped:
                last_field_line = i

    if last_field_line > class_start:
        return last_field_line

    # Fallback for non-class patterns: insert after the last non-blank line
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 1


def _ts_insert_line(text: str, opening_pattern: str) -> int:
    """For TS object/schema patterns: return the last entry line of the
    first matched balanced-brace block (1-indexed). Falls back to last
    non-blank line + 1 when no balanced match.
    """
    lines = text.splitlines()
    opening_re = re.compile(opening_pattern)
    for i, line in enumerate(lines, start=1):
        if opening_re.search(line):
            # Found the opener; now find the closing brace and the last
            # content line inside.
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                continue
            last_content_line = i
            for j in range(i + 1, len(lines) + 1):
                inner = lines[j - 1]
                inner_stripped = inner.strip()
                depth += inner.count("{") - inner.count("}")
                if depth <= 0:
                    # We've closed the block — return the line BEFORE the closer.
                    # If the closer is on a line by itself, last_content_line
                    # already points at the last content line.
                    if inner_stripped in {"}", "};", "})", "});", "})", "}));"}:
                        return last_content_line
                    return j
                # Only track content lines while still inside the block
                if inner_stripped and not inner_stripped.startswith("//"):
                    last_content_line = j
            return last_content_line
    # Fallback: last non-blank line
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 1


def _go_insert_line(text: str, opening_pattern: str) -> int:
    """For Go struct/func patterns: find the last content line inside the
    first matched balanced-brace block (1-indexed). Same shape as
    _ts_insert_line but tuned for Go's `type X struct { ... }` and
    `func X() { ... }` layouts.

    For Go patterns the structural signal often matches individual field/
    statement lines (e.g. envconfig struct tags, viper.BindEnv calls) rather
    than the block opener itself.  In that case we return the last matching
    line — the natural place to append the next field or call.  If the first
    match opens a brace block we delegate to _ts_insert_line's depth-tracking
    logic.
    """
    lines = text.splitlines()
    opening_re = re.compile(opening_pattern)

    # Collect all matching line numbers.
    matching: list[int] = []
    for i, line in enumerate(lines, start=1):
        if opening_re.search(line):
            matching.append(i)

    if not matching:
        # Fallback: last non-blank line.
        for i in range(len(lines), 0, -1):
            if lines[i - 1].strip():
                return i
        return 1

    first_match_line = matching[0]
    first_match_text = lines[first_match_line - 1]

    # If the first match opens a brace block, use TS depth-tracking.
    if first_match_text.count("{") > first_match_text.count("}"):
        return _ts_insert_line(text, opening_pattern)

    # Otherwise (field-level or statement-level signal): return the last
    # matching line — the natural append point for the next field/call.
    return matching[-1]


def scan_file(path: Path, patterns: list[Pattern]) -> ScanResult | None:
    """Scan a single file against the patterns. Return the highest-confidence
    match, or None if no pattern reaches the LOW threshold."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None

    best: ScanResult | None = None
    best_score: float = 0.0

    for p in patterns:
        import_score, import_evidence = _signal_score(text, p.import_signals)
        struct_score, struct_evidence = _signal_score(text, p.structural_signals)

        # Combine: structural weighted higher than imports.
        # Both → 0.95 (high); structural only → 0.65 (medium);
        # import only → 0.35 (low); neither → 0.0
        if import_score > 0 and struct_score > 0:
            score = 0.95
        elif struct_score > 0:
            score = 0.65
        elif import_score > 0:
            score = 0.35
        else:
            score = 0.0

        if score < _LOW_THRESHOLD:
            continue

        if score > best_score:
            best_score = score
            # For class-based Python patterns, derive insertion line from the class
            line_to_insert = 1
            if p.structural_signals:
                if p.language == "python":
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
                elif p.language == "typescript":
                    line_to_insert = _ts_insert_line(text, p.structural_signals[0])
                elif p.language == "go":
                    line_to_insert = _go_insert_line(text, p.structural_signals[0])
                else:
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
            best = ScanResult(
                pattern_id=p.id,
                file=str(path),
                line_to_insert=line_to_insert,
                confidence=_band(score),
                confidence_score=score,
                evidence=import_evidence + struct_evidence,
                wire_target=p.wire_target,
            )

    return best


# ──────────────────────────────────────────────────────────────────────
# CLI (skeleton — fleshed out in later tasks)
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--catalog", required=True, help="path to env-loader-patterns.yaml")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    # Phase A Task 3 ships catalog load + per-file scan only. Granularity,
    # deployment surfaces, and YAML emit land in later tasks.
    catalog = load_pattern_catalog(Path(args.catalog))
    print(f"loaded {len(catalog)} patterns", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
