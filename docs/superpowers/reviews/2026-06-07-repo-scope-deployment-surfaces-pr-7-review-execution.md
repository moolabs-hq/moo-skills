# Adversarial PR Review — repo-scope deployment surfaces (PR #7)
Date: 2026-06-07
Operator: claude-opus-4-8

## PR in scope
| PR  | Branch                                          | Base | Head SHA  | Status        |
|-----|-------------------------------------------------|------|-----------|---------------|
| #7  | spec/cost-billing-repo-scope-deployment-surfaces | main | b85782d   | in-progress   |

## Cross-PR dependencies
None. PR #7 branches off main AFTER PRs #4/#5/#6 merged (it depends on the merged Phase B/C/D code — config_wire.py, env_loader_scan.py, task_planner.py all carry the prior phases). It's a standalone follow-up fix to the env-routing discovery layer.

## Codebase profile (Phase 1.5)
- **Languages/frameworks:** Python 3.10+ stdlib unittest; Jinja2 templates; hand-rolled YAML emit (PyYAML soft dep with `try/except ImportError` fallback to `_naive_yaml`).
- **Test runner:** `bash skills/cost-billing/scripts/test-suite.sh` (8 phases; Phase 7 = Jinja render + scanner assertions; Phase 8 auto-discovers `test_*.py`). Scoped: `python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py` / `.../instrument/scripts/test_config_wire.py` / `.../instrument/scripts/test_task_planner.py`.
- **No CI configured** (verified: `.github/workflows/` absent; `gh pr checks 7` → "no checks reported"). The skill's no-CI edge case applies — operator validates locally.
- **Real-world verification target:** `../moolabs` has the actual `infrastructure/terraform/` tree (28 variables.tf files) — the regression that motivated this PR. Use it for ground-truth scanner checks.
- **Conventions docs:** repo CLAUDE.md; `skills/cost-billing/SKILL.md`. Documented anti-pattern (Phase A): hand-rolled YAML must escape backslash THEN quote.

## Risk categories that apply
- **rglob performance / traversal scope** — `scan_repo_level_deployment_surfaces` walks repo-root infra dirs via rglob. Over a large monorepo's `infrastructure/` this could be slow or pull in vendored .tf files.
- **Path-anchor correctness** — repo-scope surfaces must emit repo-relative paths; the `path_anchor` + `relative_to` logic has a fallback branch for paths not under the anchor.
- **Scope-downgrade safety** — repo-scope MUST never auto-emit a file (centralized-infra blast radius). A miss here is the most dangerous outcome (silently committing a change to shared infra).
- **YAML round-trip of new fields** — scope / source_path / infra_discovery_gap across 3 emitters.
- **Backward compat** — pre-fix inventories/plans (no scope field, no gap field) must still work.
- **Gap-detection false-negatives** — treating `.env.example`-only as "has infra" would mask the gap and suppress the developer ask. The heuristic uses `infra_kinds = {terraform, k8s, dockerfile}`.

## Per-PR detail

### PR #7 — repo-scope deployment surfaces + ask-developer fallback

- Branch: `spec/cost-billing-repo-scope-deployment-surfaces`
- Base: main (c76b6ff — post Phase B/C/D merge)
- Head SHA at start: b85782d
- 4 commits, ~13 files.

#### Summary of changed areas
- `discovery/scripts/env_loader_scan.py`: `_REPO_LEVEL_INFRA_DIRS`, `scope` field on `DeploymentSurface`, `scan_deployment_surfaces(scope, path_anchor)` params, `scan_repo_level_deployment_surfaces()`, `_service_entry` combines both scopes + `infra_discovery_gap`, YAML emit includes scope + gap.
- `instrument/scripts/config_wire.py`: `_plan_deployment_stubs` downgrades repo-scope to `checklist_only`; `plan_service_env_wire` carries `infra_discovery_gap`; YAML emit includes scope + gap.
- `instrument/scripts/task_planner.py`: `EnvWireTask.infra_discovery_gap`; `build_env_wire_tasks` reads it; `emit_tasks_yaml` writes gap + per-stub source_path + scope.
- `examples/customer-fixture-centralized-infra/`: moolabs-shape fixture (services/moo-arc + repo-root infrastructure/terraform/).
- `skills/adversarial-pr-review/SKILL.md`: 4th Pass 2 lens (deployment-surface coverage gap).
- `scripts/test-suite.sh`: centralized-infra fixture fence.
- 3 test files extended (+14 tests total).

