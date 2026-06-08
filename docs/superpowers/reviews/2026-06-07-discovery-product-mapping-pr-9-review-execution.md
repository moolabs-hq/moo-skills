# Adversarial PR Review — discovery product-mapping (PR #9)
Date: 2026-06-07
Operator: claude-opus-4-8

## PRs in scope
| PR | Branch | Base | Head SHA | Status |
|---|---|---|---|---|
| #9 | fix/cost-billing-discovery-product-mapping | main | 6d30134 | in-progress |

## Cross-PR dependencies
Composes with merged PR #8 (instrument _effective_product_slug value-search). #9 makes discovery emit real product buckets; #8 resolves against them. Independent merge.

## Codebase profile
- Python 3.10+ stdlib unittest; hand-rolled YAML emit; PyYAML soft dep (_read_yaml_safe returns {} if absent).
- Test runner: `bash skills/cost-billing/scripts/test-suite.sh` (92 baseline). Scoped: `python3 .../discovery/scripts/test_slug_inventory.py`.
- No CI (verified: gh pr checks 9 → no checks; .github/workflows absent).
- cost/usage inventories AGENT-authored (no script emits them); slug_inventory.py is the deterministic consumer. per-feature-spec.yaml = authoritative product↔event map (root + per-product subdirs).
- Real dogfood artifacts at ../moolabs/.moolabs/.

## PR #9 — discovery product_slug derivation + consolidation detection

### Original intention
`derive_per_product_constants` bucketed by `entry.get("product_slug") or "default"`. Agent-authored entries omit product_slug → all products collapse to one "default" bucket → instrument resolution worked only by single-bucket accident.

