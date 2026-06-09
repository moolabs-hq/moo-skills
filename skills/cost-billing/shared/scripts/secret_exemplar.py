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
import os
import re
import subprocess
from collections import Counter
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
    field: SecretField                   # the secret to MIRROR (primary = most-recently-added)
    confidence: str                      # "blame" (dated) | "position" (last-defined fallback)
    considered: tuple[SecretField, ...]  # the last N (<=3) the OPINION is formed from, newest first
    secret_type: str                     # consensus type across `considered`: "SecretStr" | "plain" | "mixed"
    agreement: str                       # "single" | "unanimous" | "majority" | "split" — how settled the recent convention is


@dataclass(frozen=True)
class AccessIdiom:
    kind: str               # "singleton" | "factory" | "unknown"
    import_name: str | None  # the symbol to import: "settings" / "get_settings" / None
    # `read("moolabs_api_key")` -> "settings.moolabs_api_key" / "get_settings().moolabs_api_key"

    def read(self, field: str) -> str | None:
        if self.kind == "singleton":
            return f"{self.import_name}.{field}"
        if self.kind == "factory":
            return f"{self.import_name}().{field}"
        return None


def _secret_reason(name: str, annotation: str | None) -> str | None:
    a = annotation or ""
    if any(tok in a for tok in _SECRET_ANNOTATIONS):
        return "SecretStr annotation"
    n = name.lower()
    for suf in _SECRET_NAME_SUFFIXES:
        if n.endswith(suf) or n == suf.lstrip("_"):
            return f"name suffix *{suf}"
    return None


def _is_settings_class(node: ast.ClassDef) -> bool:
    """A config Settings class by the RELIABLE signals only — named `*Settings` or
    subclassing `*Settings`/`*BaseSettings`. Deliberately NOT 'has a secret field'
    (that would re-include a `LoginForm` with a `password` field — a DTO, not config)."""
    if node.name.lower().endswith("settings"):
        return True
    return any((_last_name(b) or "").lower().endswith("settings") for b in node.bases)


def find_secret_fields(source: str) -> list[SecretField]:
    """Secret-typed annotated fields (`x: SecretStr` / `stripe_api_key: str`) in the
    customer's CONFIG Settings class(es), in definition order. Scoped to *Settings
    classes (NOT every class) so a DTO's `password` / a function-local `token` is not
    proposed as the secret exemplar. Pure AST — no git, no I/O. [] on a syntax error
    or when nothing looks like a secret (the honest 'no exemplar here' state)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[SecretField] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and _is_settings_class(node)):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                ann = _unparse(stmt.annotation)
                reason = _secret_reason(stmt.target.id, ann)
                if reason is not None:
                    out.append(SecretField(name=stmt.target.id, lineno=stmt.lineno,
                                           annotation=ann, reason=reason))
    out.sort(key=lambda f: f.lineno)
    return out


def _unparse(node) -> str | None:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - older/edge nodes -> no annotation string
        return None


def _field_type(f: SecretField) -> str:
    return ("SecretStr" if f.annotation and any(t in f.annotation for t in _SECRET_ANNOTATIONS)
            else "plain")


def _ranked(fields: list[SecretField], line_dates: dict[int, float] | None):
    """Secret fields most-recently-added FIRST, + the confidence of that ordering."""
    if line_dates:
        dated = sorted((f for f in fields if f.lineno in line_dates),
                       key=lambda f: (line_dates[f.lineno], f.lineno), reverse=True)
        undated = sorted((f for f in fields if f.lineno not in line_dates),
                         key=lambda f: f.lineno, reverse=True)
        if dated:
            return dated + undated, "blame"
    return sorted(fields, key=lambda f: f.lineno, reverse=True), "position"


def propose_exemplar(source: str, line_dates: dict[int, float] | None = None,
                     n: int = 3) -> "Exemplar | None":
    """Form an OPINION from the customer's last `n` (default 3) secrets — not one.

    One secret can be an anomaly (a deprecated path, a one-off); the last few are the
    convention the team currently considers correct. Ranks secret fields newest-first
    (`line_dates` = git author epochs -> confidence="blame"; absent -> last-defined,
    confidence="position"), takes the top `n` as `considered`, mirrors the PRIMARY
    (newest) field, and reads a CONSENSUS `secret_type` (majority SecretStr vs plain)
    + an `agreement` level so the caller knows how settled the recent pattern is
    (split -> the engineer should look harder before confirming). None when there are
    no secrets. CANDIDATE ONLY — confirmed by a human at Phase 1.7."""
    fields = find_secret_fields(source)
    if not fields:
        return None
    ranked, confidence = _ranked(fields, line_dates)
    considered = tuple(ranked[:n])
    types = [_field_type(f) for f in considered]
    top, n_top = Counter(types).most_common(1)[0]
    if len(considered) == 1:
        agreement, secret_type = "single", top
    elif n_top == len(considered):
        agreement, secret_type = "unanimous", top
    elif n_top > len(considered) / 2:
        agreement, secret_type = "majority", top
    else:
        agreement, secret_type = "split", "mixed"
    return Exemplar(field=ranked[0], confidence=confidence, considered=considered,
                    secret_type=secret_type, agreement=agreement)


def _last_name(node) -> str | None:
    s = _unparse(node)
    return s.rsplit(".", 1)[-1] if s else None


def _class_is_settings(node: ast.ClassDef) -> bool:
    """A Settings class: subclasses *Settings (BaseSettings/Settings), is named
    *Settings, or carries secret fields."""
    if node.name.lower().endswith("settings"):
        return True
    if any((_last_name(b) or "").lower().endswith("settings") for b in node.bases):
        return True
    return any(isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
               and _secret_reason(s.target.id, _unparse(s.annotation)) for s in node.body)


_FACTORY_VERBS = ("get_", "load_", "make_", "build_", "provide_", "create_", "fetch_", "resolve_")


def detect_access_idiom(source: str) -> AccessIdiom:
    """SEARCH (not blame) for HOW the config is read — the dimension blame can't see.
    Mirroring the secret FIELD is not enough; the helper must read it the way the
    customer's code does:
      - factory:   `def get_settings(): ...`         -> `from <mod> import get_settings`
                                                         + `get_settings().moolabs_api_key`
      - singleton: `settings = Settings()` (module)  -> `from <mod> import settings`
                                                         + `settings.moolabs_api_key`
      - unknown:   neither (DI / custom)             -> FLAG; caller falls to the stub.
    Returns the idiom + the import symbol; `.read(field)` builds the accessor. moo-arc
    is a singleton — the common case the get_settings()-hardcoded helper breaks on."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return AccessIdiom("unknown", None)
    settings_classes = {n.name for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef) and _class_is_settings(n)}
    # factory first: an explicit accessor function is the intended public API. A
    # `*settings` name is only a factory when it PROVIDES the config (get/load/build…)
    # — NOT a mutator (`reset_settings` / `update_settings` / `save_settings` return
    # None; importing one as the accessor -> None.moolabs_api_key AttributeError).
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nm = node.name.lower()
            if node.name == "get_settings" or (
                    nm.endswith("settings")
                    and any(nm.startswith(v) for v in _FACTORY_VERBS)):
                return AccessIdiom("factory", node.name)
    # singleton: a module-level `name = <SettingsClass>()`.
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and _last_name(node.value.func) in settings_classes):
            return AccessIdiom("singleton", node.targets[0].id)
    return AccessIdiom("unknown", None)


