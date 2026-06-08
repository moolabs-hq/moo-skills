# Adversarial PR Review — v0.3 dogfood rough-edges (PR #8)
Date: 2026-06-07
Operator: claude-opus-4-8

## PRs in scope
| PR | Branch | Base | Head SHA | Status |
|---|---|---|---|---|
| #8 | fix/cost-billing-v03-rough-edges | main | 666bb49 | in-progress |

## Cross-PR dependencies
None. PR #8 bundles 7 commits (6 from a parallel "other tab" session + 1 this session). Branches off main (post Phase A-D + PR #7 merges).

## Codebase profile (Phase 1.5)
- Python 3.10+ stdlib unittest; Jinja2 templates; hand-rolled YAML emit (PyYAML soft dep + `_naive_yaml` fallback in task_planner).
- Test runner: `bash skills/cost-billing/scripts/test-suite.sh` (92-check baseline). Scoped: `python3 .../test_task_planner.py` / `test_env_loader_scan.py` / `test_config_wire.py`.
- **No CI configured** (verified: `gh pr checks 8` → no checks; `.github/workflows/` absent).
- Real dogfood artifacts at `../moolabs/.moolabs/` for ground-truth.
- Documented anti-pattern (Phase A): hand-rolled YAML escape backslash THEN quote; unquoted ISO-8601 generated_at coerces to datetime on safe_load.

## PR #8 — v0.3 dogfood rough-edges

- 7 commits: 42d51a8, 978dbca, b710bb8, e6bae2e, be38a66, f70ce55 (other tab) + 666bb49 (this session).
- Files: discovery/SKILL.md, env_loader_scan.py, 3 helper templates (py/ts/go), task_planner.py, test_task_planner.py.