#### Original intention (Phase 1c)
`scan_deployment_surfaces(repo_root)` walked a single path (the service path, passed by `_service_entry` as `repo_root / service["root"]`). The comment said "scoped to the SERVICE's path (not the whole repo)" — a deliberate design choice. Contract: each service's `deployment_surfaces` lists only files under `services/<svc>/`. For monorepos with centralized infra (no per-service infra), this returns no Terraform/k8s — exactly the bug behind moolabs#531.

#### New intention
The scanner now walks BOTH the service path (scope=service) AND repo-root infra dirs (scope=repo). `_service_entry` aggregates both. When no infra (terraform/k8s/dockerfile) is found at either scope, `infra_discovery_gap=True` is set. The instrument layer (config_wire) downgrades repo-scope surfaces to `checklist_only` so centralized infra is never auto-modified. task_planner carries gap + scope into tasks.yaml for the execution agent. Contract: every service entry sees centralized infra; centralized infra is CHECKLIST-only; a no-infra repo flags the gap for a developer ask.

#### Success criteria
1. Against the real `../moolabs` repo, `_service_entry(moo-arc)` returns ≥1 terraform surface with scope=repo (the regression: previously 0).
2. Every repo-scope surface in the config-wiring plan has `mode: checklist_only` (NEVER `new_file` or `append`) — centralized infra is never auto-modified.
3. Service-scope surfaces preserve the pre-fix auto-emit behavior (terraform → new_file moolabs.tf; .env.example → append).
4. `infra_discovery_gap=True` iff no terraform/k8s/dockerfile found at either scope; `.env.example`/`docker-compose` alone do NOT clear the gap.
5. scope / source_path / infra_discovery_gap round-trip cleanly through all 3 YAML emitters (loadable by PyYAML, correct values).
6. Backward compat: a deployment_surface dict WITHOUT a `scope` key defaults to service-scope (auto-emit preserved); a service entry WITHOUT `infra_discovery_gap` defaults to False.
7. `scan_repo_level_deployment_surfaces` doesn't crash on missing infra dirs, doesn't pull in `.git`/`node_modules`/`vendor`, and emits repo-relative (not anchor-relative) paths.

#### Codebase-specific challenges
1. **path_anchor relative_to fallback** (env_loader_scan): `scan_deployment_surfaces` computes `rel = path.relative_to(anchor)`. When `path_anchor=repo_root` but the walked dir is `repo_root/infrastructure`, every file IS under repo_root so relative_to succeeds. But the `except ValueError` fallback emits an ABSOLUTE path — could that absolute path leak into the inventory and then into a customer-committed YAML (machine-specific path)? When is the fallback reachable?
2. **rglob symlink loop / huge tree** (env_loader_scan): `_REPO_LEVEL_INFRA_DIRS` includes broad names (`ops`, `deploy`, `charts`). `candidate.rglob("*")` over a large `infrastructure/` with vendored Terraform modules (`.terraform/` provider mirrors can be GB-scale) could be slow or hit symlink cycles. Is `.terraform` skipped? `_SURFACE_SKIP_DIRS` = {.git, node_modules, __pycache__, vendor} — does NOT include `.terraform`.
3. **Double-scan when service lives under an infra dir name** (env_loader_scan): if a service root happens to be under one of `_REPO_LEVEL_INFRA_DIRS` (e.g. a repo with `deploy/myservice/`), would `_service_entry` scan the same file twice — once service-scope, once repo-scope — producing duplicate surfaces with conflicting scope tags?
4. **Multi-service gap aggregation** (env_loader_scan): `scan_repo_level_deployment_surfaces(repo_root)` is called per-service inside `_service_entry`. In a 10-service monorepo, the repo-root infra tree is rglob'd 10 times (once per service) AND every service gets the SAME 28 repo-scope terraform surfaces attached. Is that the intended data shape, or does it bloat each service entry + waste 10× the scan time?
5. **checklist_only downgrade completeness** (config_wire): the `if scope == "repo": ... continue` branch downgrades. But does it preserve enough info (source_path) for the execution agent to name the file? And does the gap flag correctly propagate when deployment_surfaces is non-empty but ALL surfaces are repo-scope (so service-scope auto-emit list is empty)?