def env_var_for_field(field_name: str) -> str:
    """Config field -> the env var it maps to (UPPER_SNAKE). The env var name is the
    STABLE IDENTIFIER that bridges config and deployment across EVERY pattern (terraform
    `name = "X"`, k8s `name: X` / secretKeyRef, `os.environ["X"]`, `.env` `X=`). Handles
    snake_case (`moolabs_api_key` -> `MOOLABS_API_KEY`) and camel/Pascal
    (`arcGlobalApiKey` -> `ARC_GLOBAL_API_KEY`). NOTE: a customer using an `env_prefix`
    / Field(alias=...) maps differently — the trace also greps the FIELD name to catch
    the config site, and the agent widens tokens from the first hit."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", field_name)   # camel boundary: apiKey -> api_Key
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)          # acronym boundary: URLKey -> URL_Key
    return re.sub(r"_+", "_", s).upper()


_GREP_SKIP_DIRS = (".git", ".terraform", "node_modules", "dist", "build", "vendor",
                   ".venv", "venv", "__pycache__", ".mypy_cache", ".next")


def _parse_grep_lines(out: str, repo_root: str | None) -> list[tuple[str, int, str]]:
    """`path:lineno:content` -> [(relpath, lineno, snippet)]. `repo_root=None` means the
    paths are already repo-relative (git grep); otherwise relpath them from repo_root."""
    hits: list[tuple[str, int, str]] = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        try:
            ln = int(parts[1])
        except ValueError:
            continue
        path = parts[0] if repo_root is None else os.path.relpath(parts[0], repo_root)
        hits.append((path, ln, parts[2].strip()[:200]))
    return hits


def grep_tokens(repo_root: str, tokens: list[str], timeout: int = 120) -> list[tuple[str, int, str]]:
    """FORMAT-AGNOSTIC wide search: every file:line under `repo_root` mentioning any
    token. No parsing — the AGENT classifies the hits + follows references (config vs
    terraform vs k8s vs secrets-manager is unbounded; do NOT encode a format zoo here).
    Returns [(relpath, lineno, snippet)] — the breadcrumbs of a real secret's path.

    PRIMARY is `git grep`: TRACKED files only, so it auto-skips .venv/node_modules/
    .terraform and is ~0.2s where a whole-repo `grep -rn` is ~90s on a real monorepo.
    That speed gap is the bug class: `grep -rn` blows the timeout, and a swallowed
    timeout returns [] — INDISTINGUISHABLE from 'no wiring exists', which ships an empty
    secret path. So a TimeoutExpired is RAISED, never returned as []: a timeout must
    never look like 'nothing found'. Falls back to `grep -rn` (vendor-excluded) only when
    the repo is not a git checkout; returns [] only when no search tool is available."""
    toks = [t for t in tokens if t]
    if not toks:
        return []
    pattern = "|".join(re.escape(t) for t in toks)
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "grep", "-nIE", pattern],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode in (0, 1):  # 0 = matches, 1 = no matches — both are valid results
            return _parse_grep_lines(r.stdout, repo_root=None)  # paths already repo-relative
    except subprocess.TimeoutExpired:
        raise  # NEVER swallow a timeout into [] — that masks the #550 empty-path gap
    except (OSError, subprocess.SubprocessError):
        pass  # git absent / not a git checkout -> fall back
    excludes = [f"--exclude-dir={d}" for d in _GREP_SKIP_DIRS]
    try:
        r = subprocess.run(["grep", "-rnIE", pattern, *excludes, str(repo_root)],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise
    except (OSError, subprocess.SubprocessError):
        return []
    return _parse_grep_lines(r.stdout, repo_root=str(repo_root))


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
