# Adversarial PR Review — config detection + render driver (PR #10)
Date: 2026-06-08
Operator: claude-opus-4-8

## PRs in scope
| PR | Branch | Base | Head SHA | Status |
|---|---|---|---|---|
| #10 | fix/cost-billing-config-detection-and-render-driver | main | f8ffef6 | in-progress |

## Codebase profile
- Python 3.10+ stdlib unittest; Jinja2 + PyYAML soft deps (try/except ImportError).
- Test: `bash skills/cost-billing/scripts/test-suite.sh` (95 baseline; Phase 8 auto-discovers test_*.py). Scoped per-script test_*.py.
- No CI (verified: gh pr checks 10 → no checks; .github/workflows absent).
- Real repo at ../moolabs/, dogfood artifacts at ../moolabs/.moolabs/.
- Maintainer guidance: "don't model on moo-arc; env_file is local-only, not the env loader." → general fixes only.

## PR #10 — two commits

### Original intention
Discovery config detection only matched DIRECT `class X(BaseSettings)`; a project-base subclass was invisible → detection fell to a test, then a smoke script. Artifact emission (stub/slugs/deployment) was hand-authored by the agent (template-bypass).

### New intention
(a) Detect a Settings class via transitive base resolution to BaseSettings (general, cross-file). (b) Route subclass → stub (no get_settings assumption). (c) Deterministic render driver emits all artifacts from the shipped templates, honoring deployment modes.

### Success criteria (1-9 — see reviewer brief)
### Codebase-specific challenges (1-7 — see reviewer brief; lean on render driver + resolver)

### Phase 1f self-review
- Round 1: sharpened challenge 1 (new_file CLOBBER of a pre-existing customer file, not just generated overwrite) + challenge 7 (language misroute when all-modify TS/Go).
- Round 2: no further edits.

### Risk map
- render_artifacts.py new_file: MEDIUM — unconditional write_text clobbers any pre-existing file at a deployment emit_path.
- render_artifacts.py infer_language: LOW-MEDIUM — defaults python when no stub_emit_path; misroutes slugs template for an all-modify TS/Go repo.
- env_loader_scan _resolve_module_files src-layout fallback: MEDIUM — suffix-match first-wins could pick a wrong same-named module across roots.
- _PY_INDEX_CACHE staleness: LOW — module-global, keyed by root; tmp-dir uniqueness in tests.

### CI status
No checks configured (verified).

### Round 1 — operator pre-findings (before reviewer)
- **Challenge 1 (new_file clobber) — CONFIRMED concern:** `render_artifacts.py` new_file branch does `dest_path.write_text(rendered)` unconditionally. Intended for stub/slugs (generated DO-NOT-EDIT), but a deployment new_file (terraform `moolabs.tf`) would overwrite a pre-existing customer file at that path. Low-probability (moolabs-specific name) but unguarded. Likely fix: for deployment new_file, skip-with-warning if dest exists and lacks our generated marker.
- **Challenge 7 (language inference) — edge:** `infer_language` defaults python when no env_wire_task carries a stub_emit_path (all-modify repo) → slugs render with python template for a TS/Go repo.

### Round 1 — fixes (commits 7601511 + 89f7b41)
- Operator pre-findings (7601511): new_file customer-file clobber guard (`/cost-billing-instrument` marker gate); infer_language prefers per-file insert tasks' `language` (fixes all-modify TS/Go misroute).
- Reviewer: 2 IMP + 2 MINOR + 2 NIT. IMP-1 stub templates' marker was inside a stripped Jinja `{# #}` comment → re-run refused to regenerate; added marker to rendered headers (py/ts/go). IMP-2 `_parse_from_imports` missed parenthesized multi-line imports → `_PAREN_IMPORT_RE` collapse. MINOR-1 sentinel comment, MINOR-2 `_py_file_index` sort determinism, + defensive `_APPEND_SAFE_KINDS` guard. Streak: 0.
- Operator spot-check: verified IMP-1 (grep + real render) + IMP-2 (`_parse_from_imports` returns `{}` pre-fix).

### Round 2 — verify-fix (commit 6b9740b)
- 0 CRIT/IMP/MINOR + 2 NIT (both from round-1 fixes): NIT-1 the reworded sentinel comment over-claimed k8s is append-capable; NIT-2 dotenv template lacked the marker. Both fixed. Streak: 1.
- Operator spot-check: dotenv append idempotency holds with the new marker (sentinel MOOLABS_API_KEY unaffected; `test_append_is_idempotent` green).

### Round 3 — exit gate
- ZERO confirmed findings. Whole-PR coherence verified: new_file-guard + append-idempotency mutually exclusive (no corrupt/duplicate path); `_class_reaches_basesettings` cannot infinite-loop (visited-set + `_MAX_BASE_DEPTH`); `_py_file_index` bounded+cached+pruned; NO moo-arc-specific modeling in logic (env_file/CommonSettings/python_common only in comments/tests). Streak: **2 — EXIT GATE SATISFIED**.
- Operator spot-check: confirmed the dotenv marker comment has no `MOOLABS_API_KEY`.
- Tests: smoke 95/95; render_artifacts 22, env_loader_scan 57 (79 focused, zero skips/failures).

### Bugs fixed (chronological)
| Commit | Severity | Description |
|---|---|---|
| 7601511 | IMPORTANT | render new_file no longer clobbers customer files; infer_language reliable (per-file `language`) |
| 89f7b41 | IMPORTANT×2 | stub-template markers (regeneration); multi-line paren imports; +MINOR comment/determinism +append-safe guard |
| 6b9740b | NIT×2 | corrected sentinel comment; dotenv marker for defensive consistency |

### Success criteria (final) — all PASS
1✅ transitive detection (same-file/abs/rel/src-layout/aliased/multi-line-paren). 2✅ data-model/unresolvable/cycle safe. 3✅ direct BaseSettings → precise v1/v2. 4✅ subclass → stub. 5✅ skill-artifact skip. 6✅ render mode fidelity (new_file no-clobber, append idempotent, checklist writes nothing). 7✅ perf 0.73s (pruned index). 8✅ smoke 95/95, catalog 10. 9✅ no moo-arc modeling.

### Status: ready → AWAITING USER MERGE RE-CONFIRM (approach changed mid-stream; re-confirm per contract)

## Final summary
**PR #10 — ready-for-human** (3 rounds, head 6b9740b). Fix commits: 7601511, 89f7b41, 6b9740b. Smoke 95/95. CI: none configured (verified). The env_file→transitive-base approach change (maintainer correction) was fully incorporated. **Merge status: NOT MERGED — re-confirming with user (the approach changed substantially mid-stream).**
