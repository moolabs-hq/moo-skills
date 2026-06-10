"""Deterministic infra-edit planner — ADD the new secret's deployment wiring by mirroring
where the customer's RECENT secrets were placed (the same last-three / point-at-a-real-thing
pattern the config exemplar uses, applied to infra placement instead of a config field).

Why ANCHORED + ADDITIVE (not a token swap): a blind swap of the exemplar's store key is
unsafe — store keys are SHARED across many secrets (moo-arc: `shared/api-key` backs six),
so swapping it corrupts unrelated secrets, and the tokens also appear in app code. Instead:

  - Anchor on the exemplar's UNIQUE env-var name (`ARC_GLOBAL_API_KEY`). `grep_tokens` of
    that name returns ONLY the exemplar's own wiring lines — no other secret contains it.
  - For each anchored line, produce a NEW SIBLING line for the new secret (env-var swapped,
    store key swapped to a new DEDICATED key) and INSERT it after the anchor. The original
    line and every other secret are untouched → the shared-key corruption is impossible.
  - Scope to infra files (default `.tf`/`.tfvars`/`.hcl`) — never app code or prose.
  - Idempotent: a file already carrying the new env var is skipped.

Apply (`terraform apply`) and seeding the secret VALUE stay human; this WRITES the wiring
as a reviewable, additive diff (validate with `terraform fmt`/`validate` before apply).
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass

import secret_exemplar

_DEFAULT_INFRA_EXTS = (".tf", ".tfvars", ".hcl")

# A secrets-map DECLARATION entry: `"<ns>/<name>" = { description = "..." }`. Captures the
# leading indentation so the mirrored entry aligns (terraform fmt re-aligns regardless).
_DECL_RE = re.compile(r'^(\s*)"([a-z0-9]+/[a-z0-9._-]+)"\s*=\s*\{\s*description')


@dataclass(frozen=True)
class InsertEdit:
    """A new sibling wiring line for the new secret, inserted AFTER `anchor_line`."""
    file: str          # repo-relative path
    anchor_line: int   # 1-based line the new entry is inserted AFTER (the exemplar's)
    anchor_text: str   # the exemplar's line (for the reviewer to see what was mirrored)
    new_line: str      # the new secret's line — additive, never replaces the anchor


def _apply_swaps(text: str, swaps: dict[str, str]) -> str:
    out = text
    for old, new in swaps.items():
        if old:
            out = out.replace(old, new)
    return out


def plan_inserts(
    repo_root: str,
    anchor_env: str,
    swaps: dict[str, str],
    exts: tuple[str, ...] = _DEFAULT_INFRA_EXTS,
    timeout: int = 120,
) -> list[InsertEdit]:
    """ADDITIVE inserts for the new secret, anchored on the exemplar's UNIQUE env var.

    `anchor_env` is the exemplar's env-var name (e.g. `ARC_GLOBAL_API_KEY`) — grepped ALONE
    so only the exemplar's own lines match. `swaps` maps the exemplar's tokens to the new
    secret's (e.g. `{"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY", "shared/api-key":
    "arc/moolabs-api-key"}`) and is applied ONLY on those anchored lines. Returns the
    sibling lines to INSERT after each anchor (the originals stay). A file already carrying
    the new env var (`swaps[anchor_env]`) is skipped (idempotent — already wired)."""
    if not anchor_env or not swaps:
        return []
    new_env = swaps.get(anchor_env, "")
    hits = secret_exemplar.grep_tokens(repo_root, [anchor_env], timeout=timeout)

    file_lines: dict[str, list[str]] = {}
    already_wired: set[str] = set()
    edits: list[InsertEdit] = []
    seen: set[tuple[str, int]] = set()

    for rel, lineno, _snippet in hits:
        if not rel.lower().endswith(exts):
            continue  # infra files only — never app code (.py/.go) or prose (.md)
        if rel not in file_lines:
            try:
                with open(os.path.join(repo_root, rel)) as f:
                    file_lines[rel] = f.readlines()
            except OSError:
                file_lines[rel] = []
            if new_env and new_env in "".join(file_lines[rel]):
                already_wired.add(rel)
        if rel in already_wired:
            continue
        lines = file_lines[rel]
        if not (1 <= lineno <= len(lines)):
            continue
        key = (rel, lineno)
        if key in seen:
            continue
        seen.add(key)
        anchor_text = lines[lineno - 1].rstrip("\n")
        new_line = _apply_swaps(anchor_text, swaps)
        if new_line == anchor_text:
            continue  # the anchor line didn't carry the env var verbatim — skip
        edits.append(InsertEdit(file=rel, anchor_line=lineno, anchor_text=anchor_text, new_line=new_line))
    return edits


def plan_declaration_insert(
    repo_root: str,
    new_store_key: str,
    description: str,
    prefer_files: tuple[str, ...] | list[str] | None = None,
    exts: tuple[str, ...] = _DEFAULT_INFRA_EXTS,
    timeout: int = 120,
) -> InsertEdit | None:
    """Hop 2 — declare the new store key in the secrets map (so `secret_arns[new_store_key]`
    resolves). Mirror an EXISTING declaration entry's structure, anchoring in the SAME FILE as
    the injection when possible (`prefer_files` = the injection edits' files) — moo-arc has
    PER-ENVIRONMENT secrets maps, so a prod injection must get a prod declaration, not dev's.
    Idempotent: returns None if `new_store_key` is already declared in a preferred file (or no
    declaration map is found — then it stays a flagged checklist item)."""
    ns = new_store_key.split("/")[0]
    prefer = set(prefer_files or [])
    hits = secret_exemplar.grep_tokens(repo_root, [f'"{ns}/'], timeout=timeout)
    seen_files: dict[str, list[str]] = {}
    candidates: list[tuple[str, int, str]] = []  # (file, line, indentation)
    for rel, lineno, _snippet in hits:
        if not rel.lower().endswith(exts):
            continue
        if rel not in seen_files:
            try:
                with open(os.path.join(repo_root, rel)) as f:
                    seen_files[rel] = f.readlines()
            except OSError:
                seen_files[rel] = []
        lines = seen_files[rel]
        if 1 <= lineno <= len(lines):
            m = _DECL_RE.match(lines[lineno - 1])
            if m:
                candidates.append((rel, lineno, m.group(1)))
    if not candidates:
        return None
    # co-locate with the injection: prefer a candidate in one of the injection's files.
    chosen = next((c for c in candidates if c[0] in prefer), candidates[0])
    rel, lineno, indent = chosen
    if f'"{new_store_key}"' in "".join(seen_files[rel]):
        return None  # already declared in the chosen file — idempotent
    new_line = f'{indent}"{new_store_key}" = {{ description = "{description}" }}'
    return InsertEdit(file=rel, anchor_line=lineno,
                      anchor_text=seen_files[rel][lineno - 1].rstrip("\n"), new_line=new_line)


def apply_inserts(repo_root: str, edits: list[InsertEdit]) -> list[str]:
    """WRITE each edit: insert `new_line` AFTER `anchor_line`. Per file, applies bottom-up so
    earlier anchor line numbers stay valid. Preserves the file's newline. Returns files written."""
    by_file: dict[str, list[InsertEdit]] = defaultdict(list)
    for e in edits:
        by_file[e.file].append(e)
    written: list[str] = []
    for rel, es in by_file.items():
        path = os.path.join(repo_root, rel)
        try:
            with open(path) as f:
                lines = f.readlines()
        except OSError:
            continue
        for e in sorted(es, key=lambda x: -x.anchor_line):  # bottom-up: indices don't shift
            if not (1 <= e.anchor_line <= len(lines)):
                continue
            nl = e.new_line if e.new_line.endswith("\n") else e.new_line + "\n"
            lines.insert(e.anchor_line, nl)  # 0-based index == 1-based anchor_line -> AFTER anchor
        with open(path, "w") as f:
            f.writelines(lines)
        written.append(rel)
    return written
