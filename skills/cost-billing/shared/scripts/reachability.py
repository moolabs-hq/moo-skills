"""Reachability gate — does a target emit-site function actually RUN in production?

The #572 dead-emit class: the codemod placed valid, well-named, correctly-anchored emits
inside functions PRODUCTION never calls — a test-only method, an admin-only sweep, or a
dead parallel twin of the live function. EVERY static gate (name match, AST-verify the
emit is inside the function, file:line signoff, adversarial review) passed, because each
checks the artifact IN ISOLATION. Liveness ("does this path execute when the billable
action happens") is a DYNAMIC property none of them inspects. And name-matching
systematically anchors to the tidy standalone twin — which is usually the dead one
(`generate_dunning_email_async` over the live `draft_dunning`).

This is the missing check. For a target function, find its CALLERS and classify them by
file kind. Only-test / only-admin / no-callers -> the emit is dead-in-production, FLAGGED
loudly (like the entity_id gate). It does NOT auto-decide liveness — dynamic dispatch /
config routing / feature flags can't be seen statically, so a prod caller yields
`live_candidate`, not "confirmed live"; the engineer signoff still owes the runtime trace
(trigger the flow, see the emit fire). Bounded + deterministic: callers via `grep_tokens`,
kind via path heuristics. NOT framework-entry-point detection (that is an unbounded zoo).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import secret_exemplar  # grep_tokens — same shared/scripts dir

# Path-kind heuristics — GENERIC across languages, deliberately conservative (a false
# "flag" costs a human glance; a false "live" ships a dead meter). Only clearly-non-prod
# locations are flaggable: test files/dirs, e2e/integration suites, admin endpoints.
_TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)(/|$)"
    r"|(^|/)(test_[^/]*|[^/]*_test|[^/]*\.test|[^/]*\.spec)\.[a-z0-9]+$"
    r"|(^|/)conftest\.py$",
    re.I,
)
_E2E_RE = re.compile(r"(^|/)(e2e|integration)(/|$)", re.I)
_ADMIN_RE = re.compile(r"(^|/)admin(/|$)|(^|/)[a-z0-9_]*admin[a-z0-9_]*\.[a-z0-9]+$", re.I)


def _file_kind(rel: str) -> str:
    """Classify a caller's file: 'test' | 'e2e' | 'admin' | 'prod' (the default)."""
    r = rel.replace("\\", "/")
    if _TEST_RE.search(r):
        return "test"
    if _E2E_RE.search(r):
        return "e2e"
    if _ADMIN_RE.search(r):
        return "admin"
    return "prod"


@dataclass(frozen=True)
class Reachability:
    # FLAG buckets (provably dead-in-prod, deterministic): orphan | test_only |
    # admin_e2e_only. NON-flag bucket: `unverified` — has a prod caller, so NOT provably
    # dead, but NOT proven live either (a prod caller can itself be transitively dead).
    # `unverified` is deliberately NOT "live_candidate": it is not a pass — it means the
    # static gate can't decide, and the runtime trace is owed. Static reachability cannot
    # prove liveness (dynamic dispatch / entry points are unbounded); it only catches the
    # cheap, certain dead cases early.
    status: str
    callers: list[tuple[str, int]]    # (relpath, lineno)
    prod_caller_files: list[str]
    note: str

    @property
    def flagged(self) -> bool:
        """Only the PROVABLY-dead buckets flag. `unverified` does not — but it is not a
        pass; it requires the runtime trace (see note)."""
        return self.status in ("orphan", "test_only", "admin_e2e_only")


_DEF_RE_TMPL = r"(async\s+)?(def|function|func|fn)\s+{name}\b|{name}\s*[:=]\s*(async\s*)?(function|\()"

# A "caller" must be CODE — a function name appearing in docs/specs/config is a MENTION,
# not a call (observed: a target's only 'prod' callers were docs/*.md, masking that its
# only real caller is a test). Same lesson as the secret-mirror doc exclusion.
_NON_CODE_EXT = (".md", ".rst", ".txt", ".adoc", ".markdown",
                 ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".lock", ".csv")


def find_callers(repo_root: str, func_name: str, def_file: str | None = None,
                 timeout: int = 120) -> list[tuple[str, int]]:
    """Call-shaped references to `func_name` across the repo, MINUS its own definition.

    Greps `func_name(` (the call shape) and drops lines that are the DEFINITION
    (`def/function/func func_name` / `func_name = function` / `func_name: (`). The emit
    site's own def-site is not a caller."""
    if not func_name:
        return []
    def_re = re.compile(_DEF_RE_TMPL.format(name=re.escape(func_name)))
    out: list[tuple[str, int]] = []
    for rel, lineno, snippet in secret_exemplar.grep_tokens(repo_root, [f"{func_name}("], timeout=timeout):
        if rel.lower().endswith(_NON_CODE_EXT):
            continue  # a mention in docs/specs/config is not a call
        if def_re.search(snippet):
            continue  # the definition, not a call
        out.append((rel, lineno))
    return out


def classify_reachability(repo_root: str, func_name: str, def_file: str | None = None,
                          timeout: int = 120) -> Reachability:
    """Is `func_name` (a target emit site) reachable from production code? Classifies its
    callers; only-test / only-admin / orphan are FLAGGED (`.flagged`)."""
    callers = find_callers(repo_root, func_name, def_file, timeout)
    if not callers:
        return Reachability(
            "orphan", [], [],
            f"no callers of '{func_name}' found — dead, or dynamically dispatched; "
            f"confirm at runtime before billing on it",
        )
    prod = sorted({f for f, _ in callers if _file_kind(f) == "prod"})
    if prod:
        # ANY prod caller -> unverified (never flag), even alongside admin/test callers.
        return Reachability(
            "unverified", callers, prod,
            f"has prod-file caller(s) {prod[:3]} — NOT provably dead, but liveness is NOT "
            f"proven (a prod caller can itself be transitively dead). This is NOT a pass: "
            f"the engineer signoff MUST run the runtime trace — trigger the billable action "
            f"once and confirm the emit fires.",
        )
    kinds = sorted({_file_kind(f) for f, _ in callers})
    status = "test_only" if kinds == ["test"] else "admin_e2e_only"
    return Reachability(
        status, callers, [],
        f"callers ONLY in {kinds} files — NOT reached in production. The emit is in a "
        f"dead/test/admin path; the meter will stay at ZERO. Re-anchor to the live "
        f"function, or de-scope this event.",
    )


def audit_emit_reachability(entries, repo_root, fn_key="target_function", timeout=120):
    """Run the gate over emit-site inventory entries (each carrying `target_function`).

    Returns (annotations, findings):
      - annotations: {entry_index: Reachability} — write the verdict back into the
        inventory entry (`reachability: {status, note}`) so the review sees it.
      - findings: the FLAGGED entries (provably dead-in-prod). These BLOCK the engineer
        signoff — the engineer must re-anchor to the live function or explicitly de-scope
        the event. `unverified` entries are NOT findings, but the signoff still owes each
        one the runtime trace (this gate cannot prove liveness, only catch certain death)."""
    annotations: dict[int, Reachability] = {}
    findings: list[dict] = []
    for i, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        fn = entry.get(fn_key)
        if not fn:
            continue
        r = classify_reachability(repo_root, fn, entry.get("file"), timeout)
        annotations[i] = r
        if r.flagged:
            findings.append({
                "target_function": fn,
                "file": entry.get("file"),
                "workflow_id": entry.get("workflow_id"),
                "status": r.status,
                "note": r.note,
            })
    return annotations, findings