#### Phase 1f self-review — round 1
- Intentions: confirmed against the diff — new intention matches (both-scope walk + gap + checklist downgrade). No edits.
- Success criteria: added criterion 7 (rglob safety + repo-relative paths) — round 1 had only the behavioral criteria, missed the traversal-safety surface. Added the "`.env.example` alone doesn't clear gap" clause to criterion 4 explicitly.
- Challenges: added challenge 4 (multi-service 10× rescan + per-service duplication of 28 surfaces) — this is the highest-value codebase-specific concern and round-1 draft didn't have it. Sharpened challenge 2 with the `.terraform` provider-mirror detail.

#### Phase 1f self-review — round 2
- Intentions: no edits.
- Success criteria: no edits.
- Challenges: added challenge 3 (service-under-infra-dir double-scan) — discovered while re-reading challenge 4's traversal logic. Round 2 found 1 new challenge → flag extra scrutiny on Pass 1 for the reviewer.
- Suspicions deferred to Phase 2: (a) is `infra_discovery_gap` computed BEFORE or AFTER repo-scope surfaces are added? If after, a repo with ONLY repo-scope terraform correctly clears the gap; if the gap were computed on service-scope only it'd be a false-positive ask. (b) Does the per-service 10× rescan matter for the moolabs real-world case (it has ~10 services)?

#### Risk map by subsystem
- **env_loader_scan.scan_repo_level_deployment_surfaces + _service_entry**: HIGH — new traversal; rglob safety, path-anchor, per-service duplication, gap computation all live here.
- **config_wire._plan_deployment_stubs repo-scope branch**: HIGH — the checklist_only downgrade is the safety-critical path (auto-modifying centralized infra is the worst outcome).
- **3 YAML emitters (scope/source_path/gap)**: MEDIUM — round-trip correctness; backward compat.
- **task_planner.emit_tasks_yaml**: MEDIUM — gap + per-stub source_path/scope passthrough.
- **test-suite.sh fixture fence**: LOW — additive smoke check.
- **adversarial-pr-review SKILL.md lens**: LOW — documentation.

#### Verification commands
- Scoped: `python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py 2>&1 | tail -3`
- Scoped: `python3 skills/cost-billing/instrument/scripts/test_config_wire.py 2>&1 | tail -3`
- Scoped: `python3 skills/cost-billing/instrument/scripts/test_task_planner.py 2>&1 | tail -3`
- Full smoke: `bash skills/cost-billing/scripts/test-suite.sh 2>&1 | tail -3`
- Real-world: `_service_entry` against `../moolabs` for moo-arc, count repo-scope terraform surfaces + time the scan.

#### CI status
No checks configured (verified). Recorded per skill's no-CI edge case.