### Original intention
Raw dogfood run of /cost-billing-instrument against real moolabs surfaced 6 rough edges (#1-#6) requiring hand-correction every run. The skill should emit correct artifacts without hand-correction.

### New intention
Fix the skill-side defects: task_planner datetime quoting (#3), slug resolution robustness (#5), helper templates fold in #528/#531 review fixes (lazy SDK import + secret scrub), env_loader_scan test-file skip + terraform over-detection narrowing (#1/#2), discovery guidance for consolidation pattern (#4 — prose only).

### Success criteria
1. EVERY `generated_at` emit across all 3 emitters round-trips as a string (no datetime coercion). [#3 — the whole point]
2. Slug resolution resolves for single-collapsed-bucket AND multi-product (declared + value-search) without depending on the single-bucket accident. [#5]
3. All 3 helper templates (py/ts/go) import the SDK lazily so the module imports without the SDK installed; `_scrub_secrets` applied on BOTH log and raise paths.
4. env_loader_scan test-file skip excludes test files but NOT real prod config; `.terraform`/`.terragrunt-cache` skip preserved after the e6bae2e revert.
5. Backward compat across all 3 YAML emitters (scope/source_path/infra_discovery_gap/product_slug all `.get(default)`-guarded).
6. f70ce55 accurately scoped as guidance-only (emitter still writes pattern: sibling-pair — KNOWN open, out-of-PR).
7. Test suite 92/92 smoke + per-script unit tests green.

### Codebase-specific challenges
1. #3 sibling completeness: 42d51a8 + 666bb49 fixed the task_planner emitters, but the dogfood handoff named slug-inventory as THE fix location. Are slug_inventory.py / env_loader_scan.py generated_at emits also quoted?
2. #5 ambiguity: value present in 2 products → does _product_owning_value WARN + first-wins, not crash/silently-pick-wrong?
3. #5 import-path coherence: does build_tasks use the EFFECTIVE product (not the declared one) for BOTH resolve_slug_constants AND _slugs_import_path_for?
4. helper templates: does the TS dynamic import shadow the type-only import correctly (no double-import / no runtime ReferenceError)? Go top-level import — is that correct (Go has no lazy-import; compile-time dep)?
5. env_loader_scan test-skip: a real config file under a path containing "test" (e.g. `src/contest/config.py`) — does the skip heuristic false-positive and exclude it?

### Phase 1f self-review
- Round 1: added criterion 1 emphasis (EVERY emitter) + challenge 1 (slug-inventory sibling) after the operator sibling-search found 2 unquoted emits. Added challenge 5 (test-skip false-positive on "test"-containing prod paths).
- Round 2: no further edits. Operator already has 1 confirmed finding (challenge 1) → flag extra Pass-1 scrutiny.

### Risk map
- **slug_inventory.py:188 + env_loader_scan.py:813 generated_at**: MEDIUM — unquoted (operator-confirmed); #3 incomplete.
- **task_planner _effective_product_slug / _product_owning_value**: MEDIUM — new resolution logic; ambiguity + import-path coherence.
- **helper templates (py/ts/go)**: MEDIUM — lazy import correctness across 3 languages.
- **env_loader_scan test-skip heuristic**: LOW-MEDIUM — false-positive risk on prod paths containing "test".

### Verification commands
- `python3 skills/cost-billing/instrument/scripts/test_task_planner.py`
- `python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py`
- `bash skills/cost-billing/scripts/test-suite.sh`
- real moolabs: load `.moolabs/customer-context/slug-inventory.yaml`, check generated_at type after safe_load.

### CI status
No checks configured (verified).

### Round 1 — operator pre-findings
- **CONFIRMED (operator, Pass 1 challenge 1):** #3 incomplete — `slug_inventory.py:188` and `env_loader_scan.py:813` emit `generated_at:` UNQUOTED. Severity: MINOR→IMPORTANT (the PR's stated #3 mandate is "quote generated_at in slug-inventory" per the dogfood handoff; leaving it unquoted means a consumer doing date-string ops on the discovery output gets a datetime).
- **VERIFIED clean:** `.terraform`/`.terragrunt-cache` skip present post-revert (env_loader_scan.py:431); Python helper lazy import (TYPE_CHECKING:64-69 + lazy:113).

### Round 1 — fixes
- Reviewer found 4 IMPORTANT (#3 generated_at siblings: slug_inventory:188, env_loader_scan:813, sdk_snapshot:621, attribution_discovery:347) + 3 MINOR (1.5 TS race, 2.1 missing WARN test, 2.3 TS stub).
- Operator severity: 1.1 IMPORTANT (active consumer corrupts tasks.yaml), 1.2/1.3/1.4 MINOR (no active consumer) but same class → fixed all to close #3.
- REJECTED 2.3 (TS stub `?? ''` = intended warn-and-drop). ACCEPTED-RESIDUE 1.5 (benign idempotent-client race).
- CI: none configured. Streak: 0 (IMPORTANT present).
- Operator spot-check: verified both discovery emitters round-trip `str` post-fix.
- Fixes: 17c9117 (4 emitters quoted + 2 round-trip guards + WARN-path test).

### Round 2 — verify-fix + operator sibling catch
- Operator broad sibling-search BEFORE re-review caught a 5TH instance the round-1 reviewer MISSED: attribution_discovery:359+367 `confirmed_at` unquoted (timestamp). Fixed at a00d7bf.
- Reviewer round 2: 0 CRIT/IMP + 1 LOW — the round-1 quote-fix introduced `confirmed_at: "None"` when value is None (broke old self-healing null). Reachable via naive-loader + pre-existing empty field.
- CI: none. Streak: 1 (LOW-only).
- Operator spot-check: verified None-guard emulation (None/''→`""`, ts→quoted) + zero-bare-emitter sweep.
- Fix: 2c34bdd (None-guard `{b.confirmed_at or ""}` both lines).

### Round 3 — exit gate
- Reviewer: ZERO findings. Verified: round-2 fix correct (all 3 cases); no Optional-in-quotes siblings anywhere in suite; #3 class fully closed (9 emitters all quoted); _effective_product_slug robust.
- CI: none. Streak: **2 — EXIT GATE SATISFIED**.
- Operator spot-check: confirmed None-guard 3-case behavior + final bare-emitter sweep = zero.

### PR #8 head: 2c34bdd — READY (merge authorized this turn)

#### Bugs fixed (chronological)
| Commit | Severity | Description |
|---|---|---|
| 17c9117 | IMPORTANT | #3 incomplete — quoted generated_at in 4 emitters (slug_inventory/env_loader_scan/sdk_snapshot/attribution_discovery) the prior fix missed; +2 round-trip guards + WARN-path test |
| a00d7bf | MINOR | #3 5th sibling — confirmed_at emits (attribution_discovery:359+367) quoted (operator sibling-catch) |
| 2c34bdd | LOW | None-guard confirmed_at quote — prevent `"None"` literal under naive-loader (regression from 17c9117) |

#### Findings rejected / accepted
- REJECTED 2.3: TS settings-stub `?? ''` is the intended warn-and-drop, not a bug.
- ACCEPTED-RESIDUE 1.5: benign TS getClient init race (idempotent client).
- ACCEPTED-RESIDUE (PR scope): #5 upstream root (discovery omits product_slug per cost/usage entry) + #4 emitter enforcement (f70ce55 is guidance-only) — both DISCOVERY-lane follow-ups; this PR makes instrument robust to both states.

#### Success criteria (final)
1. ✅ Every timestamp emitter (9) quotes + None-guards generated_at/confirmed_at.
2. ✅ Slug resolution robust (single-bucket + multi-product via value-search).
3. ✅ All 3 helper templates lazy-import SDK + scrub on both paths (Go top-level is correct for Go).
4. ✅ env_loader test-skip excludes tests not prod config; .terraform/.terragrunt-cache skip intact post-revert.
5. ✅ Backward compat across 3 YAML emitters.
6. ✅ f70ce55 accurately scoped guidance-only.
7. ✅ Smoke 92/92; per-script units green.

#### Status: ready → MERGING (user authorized "run /adversarial-pr-review and merge it")

## Final summary
**PR #8 — ready-for-human → merging** (3 rounds, 3 fix commits, head 2c34bdd).
Fix commits: 17c9117, a00d7bf, 2c34bdd. Smoke 92/92. CI: none configured (verified).
Merge: AUTHORIZED by user this turn ("run /adversarial-pr-review and merge it") + exit gate satisfied.
