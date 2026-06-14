"""`detect` — confirm AWS usage from code imports (read-only). Evidence-based;
never assumes AWS. Optional sanity-check before `configure`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs"}
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".tox",
}
_PATTERNS = [
    (re.compile(r"\bboto3\b"), "boto3"),
    (re.compile(r"\bbotocore\b"), "botocore"),
    (re.compile(r"@aws-sdk/"), "@aws-sdk"),
    (re.compile(r"\baws-sdk\b"), "aws-sdk"),
]
_MAX_EVIDENCE = 50


@dataclass
class DetectResult:
    detected: bool
    evidence: list[tuple[str, int, str]] = field(default_factory=list)


def detect_aws(repo: Path) -> DetectResult:
    repo = Path(repo)
    evidence: list[tuple[str, int, str]] = []
    for path in _walk(repo):
        if len(evidence) >= _MAX_EVIDENCE:
            break
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, token in _PATTERNS:
                if pattern.search(line):
                    evidence.append((str(path.relative_to(repo)), lineno, token))
                    break
            if len(evidence) >= _MAX_EVIDENCE:
                break
    return DetectResult(detected=bool(evidence), evidence=evidence)


def _walk(repo: Path):
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix not in _EXTS:
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def run_detect(repo, *, out=print) -> int:
    result = detect_aws(Path(repo))
    if result.detected:
        out(f"AWS detected ({len(result.evidence)} site(s)):")
        for rel, lineno, token in result.evidence[:10]:
            out(f"  {rel}:{lineno}  ({token})")
        out("confirmed_account: <pending — supply explicitly>")
        return 0
    out("AWS not detected from code imports. Confirm before configuring a CUR.")
    return 0