#### Round 1 — findings + fixes
- **Pass 1 findings:** 3 IMPORTANT. (1) `.terraform`/`.terragrunt-cache` vendored module mirrors not in `_SURFACE_SKIP_DIRS` → false-positive repo-scope surfaces + falsely-cleared gap (operator-confirmed via synthetic `.terraform/modules/vendored/variables.tf`). (2) Challenge 3 service-under-infra-dir double-scan → same file emitted as scope=service AND scope=repo with conflicting modes (operator-confirmed: `deploy/myservice/variables.tf` → 2 entries). (3) Challenge 4 reviewer split into 4(a) per-service duplication [accepted residue] + 4(b) gap-flag corollary [REJECTED].
- **Pass 2 findings:** 1 MINOR (Challenge 1 absolute-path fallback — latent, unreachable with current callers) + 5 LOW (test-coverage gaps).
- **CI status:** no checks configured (verified).
- **Severity tally (CONFIRMED, operator-adjusted):** CRIT=0, IMP=2 (4(b) rejected, not counted), MIN=1, LOW=5.
- **Low-only streak:** 0 (IMPORTANT present).
- **Operator spot-check:** personally reproduced finding (1) — rendered a synthetic `.terraform/modules/vendored/variables.tf` and confirmed the scanner detected it as a surface; and finding (2) — `_service_entry` for `deploy/myservice` returned 2 terraform surfaces with conflicting scopes.
- **REJECTED — 4(b)** with primary-source evidence + advisor reconciliation: moolabs moo-arc has ZERO service-scope terraform (only centralized repo-scope). The reviewer's suggested fix (compute `has_infra` from service-scope only) would set gap=True for moo-arc — the exact false-positive ask this PR eliminates. `infra_discovery_gap`'s contract is "found no infra ANYWHERE → ask"; repo-scope infra IS infra found. Advisor confirmed the rejection AND caught a gotcha in the Challenge-3 fix (naive string-dedup would silently no-op because the two scopes use different path representations — fixed by comparing against the service-root prefix).
- **Fixes pushed at f6b5c05:** (1) `.terraform` + `.terragrunt-cache` → `_SURFACE_SKIP_DIRS`. (2) `_service_entry` drops repo-scope surfaces under `service["root"]` (root-prefix compare, not path-string compare). (3) except-ValueError → warn-and-skip. +5 regression tests (TerraformVendorMirrorSkip ×3, ServiceUnderInfraDirNoDoubleScan ×2). env_loader tests 42→47.
- **ACCEPTED RESIDUE — 4(a):** repo-scope surfaces attached per-service (N×28). Perf fine (0.28s/10× on real moolabs). Per-service self-contained-task shape intended; CHECKLIST repetition is noise, not incorrectness. Follow-up: hoist repo-scope scan to inventory level + dedup CHECKLIST.

