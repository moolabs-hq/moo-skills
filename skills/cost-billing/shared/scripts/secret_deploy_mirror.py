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
  - Idempotent PER ANCHOR: skip an anchor only when the mirror is ALREADY the line right
    after it (a prior apply). NOT file-wide — a DIFFERENT task-def block that happens to
    carry the new env var must not suppress wiring THIS block (moo-arc: the UI's
    `ui_secrets` block has MOOLABS_API_KEY, but arc's own task-def still needs it).

Apply (`terraform apply`) and seeding the secret VALUE stay human; this WRITES the wiring
as a reviewable, additive diff (validate with `terraform fmt`/`validate` before apply).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
    hits = secret_exemplar.grep_tokens(repo_root, [anchor_env], timeout=timeout)

    file_lines: dict[str, list[str]] = {}
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
        lines = file_lines[rel]
        if not (1 <= lineno <= len(lines)):
            continue
        key = (rel, lineno)
        if key in seen:
            continue
        seen.add(key)
        anchor_text = lines[lineno - 1].rstrip("\n")
        if anchor_text.lstrip().startswith(("#", "//")):
            continue  # a COMMENT mentioning the env var (e.g. "# ARC accepts …") — not a
            # wiring line; mirroring it would emit a nonsense sibling comment.
        new_line = _apply_swaps(anchor_text, swaps)
        if new_line == anchor_text:
            continue  # the anchor line didn't carry the env var verbatim — skip
        # ANCHOR-LOCAL idempotency: skip ONLY when the mirror is already the line right
        # after THIS anchor (a prior apply inserts it there). A file-wide "new_env appears
        # anywhere" check is WRONG — a DIFFERENT block carrying new_env (moo-arc: the UI's
        # ui_secrets MOOLABS_API_KEY) would suppress wiring the anchor's OWN block.
        nxt = lines[lineno].rstrip("\n") if lineno < len(lines) else ""
        if nxt.strip() == new_line.strip():
            continue
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


# ──────────────────────────────────────────────────────────────────────
# CLI — Phase 1.7 invokes this to PREVIEW (--plan) the additive diff for the
# permission ask, then WRITE it (--apply) only after the engineer confirms.
# (The engine above is the same code the unit tests exercise.)
# ──────────────────────────────────────────────────────────────────────

def _parse_swaps(s: str) -> dict[str, str]:
    """`"OLD1=NEW1,OLD2=NEW2"` -> {OLD1: NEW1, ...}. Skips blanks / malformed pairs."""
    out: dict[str, str] = {}
    for pair in (s or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _edit_dict(e: InsertEdit) -> dict:
    return {"file": e.file, "anchor_line": e.anchor_line,
            "anchor_text": e.anchor_text, "new_line": e.new_line}


def skipped_already_wired(repo_root: str, anchor_env: str, new_env: str,
                          edited_files: set[str],
                          exts: tuple[str, ...] = _DEFAULT_INFRA_EXTS,
                          timeout: int = 120) -> list[dict]:
    """Infra files that had an `anchor_env` hit but produced NO injection edit because
    they ALREADY carry `new_env` (plan_inserts' file-wide idempotency skip). These must
    be SURFACED in the permission ask, not hidden — the existing wiring may be in a
    DIFFERENT block (moo-arc: regional/main.tf already has MOOLABS_API_KEY at one task-def
    but not arc's), so the engineer has to verify, not assume the file is handled."""
    if not new_env:
        return []
    hit_files = {
        rel for rel, _ln, _s in secret_exemplar.grep_tokens(repo_root, [anchor_env], timeout=timeout)
        if rel.lower().endswith(exts)
    }
    out: list[dict] = []
    for rel in sorted(hit_files - edited_files):
        try:
            content = open(os.path.join(repo_root, rel)).read()
        except OSError:
            continue
        if new_env in content:
            existing = [i + 1 for i, ln in enumerate(content.splitlines()) if new_env in ln]
            out.append({"file": rel, "reason": f"already contains {new_env}",
                        "existing_lines": existing})
    return out


def build_plan(repo_root: str, anchor_env: str, swaps: dict[str, str],
               new_store_key: str, description: str):
    """The full mirror plan: Hop-1 injection sibling inserts + the Hop-2 declaration
    entry (None when the new store key's namespace has no existing declaration to anchor,
    or it's already declared) + the skipped-already-wired files to SURFACE in the ask.
    Returns (injection_edits, declaration_edit, skipped)."""
    inj = plan_inserts(repo_root, anchor_env, swaps)
    new_env = swaps.get(anchor_env, "")
    skipped = skipped_already_wired(repo_root, anchor_env, new_env, {e.file for e in inj})
    decl = (
        plan_declaration_insert(repo_root, new_store_key, description,
                                prefer_files=[e.file for e in inj])
        if new_store_key else None
    )
    return inj, decl, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Plan/apply the ADDITIVE terraform secret mirror (anchored on the "
                    "exemplar's unique env var; shared-key secrets untouched)."
    )
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--anchor-env", required=True,
                    help="the exemplar's UNIQUE env var, e.g. ARC_GLOBAL_API_KEY")
    ap.add_argument("--swaps", required=True,
                    help="comma-sep old=new token swaps, e.g. "
                         "'ARC_GLOBAL_API_KEY=MOOLABS_API_KEY,shared/api-key=arc/moolabs-api-key'")
    ap.add_argument("--new-store-key", default="",
                    help="new DEDICATED store key for the Hop-2 declaration, e.g. "
                         "arc/moolabs-api-key (use the service's EXISTING namespace so the "
                         "declaration can anchor; a brand-new ns leaves it a checklist item)")
    ap.add_argument("--description", default="Moolabs SDK API key (usage/billing emission)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true",
                   help="PREVIEW the edits as JSON; writes nothing (for the permission ask)")
    g.add_argument("--apply", action="store_true", help="WRITE the edits")
    args = ap.parse_args(argv)

    swaps = _parse_swaps(args.swaps)
    inj, decl, skipped = build_plan(args.repo_root, args.anchor_env, swaps,
                                    args.new_store_key, args.description)

    if args.plan:
        print(json.dumps({
            "injection_edits": [_edit_dict(e) for e in inj],
            "declaration_edit": (_edit_dict(decl) if decl is not None else None),
            "declaration_status": ("planned" if decl is not None
                                   else "checklist_only (no anchorable namespace / already declared)"),
            "skipped_already_wired": skipped,  # SURFACE in the permission ask — verify, don't assume
        }, indent=2))
        return 0

    written = apply_inserts(args.repo_root, list(inj) + ([decl] if decl is not None else []))
    print(json.dumps({
        "written": written,
        "injection_edits_applied": len(inj),
        "declaration_applied": decl is not None,
        "skipped_already_wired": skipped,  # NOT applied — already carry the env var; verify by hand
        "note": "seed the secret VALUE in your secret store out-of-band; run `terraform fmt`.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
