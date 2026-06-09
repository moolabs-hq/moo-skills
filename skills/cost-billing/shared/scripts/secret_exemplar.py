#!/usr/bin/env python3
"""Auto-propose the customer's MOST-RECENTLY-ADDED secret as the exemplar to mirror
when wiring MOOLABS_API_KEY (Phase 1.7 "mirror your last-added secret").

Rather than make the engineer hunt for "how do we wire a secret", the skill blames the
config layer (the Settings class) for the newest secret-typed field and proposes it —
the engineer confirms or @-links a different one. Same propose-confirm-mirror
ergonomics as the entity_id capture: point at a real, proven prod path; never guess.

DETERMINISTIC + TESTABLE: `find_secret_fields` is pure AST (no git). `propose_exemplar`
ranks by an injected `line_dates` map (newest wins) so tests don't need a git history;
`blame_line_dates` is the best-effort default source of that map (git subprocess,
returns {} on any failure — never raises, never blocks). The deployment layer
(terraform / k8s) is mirrored by the agent per the SKILL prose from the @-linked or
blame-found exemplar block; this module covers the config layer + the ranking."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass

# SecretStr/SecretBytes (pydantic) is the strongest signal — a field the customer
# already chose to treat as a secret. Name suffixes are the fallback for plain-typed
# secrets. Deliberately NOT a bare `_key` (matches cache_key / idempotency_key / a
# public_key) — a false exemplar makes the engineer correct it, but precision keeps
# the auto-proposal trustworthy.
_SECRET_ANNOTATIONS = ("SecretStr", "SecretBytes")
_SECRET_NAME_SUFFIXES = (
    "_api_key", "_apikey", "_secret_key", "_access_key", "_private_key", "_token",
    "_secret", "_password", "_passwd", "_credential", "_credentials", "_dsn",
)


@dataclass(frozen=True)
class SecretField:
    name: str               # the field name, e.g. "stripe_api_key"
    lineno: int             # 1-based line of the field definition
    annotation: str | None  # the annotation source, e.g. "SecretStr" / "SecretStr | None"
    reason: str             # why it's a secret ("SecretStr annotation" / "name suffix *_api_key")


@dataclass(frozen=True)
class Exemplar:
    field: SecretField
    confidence: str         # "blame" (dated, most-recently-added) | "position" (last-defined fallback)


def _secret_reason(name: str, annotation: str | None) -> str | None:
    a = annotation or ""
    if any(tok in a for tok in _SECRET_ANNOTATIONS):
        return "SecretStr annotation"
    n = name.lower()
    for suf in _SECRET_NAME_SUFFIXES:
        if n.endswith(suf) or n == suf.lstrip("_"):
            return f"name suffix *{suf}"
    return None


def find_secret_fields(source: str) -> list[SecretField]:
    """All secret-typed annotated fields (`x: SecretStr` / `stripe_api_key: str`) in
    `source`, in definition order. Pure AST — no git, no I/O. [] on a syntax error or
    when nothing looks like a secret (the honest 'no exemplar here' state)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[SecretField] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            ann = _unparse(node.annotation)
            reason = _secret_reason(node.target.id, ann)
            if reason is not None:
                out.append(SecretField(name=node.target.id, lineno=node.lineno,
                                       annotation=ann, reason=reason))
    return out


def _unparse(node) -> str | None:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - older/edge nodes -> no annotation string
        return None


def propose_exemplar(source: str, line_dates: dict[int, float] | None = None) -> "Exemplar | None":
    """The most-recently-added secret field to mirror, or None when there are none.

    `line_dates` maps a field's line -> a recency key (e.g. git author epoch); the
    field with the newest date wins (confidence="blame"). Without it (blame
    unavailable / not a git repo), falls back to the LAST-DEFINED field
    (confidence="position") — a weaker proxy the engineer should eyeball. CANDIDATE
    ONLY: the engineer confirms or @-links a different secret at Phase 1.7."""
    fields = find_secret_fields(source)
    if not fields:
        return None
    if line_dates:
        dated = [(line_dates.get(f.lineno), f) for f in fields if f.lineno in line_dates]
        if dated:
            # newest date wins; ties broken by later line (later in the file = later add)
            best = max(dated, key=lambda dl: (dl[0], dl[1].lineno))
            return Exemplar(field=best[1], confidence="blame")
    return Exemplar(field=fields[-1], confidence="position")


def blame_line_dates(file_path: str, linenos: list[int]) -> dict[int, float]:
    """Best-effort `git blame` author-times for `linenos` of `file_path`, as
    {lineno: epoch_seconds}. Returns {} on ANY failure (not a git repo, git absent,
    uncommitted file, parse hiccup) — never raises, never blocks. Isolated from the
    pure proposer so tests inject dates instead of needing a git history."""
    if not linenos:
        return {}
    try:
        out = subprocess.run(
            ["git", "blame", "--porcelain", file_path],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    return _parse_porcelain_author_times(out, set(linenos))


def _parse_porcelain_author_times(porcelain: str, want: set[int]) -> dict[int, float]:
    """Parse `git blame --porcelain` into {final_lineno: author-time epoch}. A hunk
    header is `<40-hex-sha> <orig> <final> [count]`; `author-time <epoch>` follows ONLY
    the FIRST time a commit appears, so track time per-SHA and map each line's SHA to
    it (subsequent lines from the same commit repeat the SHA header but not the time)."""
    sha_time: dict[str, float] = {}     # sha -> author epoch
    line_sha: dict[int, str] = {}       # final lineno -> sha
    cur_sha: str | None = None
    for raw in porcelain.splitlines():
        parts = raw.split(" ")
        if (len(parts) >= 3 and len(parts[0]) == 40
                and all(c in "0123456789abcdef" for c in parts[0])):
            cur_sha = parts[0]
            try:
                line_sha[int(parts[2])] = cur_sha
            except ValueError:
                pass
        elif raw.startswith("author-time ") and cur_sha is not None:
            try:
                sha_time[cur_sha] = float(raw[len("author-time "):].strip())
            except ValueError:
                pass
    return {ln: sha_time[line_sha[ln]]
            for ln in want
            if ln in line_sha and line_sha[ln] in sha_time}