#### Round 2 — verify-fix
- **Pass 1 result:** all 3 fixes VERIFIED correct + complete. `.terraform` skip is exact path-part match (doesn't collide with the real `infrastructure/terraform/` — different string); real moolabs still 28 surfaces. Challenge-3 dedup: trailing-slash prefix fences `deploy/foo` vs `deploy/foobar`; `service["root"]` == `.`/`""` edge cases don't false-drop; `scan_repo_level` called once per `_service_entry` (not per-iteration). warn-and-skip: `sys` imported, `continue` correct.
- **Pass 2 findings:** zero. Lens-15 re-scan confirmed no NEW coverage gap from over-aggressive skip or over-aggressive dedup.
- **CI status:** no checks configured.
- **Severity tally (CONFIRMED):** all zero. Low-only streak: 1.
- **Operator spot-check:** read the dedup loop (env_loader_scan.py:680-700) — confirmed `scan_repo_level` is in the for-iterator (called once), `service_prefix` has the trailing slash, `s.path == service_root_rel` handles exact-root match.

#### Round 3 — final exit-gate verification
- **Pass 1 result:** whole-file coherence confirmed (scope always set; no surface escapes dedup; no dead code). Sibling skip-search: `.terraform`/`.terragrunt-cache` are the two `.tf`-bearing vendored dirs that matter; cdk.out emits `.template.json` (not detected anyway), Pulumi emits Pulumi.yaml (no `variable{}` block) — skip list complete. dedup+gap interaction traced: post-dedup list retains the service-scope copy, so `has_infra` stays True → no false gap. YAML emit: `scope` via `.get(default)`, no KeyError. All 5 new tests verified non-vacuous (each FAILS if its fix is reverted). Cross-module field-name consistency confirmed (env_loader_scan → config_wire → task_planner).
- **Pass 2 findings:** 1 NIT (test_service_under_deploy_dir_no_duplicate didn't assert gap=False).
- **CI status:** no checks configured.
- **Severity tally (CONFIRMED):** CRIT=0, IMP=0, MIN=0, LOW=0, NIT=1. Low-only streak: **2 — EXIT GATE SATISFIED**.
- **Operator spot-check:** independently traced the dedup+gap interaction in `_service_entry` — confirmed `has_infra` is computed on the post-dedup `surfaces` list which still contains the service-scope copy.
- **Fix pushed at <round-3 SHA>:** added the NIT pin (`assertFalse(entry["infra_discovery_gap"])` to test_service_under_deploy_dir_no_duplicate).

#### PR #7 head: <final SHA> — READY FOR HUMAN

#### Success criteria verification (final)
1. ✅ `_service_entry(moo-arc)` against real moolabs returns 28 terraform surfaces scope=repo (was 0).
2. ✅ Every repo-scope surface → `mode: checklist_only` (verified end-to-end through config_wire).
3. ✅ Service-scope surfaces preserve auto-emit (terraform→new_file, .env.example→append).
4. ✅ `infra_discovery_gap=True` iff no terraform/k8s/dockerfile at either scope; `.env.example`/docker-compose alone don't clear it; `.terraform` vendored copies don't falsely clear it.
5. ✅ scope/source_path/infra_discovery_gap round-trip through all 3 YAML emitters (PyYAML; config_wire's loader is PyYAML-only — no `_naive_yaml` sibling for env-routing-inventory).
6. ✅ Backward compat: missing scope → service default; missing gap → False.
7. ✅ scan_repo_level: missing dirs OK, skips .git/node_modules/vendor/.terraform/.terragrunt-cache, repo-relative paths, warn-and-skip on non-anchor path.

#### Challenge verification (final)
1. ✅ path_anchor relative_to fallback — now warn-and-skip (no absolute-path leak).
2. ✅ rglob performance — 0.07s/scan, 0.28s/10× on real moolabs; `.terraform` mirrors skipped.
3. ✅ service-under-infra-dir double-scan — fixed via root-prefix dedup.
4. ✅ multi-service duplication — accepted residue (4a); gap-flag rejection (4b) confirmed correct.
5. ✅ checklist_only downgrade — preserves source_path; gap propagates when all surfaces are repo-scope.

#### Bugs fixed (chronological)
| Commit | Severity | Description |
|---|---|---|
| f6b5c05 | IMPORTANT | `.terraform` + `.terragrunt-cache` vendored mirrors skipped (false-positive surfaces + false gap-clear) |
| f6b5c05 | IMPORTANT | service-under-infra-dir double-scan dedup (root-prefix compare) |
| f6b5c05 | MINOR | absolute-path fallback → warn-and-skip |
| f6b5c05 | TESTS | +5 regression pins (vendor-mirror skip ×3, double-scan dedup ×2) |
| <r3 SHA> | NIT | gap=False assertion added to double-scan dedup test |

#### Remaining risks (accepted non-blocking)
- **4(a) per-service surface duplication**: N services × 28 repo-scope surfaces in the inventory; CHECKLIST repeated per service. Perf fine; correctness fine. Follow-up: hoist repo-scope scan to inventory level + dedup CHECKLIST output.
- **Challenge 1 fallback**: now warn-and-skip; still unreachable with current callers. Documented.

#### Status: ready-for-human

## Final summary
**PR #7 — ready-for-human** (3 rounds, 4 fix commits, head <final SHA>)

Fix commits pushed:
- `f6b5c05` — 2 IMPORTANT (.terraform skip, double-scan dedup) + 1 MINOR (warn-and-skip) + 5 regression tests
- `<r3 SHA>` — NIT (gap assertion in dedup test)

Verification: `python3 .../test_env_loader_scan.py` → 47/47 OK; `bash .../test-suite.sh` → 92/92 PASS. Real-moolabs scan: 28 terraform surfaces for moo-arc (was 0).

**Merge status: NOT MERGED.** Awaiting explicit "merge it" instruction.