### New intention
Derive product_slug per entry from the authoritative per-feature-spec map (exact event_type → namespace-prefix → first-dotted-segment → "default"; declared wins). Add deterministic consolidation double-count detection (#4) — invert output-input-map, WARN on sibling-pair cost feeding ≥2 usage outputs.

### Success criteria
1. cost/usage entries WITHOUT product_slug bucket under their REAL product (arc/acute/meter/bff), not "default".
2. Derivation order: declared product_slug > exact event_type match > namespace-prefix (longest-first) > first-dotted-segment > "default".
3. Multi-product: per-feature-spec root + subdir specs loaded; PyYAML-absent degrades to {}.
4. #4: sibling-pair cost feeding ≥2 usage outputs is flagged; cost-only/usage-only NOT flagged; single-output sibling-pair NOT flagged.
5. Backward compat: 4-arg derive_per_product_constants callers unaffected (product_map defaults None).
6. Smoke 92/92; existing slug_inventory tests unaffected.

### Codebase-specific challenges
1. Derivation precedence: exact must beat prefix; longest-prefix-first; first-segment only when no spec; multiple-prefix-match resolution.
2. The 'bff' bucket: first-segment fallback creates a 'bff' product for bff.* events not in any spec. Correct (real namespace, deterministic) or mis-bucketing?
3. #4 inversion: counts DISTINCT output_workflow_ids; malformed omap (no edges / missing inputs / cost entry no workflow_id) doesn't crash.
4. Glob double-count: _load_per_feature_specs root glob + subdir glob — does the root spec get loaded twice?
5. Back-compat: any existing test passing a DOTTED event without product_slug now buckets by first-segment instead of "default" → would break the test's assertion.

### Phase 1f self-review
- Round 1: added challenge 4 (glob double-count) + challenge 5 (existing-test back-compat regression). Sharpened criterion 2 with full precedence order.
- Round 2: no further edits. Operator pre-verified back-compat (smoke 25/25) + bff-bucket correctness (bff.* are real moo-arc cost events).

### Risk map
- `_product_for_event` precedence — MEDIUM (derivation correctness).
- `_load_per_feature_specs` glob — LOW-MEDIUM (double-count root spec?).
- `check_consolidation_double_count` inversion — MEDIUM (#4 detection correctness, malformed-omap robustness).
- `derive_per_product_constants` back-compat — LOW (smoke confirmed).

### Verification commands
- `python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py`
- `bash skills/cost-billing/scripts/test-suite.sh`
- real moolabs: load specs + inventories, assert buckets + consolidation flag.

### CI status
No checks configured (verified).

### Round 1 — operator pre-findings
- VERIFIED clean: back-compat (smoke 25/25 + the "default" test is the empty-inventory provider path); bff bucket correct (bff.* are genuine moo-arc cost events → real namespace bucketing beats "default").
- Operator spot-check: ran derive against real moolabs → buckets [arc, bff, meter, acute] (was [default]); consolidation flagged arc.shared.llmport-call.

### Round 1 — findings + fix
- 0 CRIT, 1 IMPORTANT (test docstring/name contradiction — `test_backward_compat_no_product_map` claimed "→ default" but asserts first-segment "x"), 1 LOW (latent garbage-bucket on future v1.*/UUID events — accepted residue).
- CI: none. Streak: 0 (IMPORTANT).
- Operator spot-check: read the test, confirmed docstring/assertion contradiction; pre-verified bff bucket correctness + back-compat smoke.
- Fix 8cbb5ff: renamed test to `test_no_product_map_dotted_event_uses_first_segment` + accurate docstring + assertNotIn("default") + added `test_no_product_map_single_token_event_uses_default`.

### Round 2 — verify-fix
- 0 CRIT/IMP; production code byte-identical to round 1 (test-only diff). Round-1 IMPORTANT verified fixed. Only the accepted LOW residue.
- CI: none. Streak: 1.
- Operator spot-check: verified `_product_for_event` precedence + single-token → "" path.

### Round 3 — exit gate + NIT hardening
- 0 CRIT/HIGH/MED/LOW, 2 NITs (wrong-type YAML crash; longest-prefix untested). Streak: **2 — EXIT GATE SATISFIED**.
- Operator spot-check: traced full main() ordering + graceful-degradation (missing customer-context-dir → empty specs → fallback).
- Closed both NITs (428dcf9) despite gate already met — they harden the agent-authored-YAML input class the PR targets: isinstance guards in `_build_product_map_from_specs` + `check_consolidation_double_count` (malformed shapes skipped, not fatal) + `test_longest_prefix_wins_over_shorter` + 2 malformed-input tests.

### PR #9 head: 428dcf9 — READY (merge authorized this turn)

#### Bugs fixed (chronological)
| Commit | Severity | Description |
|---|---|---|
| 8cbb5ff | IMPORTANT | back-compat test docstring/name corrected to state the real (intentional) first-segment-fallback contract + single-token→default companion test |
| 428dcf9 | NIT×2 | isinstance guards for malformed agent-authored YAML (specs/edges/inputs/entries) + longest-prefix precedence test |

#### Findings accepted as residue
- LOW: first-segment fallback could bucket a future garbage-prefixed event (v1.*, UUID) under a junk product. Clean in real data; a speculative "looks-like-a-product" guard risks breaking legitimate namespaces (bff, meter). Deferred.

#### Success criteria (final)
1. ✅ entries without product_slug bucket by REAL product (verified real moolabs: [arc, bff, meter, acute], no "default").
2. ✅ derivation precedence: declared > exact > longest-prefix > first-segment > "default" (all tested).
3. ✅ multi-product specs (root + subdir) loaded; PyYAML-absent → {}; malformed shapes tolerated.
4. ✅ #4 consolidation detector flags sibling-pair feeding ≥2 outputs (real moolabs: arc.shared.llmport-call); cost-only/usage-only/single-output not flagged; malformed omap tolerated.
5. ✅ backward compat (4-arg callers unaffected; behavior change documented + tested).
6. ✅ smoke 92/92; slug_inventory tests 17 → 29.

#### Status: ready → MERGING (user authorized full arc: fix → adversarial-pr-review → merge)

## Final summary
**PR #9 — ready → merging** (3 rounds, 2 fix commits, head 428dcf9).
Fix commits: 8cbb5ff (IMPORTANT test contract), 428dcf9 (NIT hardening).
slug_inventory tests 17→29; smoke 92/92; CI none (verified).
Merge: AUTHORIZED this turn + exit gate satisfied.
