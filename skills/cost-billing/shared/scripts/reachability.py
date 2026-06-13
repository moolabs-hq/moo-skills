"""Reachability gate — does a target emit-site function actually RUN in production?

The #572 dead-emit class: the codemod placed valid, well-named, correctly-anchored emits
inside functions PRODUCTION never calls — a test-only method, an admin-only sweep, or a
dead parallel twin of the live function. EVERY static gate (name match, AST-verify the
emit is inside the function, file:line signoff, adversarial review) passed, because each
checks the artifact IN ISOLATION. Liveness ("does this path execute when the billable
action happens") is a DYNAMIC property none of them inspects. And name-matching
systematically anchors to the tidy standalone twin — which is usually the dead one.

This is the missing check. For a target function, find its CALLERS and classify them by
file kind. Only-test / only-admin / no-callers -> the emit is dead-in-production, FLAGGED
loudly. It does NOT auto-decide liveness — dynamic dispatch / config routing / feature
flags can't be seen statically, so a prod caller yields `unverified` (NOT "live"); the
engineer signoff still owes the runtime trace. Bounded + deterministic: callers via
`grep_tokens`, kind via path heuristics. NOT framework-entry-point detection (unbounded).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

import secret_exemplar  # grep_tokens — same shared/scripts dir

# Path-kind heuristics — GENERIC across languages, deliberately CONSERVATIVE (a false
# "flag" costs a human glance; a false "live" ships a dead meter — but a false flag on a
# LIVE site erodes trust until the gate is ignored, so over-flagging is the worse error).
# Only clearly-non-prod locations are flaggable.
_TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)(/|$)"
    r"|(^|/)(test_[^/]*|[^/]*_test|[^/]*\.test|[^/]*\.spec)\.[a-z0-9]+$"
    r"|(^|/)conftest\.py$",
    re.I,
)
# `e2e/` only. `integration` is a real DOMAIN dir (payment/partner integration) in many
# prod repos — it is NOT e2e unless under a test path, which `_TEST_RE` already catches
# (`tests/integration/...`). Flagging a standalone `integration/` is a false flag.
_E2E_RE = re.compile(r"(^|/)e2e(/|$)", re.I)
# `admin/` dir, OR a filename that STARTS with `admin` (`admin.py`, `admin_router.py`) —
# NOT any filename merely CONTAINING admin (`superadmin.py`, `load_administration.py` are
# prod). The substring arm was over-broad and false-flagged prod files.
_ADMIN_RE = re.compile(r"(^|/)admin(/|$)|(^|/)admin(_[a-z0-9_]+)?\.[a-z0-9]+$", re.I)

# A "caller" must be CODE — a function name in docs/specs/config is a MENTION, not a call.
_NON_CODE_EXT = (".md", ".rst", ".txt", ".adoc", ".markdown",
                 ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".lock", ".csv")


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


def _is_boundaried_ref(snippet: str, name: str) -> bool:
    """`name(` with NO identifier char immediately before it — so `re_foo(`/`my_foo(` do
    NOT count as callers of `foo` (prefix collision -> false unverified)."""
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*\(", snippet) is not None


def _is_definition(snippet: str, name: str) -> bool:
    """Is this line the DEFINITION of `name` (not a call)? Covers keyword defs
    (py `def`, ts `function`, go/rust `func`/`fn`), Go RECEIVER methods
    (`func (s *Svc) name(`), TS/Java CLASS methods (`name(args): T {`), and arrow/assign
    (`name = (...) =>`). Without this, a method def with no leading keyword (Go/TS/Rust)
    is counted as a caller -> a fully orphan emit returns `unverified`, not `orphan`."""
    n = re.escape(name)
    return bool(
        re.search(rf"(?:^|\W)(?:async\s+)?(?:def|function|func|fn)\s+{n}\s*[(<]", snippet)
        or re.search(rf"\bfunc\s*\([^)]*\)\s*{n}\s*\(", snippet)              # Go receiver method
        or re.search(rf"^\s*(?:public|private|protected|static|readonly|export|async|\s)*"
                     rf"{n}\s*\([^;{{]*\)\s*(?::[^={{;]+)?\{{", snippet)        # class method def (ends `{`)
        or re.search(rf"(?<![A-Za-z0-9_]){n}\s*[:=]\s*(?:async\s*)?(?:function\b|\()", snippet)
    )


@dataclass(frozen=True)
class Reachability:
    # FLAG buckets (provably dead-in-prod, deterministic): orphan | test_only |
    # admin_e2e_only. Plus `error` (the gate could not run — e.g. grep timeout — treated
    # as blocking, never a silent skip). NON-flag bucket: `unverified` — has a prod
    # caller, so NOT provably dead, but NOT proven live (a prod caller can itself be
    # transitively dead). `unverified` is deliberately NOT a pass; the runtime trace is
    # owed. Static reachability cannot PROVE liveness; it only catches the cheap dead cases.
    status: str
    callers: list[tuple[str, int]]    # (relpath, lineno) relative to the git toplevel
    prod_caller_files: list[str]
    note: str

    @property
    def flagged(self) -> bool:
        """Only PROVABLY-dead (or error) buckets flag. `unverified` does not — but it is
        not a pass; it requires the runtime trace (see note)."""
        return self.status in ("orphan", "test_only", "admin_e2e_only", "error")


def _service_rel(repo_root: str) -> str:
    """`repo_root` relative to the git toplevel ('.' when repo_root IS the toplevel).
    Used to SCOPE callers to the service: `grep_tokens` resolves the toplevel and searches
    the whole monorepo, so a same-named function in a SIBLING service would otherwise count
    as a prod caller -> false `unverified` masking a service-local dead emit."""
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            # realpath BOTH sides — git resolves symlinks (macOS /var -> /private/var) so a
            # raw relpath against the symlinked repo_root yields a bogus `../..` and filters
            # out every caller. A `..` result (repo_root not under toplevel) -> no scoping.
            rel = os.path.relpath(os.path.realpath(repo_root),
                                  os.path.realpath(r.stdout.strip())).replace("\\", "/")
            return "." if rel.startswith("..") else rel
    except (OSError, subprocess.SubprocessError):
        pass
    return "."


def find_callers(repo_root: str, func_name: str,
                 scope_to_service: bool = True, timeout: int = 120) -> list[tuple[str, int]]:
    """Real call-shaped references to `func_name`, MINUS its own definition, scoped to the
    service. Drops: docs/config files; prefix-collision hits (`re_foo(`); definition lines
    (any language); and — when `repo_root` is a subdir of a monorepo — callers OUTSIDE the
    service dir. Limitation: a legitimate caller from OUTSIDE the service dir (a monorepo
    orchestration layer) is therefore not counted; such a target may read as `orphan` and
    needs the runtime trace / manual override. Pass `scope_to_service=False` to search the
    whole repo."""
    if not func_name:
        return []
    svc = _service_rel(repo_root) if scope_to_service else "."
    out: list[tuple[str, int]] = []
    for rel, lineno, snippet in secret_exemplar.grep_tokens(repo_root, [f"{func_name}("], timeout=timeout):
        if svc != "." and not (rel == svc or rel.startswith(svc + "/")):
            continue  # sibling-service / out-of-service hit — not this service's caller
        if rel.lower().endswith(_NON_CODE_EXT):
            continue  # a mention in docs/specs/config is not a call
        if not _is_boundaried_ref(snippet, func_name):
            continue  # prefix collision (re_foo / my_foo), not a call of func_name
        if _is_definition(snippet, func_name):
            continue  # the definition (incl. keyword-less Go/TS/Rust methods), not a call
        out.append((rel, lineno))
    return out


def classify_reachability(repo_root: str, func_name: str,
                          scope_to_service: bool = True, timeout: int = 120) -> Reachability:
    """Is `func_name` (a target emit site) reachable from production code? Classifies its
    callers; only-test / only-admin / orphan are FLAGGED (`.flagged`)."""
    callers = find_callers(repo_root, func_name, scope_to_service, timeout)
    if not callers:
        return Reachability(
            "orphan", [], [],
            f"no callers of '{func_name}' found in the service — dead, dynamically "
            f"dispatched, or only called from OUTSIDE the service dir; confirm at runtime "
            f"before billing on it",
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


def audit_emit_reachability(entries, repo_root, fn_key="target_function",
                            scope_to_service=True, timeout=120):
    """Run the gate over emit-site inventory entries (each carrying `target_function`).

    Returns (annotations, findings, owed_trace) — THREE buckets so `unverified` can NEVER
    be silently collapsed to a pass (the #572 root cause was "green static check read as
    done"):
      - annotations: {entry_index: Reachability} — write the verdict into the entry.
      - findings: FLAGGED entries (provably dead, or an `error`/timeout) — these BLOCK the
        engineer signoff until re-anchored or de-scoped.
      - owed_trace: the `unverified` entries — NOT a pass; each one STILL owes the runtime
        trace at signoff. A consumer must treat a non-empty owed_trace as outstanding work.

    A `grep_tokens` timeout on ONE entry (a common name in a huge monorepo) is isolated to
    that entry (status `error`, flagged) — it never aborts the batch and silently drops the
    rest."""
    annotations: dict[int, Reachability] = {}
    findings: list[dict] = []
    owed_trace: list[dict] = []
    for i, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        fn = entry.get(fn_key)
        if not fn:
            continue
        try:
            r = classify_reachability(repo_root, fn, scope_to_service, timeout)
        except subprocess.TimeoutExpired:
            r = Reachability("error", [], [],
                             f"reachability gate timed out for '{fn}' — gate ran INCOMPLETE "
                             f"for this entry; resolve manually (do not treat as a pass)")
        annotations[i] = r
        rec = {"target_function": fn, "file": entry.get("file"),
               "workflow_id": entry.get("workflow_id"), "status": r.status, "note": r.note}
        if r.flagged:
            findings.append(rec)
        elif r.status == "unverified":
            owed_trace.append(rec)
    return annotations, findings, owed_trace
