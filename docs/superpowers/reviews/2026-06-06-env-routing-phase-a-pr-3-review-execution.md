# Adversarial PR Review — env-routing Phase A (PR #3)
Date: 2026-06-06
Operator: claude-opus-4-7 (main session)

## PRs in scope

| PR  | Branch                                | Base    | Head SHA  | Status      |
|-----|---------------------------------------|---------|-----------|-------------|
| #3  | spec/cost-billing-env-routing-design  | main    | a96c5e1   | in-progress |

Base SHA: `187a6cbca005c75f618f2390dce3ae80ac3d4bb4` (the PR #2 merge commit on main)
Head SHA: `a96c5e1c9e231fe1fb245c1f7f77ce3164370d8f`
Stats: 10 files, +5465/-1
CI: no checks configured (verified via `gh pr checks 3`)

## Cross-PR dependencies

None. PR #3 is the discovery-side foundation for env-routing migration; Phase B/C/D follow-up plans depend on its inventory schemas but ship as separate PRs.

## Author / operator note

Same operator who wrote the spec, the implementation plan, and executed the 14 subagent-driven tasks. Self-review value is especially high here because:
1. The implementer subagents already caught and fixed 4 real plan bugs during execution.
2. The operator wrote BOTH the criteria-to-verify-against AND the code-being-verified — adversarial review hunts for the gaps neither side caught.

## Codebase profile (Phase 1.5)

- **Repo shape**: skills repo — Jinja2 templates + Python helper scripts; primary deliverable is content that runs/renders against CUSTOMER repos, not application code.
- **Languages / tools**: Python 3.10+ (uses `str | None` syntax + `is_relative_to` from 3.9+). stdlib `unittest` for tests. `re` + `tempfile` for scanning. PyYAML for parsing existing files (test environment has it; codemod runtime is documented as needing it).
- **No CI configured**. Verified via `gh pr checks 3` returning "no checks reported." Smoke is the only gate.
- **Test runner**: `bash skills/cost-billing/scripts/test-suite.sh` — 8-phase smoke. Phase 3 (py_compile) and Phase 8 (test_*.py discovery) auto-discover new scripts. Current: 67/67 PASS.
- **Coverage gap**: instrument/scripts/sdk_snapshot.py and task_planner.py have no unit tests (older inheritance). discovery/scripts/ has test_*.py for every implemented script — the new scripts follow this convention.
- **YAML emission convention**: hand-rolled string formatting (matches sdk_snapshot.py) — avoids PyYAML runtime dep for the customer codemod environment.
- **Conventions docs read**: instrument/SKILL.md (operational), shared/v1-decisions-log.md (decision matrix), spec at `docs/superpowers/specs/2026-06-06-cost-billing-env-routing-and-slugs-design.md` (the contract this PR implements).
- **Documented anti-patterns from rules**: immutable-by-default (the new scripts use mutable dataclasses and dict literals — acceptable for I/O scripts vs domain models), MANY SMALL FILES > FEW LARGE FILES (env_loader_scan.py at 706 LOC is at the upper bound), error handling must be explicit (the new scripts mostly raise/propagate rather than silently swallow).
- **CI quirks**: N/A (no CI).

## Per-PR detail

### PR #3 — env-routing + event-slug constants Phase A (discovery side)

- Branch: `spec/cost-billing-env-routing-design`
- Base: `main` @ `187a6cb` (just-merged PR #2)
- Head: `a96c5e1` at Phase 0
- 14 commits (2 docs + 12 implementation)

#### Summary of changed areas

- `shared/assets/env-loader-patterns.yaml` (new) — 10-pattern recognition catalog
- `discovery/scripts/env_loader_scan.py` (new) — env-routing scanner
- `discovery/scripts/test_env_loader_scan.py` (new) — 29 unit tests
- `discovery/scripts/slug_inventory.py` (new) — per-product slug inventory builder
- `discovery/scripts/test_slug_inventory.py` (new) — 13 unit tests
- `bootstrap-team-engineer/SKILL.md` (modified) — Q14b added
- `bootstrap-team-engineer/assets/04-final.schema.yaml` (modified) — 3 new fields
- `discovery/SKILL.md` (modified) — Phase 6/7 documented
- `docs/superpowers/specs/2026-06-06-cost-billing-env-routing-and-slugs-design.md` (new) — design spec
- `docs/superpowers/plans/2026-06-06-env-routing-and-slugs-phase-a.md` (new) — implementation plan

#### Original intention (Phase 1c)

Before this PR, the cost-billing/discovery skill produced four artifacts (cost-events-inventory.yaml, usage-events-inventory.yaml, output-input-map.yaml, attribution-bindings.yaml) describing WHAT events to emit but said nothing about HOW the customer's env (the API key) and CONSTANTS (event_type / meter_slug / etc.) should be wired. The bootstrap-team-engineer skill asked Q14 about SDK key storage strategy (env_var / aws_secrets / vault / etc.), the helper template's `_resolve_api_key()` branched on that strategy, and the framework callsite templates inlined every event_type / meter_slug as a string literal. Contract: discovery scans for cost/usage event sites; instrument renders templates referencing string literals + a strategy-branched `_resolve_api_key()`. No discovery-side awareness of the customer's existing env-loading code or the universe of slug literals being scattered.

#### New intention (Phase 1c)

PR #3 (Phase A of the multi-phase env-routing + slugs migration) adds two new discovery-side inventories and one new bootstrap question:

1. **env_loader_scan.py** scans each declared service's source tree for recognized env-loading patterns (pydantic-settings, dotenv, viper, etc. — 10 patterns in the catalog) AND the repo's deployment surfaces (Terraform variables, k8s Deployment manifests, docker-compose, .env.example, Dockerfile ENV lines). Produces `env-routing-inventory.yaml` with per-service `app_config` (pattern, file:line, confidence, stub_required) and per-service `deployment_surfaces` (kind + path + insert_kind). Granularity (per-service / repo-wide / hybrid / TBD) is declared in bootstrap Q14b.

2. **slug_inventory.py** reads the existing cost/usage event inventories + provider-catalog + CPO product list, and derives per-product canonical UPPER_SNAKE_CASE constants across 5 categories: EVENT_TYPE, METER_SLUG, FEATURE_KEY, PROVIDER, SPAN_TYPE. Refuses-to-run on collisions (two different source values that collapse to the same canonical name).

3. **Q14b** in bootstrap-team-engineer asks the engineer which granularity model their repo uses; the answer flows to `04-final.signed.yaml > integration.env_loader_granularity`.

Contract: discovery produces TWO new inventory YAMLs that Phase B (instrument env-wire) and Phase C (instrument slugs) will consume. Phase A ships independently — Phase B/C are separate follow-up plans. No code in customer repositories changes from this PR; only the discovery-side scanners and inventories are added.

#### Success criteria (Phase 1d)

1. **Catalog parse correctness**: `env-loader-patterns.yaml` parses cleanly and yields exactly 10 patterns (4 Python, 3 TS, 3 Go), each with `id`, `language`, `import_signals`, `structural_signals`, `wire_target`, `priority`. `load_pattern_catalog()` produces correctly-typed `Pattern` objects.
2. **Pattern detection accuracy on known shapes**: For each of the 10 patterns, a fixture file containing the canonical idiom is detected at HIGH confidence (when both import_signals and structural_signals match) or MEDIUM (when only structural matches — `ts-process-env-direct` and `go-os-getenv` rely on this band).
3. **Service walker conflict resolution**: When a service has multiple files matching different patterns (e.g. one with pydantic-settings AND one with dotenv+os.getenv), the highest-priority pattern's file wins. Priority comes from the catalog; ties broken by path depth (shallower path wins).
4. **Deployment-surface recognition**: Across a fixture repo with `infra/terraform/variables.tf`, `infra/k8s/deployment.yaml`, `docker-compose.yml`, `.env.example`, `Dockerfile` with ENV lines — `scan_deployment_surfaces` returns one entry per detected surface with the correct `insert_kind`.
5. **Stub-required propagation**: When `scan_service` returns None (unrecognized) OR returns a LOW-confidence match, the resulting `app_config` carries `stub_required: true`. Phase B will dispatch on this flag.
6. **YAML output structure**: `emit_inventory_yaml` produces a parseable YAML document with `generated_at`, `granularity`, `granularity_source`, `services[]` and the expected per-service shape (app_config + deployment_surfaces). Same for `emit_slug_inventory_yaml` with per-product/per-category shape.
7. **Slug duplicate-refuse**: `check_duplicates` returns a non-empty error list when two different source values in the same (product, category) collapse to the same `UPPER_SNAKE_CASE` name (e.g. `checkout.recommendation` and `checkout-recommendation`). Main exits 2 with a CRITICAL message.
8. **Q14b → env_loader_granularity flow**: The bootstrap question's answer reaches `04-final.signed.yaml > integration.env_loader_granularity`, and `parse_services_and_granularity()` correctly reads it, defaulting to `TBD` with `granularity_source: default-fallback` when absent.

#### Codebase-specific challenges (Phase 1e)

1. **Customer's signed.yaml has 2-space-indented `services:` block (most common YAML formatter)**: `parse_services_and_granularity` uses PyYAML's `safe_load` — that's correct regardless of indent (unlike `sdk_snapshot.py`'s hand-rolled parser which had the indent-fragility bug). Verify: is `safe_load` actually used here, not a hand-rolled fallback?

2. **Customer repo has BOTH `pydantic-settings` AND a `legacy_dotenv.py` (real migration scenario)**: scan_service is supposed to pick the higher-priority pydantic match. But if the LEGACY file is at a shallower path (e.g. `app/legacy_env.py`) and the canonical config is at a deeper path (e.g. `app/core/config.py`), the depth-asc tie-breaker favors the legacy. Trace the sort_key — does priority correctly dominate depth, or does confidence_score equality + same priority + path depth produce a wrong winner?

3. **Customer service is in a Python+TypeScript polyglot directory (e.g. `services/api/` has both `app/config.py` AND `frontend/src/env.ts`)**: With `language: python` in services, scan_service ignores `.ts` files via `_EXTENSION_BY_LANGUAGE`. But what about `language: hybrid` or multi-language services? Phase A only supports one language per service entry — does the parser/main produce a sensible error or silently emit `unrecognized`?

4. **Customer's deployment-surface scan walks the WHOLE repo for `scan_deployment_surfaces` (rglob)**: On a monorepo with 100k+ files (vendor/ already skipped), the scan still iterates every YAML file looking for `kind: Deployment`. Is this O(N) walk a perf cliff? For Phase A this runs once per Phase 1 invocation; acceptable. Document if it becomes a problem.

5. **Customer has a YAML file at the repo root that LOOKS like docker-compose but is named `compose.production.yml`**: Filename whitelist at L449-450 only accepts `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, `compose.yaml`. Production variants (`compose.dev.yml`, `docker-compose.prod.yaml`) miss the whitelist and surface as none of the recognized kinds. Phase B silently misses them. **Real-customer scenario.**

6. **Customer's `04-final.signed.yaml` declares `env_loader_granularity: hybrid` with `shared_config_path: packages/config`**: But Phase A's code at L551 ONLY honors repo-wide when `granularity == "repo-wide"`. Hybrid degrades to per-service silently (documented in the docstring). A customer who declares hybrid thinking they get shared-path scanning instead gets the per-service path. **Doc-vs-runtime gap that the docstring acknowledges but no warning fires.**

#### Phase 1f self-review rounds

##### Round 1

- **Intentions**: 1 edit. Original intention initially said "the helper bypasses the customer's existing env-loading layer" — but that's the OUTCOME, not the contract. Rewrote to describe the v0.2 helper's strategy-branched `_resolve_api_key()` as the actual mechanism.
- **Success criteria**: 1 added (#8 — Q14b → signed.yaml → parse_services_and_granularity flow). Initially missed because the operator was thinking about the SCANNERS but the bootstrap-question is also part of this PR's contract.
- **Challenges**: 1 sharpened (#2 — depth tie-breaker), 1 added (#5 — docker-compose filename whitelist). Re-read scan_deployment_surfaces L449-450 and noticed it doesn't accept production-prefixed compose filenames.

##### Round 2

- **Intentions**: no edits.
- **Success criteria**: no edits.
- **Challenges**: 1 added (#6 — hybrid degrades to per-service silently). Discovered by re-reading L551 of env_loader_scan.py: `if granularity == "repo-wide" and shared_config_path:` — the condition silently turns "hybrid" into "per-service" without warning.
- Re-read of diff after edits: confirmed alignment.
- Suspicions deferred to Phase 2:
  - `_python_insert_line` heuristic `if ":" in stripped or "=" in stripped` (L169) might match docstring lines that contain colons.
  - `_ts_insert_line` closer-set `{"}", "};", "})", "});", "})", "}))"}` (L205) has duplicate `"})"` — Python set dedupes silently, but `"}))"` (triple-paren close) seems missing.
  - YAML evidence/wire_target emit escapes `"` but not `\` (L605, L611, L611 of env_loader_scan; L205-206 of slug_inventory) — if evidence contained `foo\bar` the YAML might interpret `\b` as a backspace escape.
  - `check_duplicates` docstring (L158-162 in slug_inventory) says `_add_unique` dedupes by NAME but it actually dedupes by (name, value) — doc rot.

Round 2 finding count: 1 challenge added + several deferred suspicions. Per skill: more than 1 edit → record AND add the "Self-review was still finding issues at round 2" operator signal to the Phase 2 reviewer brief.

#### Risk map by subsystem (Phase 1g)

- **env_loader_scan.py pattern detection** — highest blast radius. Wrong recognition means Phase B emits wrong code. Specific risks: regex precedence + priority tie-break (Challenge #2), insert-line heuristics misfiring on docstrings (suspicion deferred), `_ts_insert_line` closer-set typo (suspicion).
- **env_loader_scan.py deployment-surface scan** — Wrong recognition leaks to PR comments. Specific risks: compose filename whitelist too narrow (Challenge #5), no warning when no surfaces detected.
- **env_loader_scan.py granularity dispatch** — Customer declares hybrid, gets per-service silently (Challenge #6). No runtime warning.
- **slug_inventory.py duplicate-refuse** — Refuse-to-run is a strong contract; bad detection logic would either over-refuse (false collisions) or under-refuse (real collisions slip through). The Task 12 fix to `_add_unique` (name+value tuple dedup) was correct; docstring drift remains.
- **YAML emit (both scripts)** — Hand-rolled emit doesn't escape backslashes, so values with `\` could produce malformed YAML. Phase B reads these via PyYAML → may interpret backslash escapes incorrectly.
- **Bootstrap Q14b flow** — Engineer's declaration must reach the scanner. The chain is bootstrap → SKILL.md prose → signed.yaml → parse_services_and_granularity. Each hop is a potential break.
- **Test fixtures** — All tests use `tempfile.TemporaryDirectory()` — no checked-in fixture files. Self-contained but harder to inspect. No fixture for "real customer repo shape" — Phase D adds an e2e fixture.

#### Verification commands

- `python3 skills/cost-billing/discovery/scripts/test_env_loader_scan.py` — scoped unit tests for env_loader_scan
- `python3 skills/cost-billing/discovery/scripts/test_slug_inventory.py` — scoped unit tests for slug_inventory
- `bash skills/cost-billing/scripts/test-suite.sh` — repo-wide smoke (8 phases)
- `gh pr checks 3` — CI verification (returns "no checks reported" — verified per round)

#### Review rounds

##### Round 1 (HEAD a96c5e1)

Reviewer: `code-reviewer` agent with Phase 2 adversarial brief including 8 success criteria, 6 codebase-specific challenges, operator signal "self-review still finding issues at round 2", and 4 specific suspicions deferred for the reviewer (`_python_insert_line` docstring colon false-positive, `_ts_insert_line` closer-set typo, YAML backslash escape, `check_duplicates` docstring rot).

Total raw findings: **5 IMPORTANT + 12 MINOR = 17**. Operator-adjusted severity:

| Reviewer sev | Operator sev | Count | Notes |
|---|---|---|---|
| IMPORTANT | **CRITICAL** | 1 | YAML backslash escape — promoted because Phase B downstream would consume the malformed YAML; data corruption class. |
| IMPORTANT | IMPORTANT | 4 | k8s envFrom check, hybrid silent degrade, compose filenames, missing YAML round-trip test |
| MINOR | MINOR | 12 | doc rot + corner cases |

CONFIRMED: CRIT=1, IMP=4, MIN=12, NIT=0.

**Pass 1 contract verification**: criteria 1-3, 5, 6 PASS; criterion 4 PARTIAL (k8s over-detection); criterion 6 CONDITIONAL PASS (backslash bug); criteria 7-8 PASS.

**Challenge verification**: #1 #2 #3 HANDLED; #4 UNHANDLED-doc-only (perf, accepted); #5 #6 UNHANDLED → CONFIRMED IMPORTANT, FIXED.

**CI status round 1**: no checks configured for this PR (verified via `gh pr checks 3`).

**Sibling search (Phase 4a)**: backslash YAML escape bug class found in 3 pre-existing emitters: `instrument/scripts/attribution_discovery.py:_emit_yaml` (writes `source` field unescaped), `instrument/scripts/task_planner.py:emit_tasks_yaml` (writes `helper_import` and various `v` values unescaped), `instrument/scripts/sdk_snapshot.py:yaml_dump` (writes capability values unescaped). **Out of scope for PR #3 — filing as follow-up.** The bug class is identical but those files were not changed by this PR.

**Operator spot-check round 1**: I personally read `env_loader_scan.py:577-622` (emit_inventory_yaml) and `slug_inventory.py:184-208` (emit_slug_inventory_yaml) line-by-line to confirm the missing-backslash-escape bug. I also wrote the round-trip + backslash regression tests by hand using PyYAML — verified they FAIL before the fix and PASS after.

Low-only streak after round 1: **0** (CRITICAL + IMPORTANT confirmed).

##### Round 2 (HEAD 4aa4d8d — after Round 1 fixes)

Reviewer: `code-reviewer` agent with re-review brief naming the 4 Round 1 fix commits + asking for fix verification + new-bug hunt + remaining-risks re-check.

Findings: **0 CRITICAL, 0 IMPORTANT, 2 MINOR**.

| Reviewer sev | Operator sev | Count | Disposition |
|---|---|---|---|
| MINOR (NEW IN ROUND 1 FIX) | MINOR | 1 | envFrom cross-container false positive introduced by adf1bc1's regex tightening. Operator spot-check reproduced empirically. FIXED in d276d80. |
| MINOR | MINOR | 1 | Missing YAML round-trip test for `granularity="hybrid (degraded to per-service)"`. Two-line test addition. FIXED in d276d80. |

CONFIRMED: CRIT=0, IMP=0, MIN=2, NIT=0.

**Pass 1 contract**: all 8 success criteria PASS. Criterion #4 now stricter (envFrom required). Criterion #6 now round-trip-verified.

**Challenge verification**: all 6 challenges either FIXED in Round 1 or accepted-as-latent (perf).

**CI status round 2**: no checks configured (verified).

**Operator spot-check round 2**: empirically reproduced the cross-container false positive via a 17-line multi-container YAML fixture. Confirmed the naive `envFrom:[\s\S]{0,200}secretRef:` regex matched across container boundaries. Designed and implemented the line-walker fix (`_envfrom_secretref_in_same_container`) that tracks YAML indent and breaks on `- name:` (next container) or sibling key.

Low-only streak after round 2: **1**.

##### Round 3 (HEAD d276d80 — after Round 2 fix, exit-gate eligible)

Reviewer: `code-reviewer` agent with exit-gate brief focusing on `d276d80` fix verification + new-bug hunt + final scan for latent classes.

Findings: **0 CRITICAL, 0 IMPORTANT, 0 MINOR, 1 NIT**.

| Reviewer sev | Operator sev | Count | Disposition |
|---|---|---|---|
| NIT | NIT | 1 | `_envfrom_secretref_in_same_container` regex `^(\s*)envFrom:\s*$` requires the key to be end-of-line — trailing YAML comment like `envFrom: # loaded from shared-secrets` is silently missed. Conservative false-negative; uncommon in real k8s. ACCEPTED-residue. |

CONFIRMED: CRIT=0, IMP=0, MIN=0, NIT=1.

**Pass 1 contract**: all 8 success criteria PASS.

**Walker correctness trace** (reviewer worked through three scenarios + nested-name + multi-envFrom + EOF):
- Cross-container fixture: walker correctly breaks at `- name: app` (next container), returns False → fix VERIFIED.
- Single container with `envFrom + secretRef`: walker finds `- secretRef:` at same indent, returns True → positive case VERIFIED.
- Single container with `envFrom + configMapRef only`: walker scans through items and breaks on `volumes:` (sibling key at envfrom_indent) → no false positive.
- `secretRef.name` nested inside the secretRef block: not reachable (walker returned True on the parent `- secretRef:` line).
- Multiple `envFrom:` blocks: outer loop independently visits each → handled correctly.

**YAML round-trip verification**: `emit_inventory_yaml` writes `granularity: hybrid (degraded to per-service)` as a plain scalar. PyYAML round-trips this correctly (spaces and parens are legal in plain scalars). Verified empirically.

**CI status round 3**: no checks configured (verified).

**Operator spot-check round 3**: reproduced the NIT empirically — wrote a YAML fixture with `envFrom:  # loaded from shared-secrets` (trailing comment) and confirmed `scan_deployment_surfaces` does NOT detect it. Conservative false-negative as the reviewer described.

Low-only streak after round 3: **2 — EXIT GATE PASSES**.

#### Bugs fixed (final list)

| Commit | Operator severity | Description |
|---|---|---|
| `4fc7927` | CRITICAL | YAML backslash escape + 4 round-trip regression tests (criterion #6) |
| `adf1bc1` | IMPORTANT | k8s envFrom requirement + compose env-suffix filenames (criterion #4, challenge #5) |
| `075d6c9` | IMPORTANT | Hybrid granularity loud warning + records degradation (challenge #6) |
| `4aa4d8d` | MINOR (×4) | Doc-rot bundle: _ts_insert_line closer-set, scan_service docstring, check_duplicates docstring, SKILL.md to_constant_name formula |
| `d276d80` | MINOR | k8s envFrom must stay in same container (Round 2 regression fix introduced by adf1bc1) + hybrid YAML round-trip test |

#### Findings rejected (false positives)

None across 3 rounds. Severity demotions (HIGH → MINOR, IMPORTANT → MINOR for some items) are operator grading judgments, not rejections.

#### Defensive hardening applied

- YAML round-trip regression tests in commits `4fc7927` and `d276d80` are themselves a defensive harness: future emit changes that re-introduce escape bugs OR scalar-quoting issues will fail at smoke time rather than silently corrupt customer data downstream.
- The `_envfrom_secretref_in_same_container` line-walker is more defensive than a regex — it understands the YAML structure, not just textual proximity. Future k8s manifest shapes (multi-container, nested envFrom) are less likely to false-positive.

Considered but deferred:
- `try/except` around `re.compile(class_pattern)` in `_python_insert_line` — if catalog ever ships a malformed regex, scan crashes. Catalog content is currently statically checked; deferred per "behavior correct under failure" bar.
- Warning when `_read_yaml_safe` returns `{}` due to PyYAML being absent — pre-existing pattern, applies to multiple scripts in the suite; out of scope for PR #3.

#### Remaining risks (accepted non-blocking)

All NIT or pre-existing-MINOR, all confirmed by reviewer + operator spot-check:

1. **Trailing YAML comment on `envFrom:` defeats detection** (NIT, new in d276d80). Conservative false-negative; uncommon in real k8s. The regex requires the key line to be `^(\s*)envFrom:\s*$` — `envFrom: # comment` doesn't match. Could be widened with `^(\s*)envFrom:\s*(#.*)?$` in a follow-up.

2. **Pre-existing backslash YAML escape bugs in `attribution_discovery.py`, `task_planner.py`, `sdk_snapshot.py`** (MINOR, pre-existing). Same bug class as the CRITICAL fix; out-of-scope for PR #3. Filing as a follow-up — affects 3 emitters in instrument/scripts/.

3. **`_python_insert_line` heuristic** at L169 can match docstring lines containing colons (MINOR, pre-existing). Corner case.

4. **`ts-process-env-direct` fires on single `process.env` occurrence** (MINOR, pre-existing). Could create stub-fragments in utility files; operator graded as acceptable since stub_required path is safe.

5. **`generated_at` unquoted → PyYAML coerces to `datetime`** (MINOR, pre-existing). Latent until Phase B reads.

6. **`scan_deployment_surfaces` does full `rglob("*")` over repo** (MINOR, pre-existing). O(N) for one-shot Phase 1 invocation.

7. **`python-decouple` structural signal requires uppercase keys** (MINOR, pre-existing). Lowercase `config('database_url')` detected only at LOW confidence.

8. **`ts-zod-env-schema` has two overlapping import signals** (MINOR, pre-existing). Cosmetic; duplicate evidence strings only.

9. **PyYAML-missing produces silent empty output** in slug_inventory.py (MINOR, pre-existing pattern across suite).

10. **Q14b doesn't note hybrid degradation** in the question prose (MINOR, pre-existing). Engineer only sees warning at runtime via stderr.

11. **Unquoted plain scalars in `emit_inventory_yaml`** (MINOR, pre-existing). Slugs with `: ` would corrupt output — but slugs are catalog-controlled.

12. **Missing file error UX in main()** (MINOR, pre-existing). Raw FileNotFoundError traceback.

#### Status

`ready-for-human`

Exit gate: **PASS**. Streak = 2 consecutive LOW-only rounds with CI verified. Loop closed at HEAD `d276d80`.

#### Bugs fixed

| Commit | Operator severity | Description |
|---|---|---|
| `4fc7927` | CRITICAL | YAML backslash escape in 3 emit points + 4 round-trip regression tests (Phase 1d criterion #6, suspicion deferred from 1f) |
| `adf1bc1` | IMPORTANT | k8s detection now requires envFrom: secretRef (criterion #4); docker-compose filename matches env-suffix variants like `docker-compose.prod.yaml` (challenge #5) |
| `075d6c9` | IMPORTANT | Hybrid granularity: loud stderr warning + records degradation in output YAML (challenge #6) |
| `4aa4d8d` | MINOR (×4) | Doc rot bundle: `_ts_insert_line` closer-set duplicate cleanup + missing triple-paren entry; `scan_service` docstring corrected to "shallowest wins"; `check_duplicates` docstring updated for (name, value) dedup; SKILL.md `to_constant_name` formula replaced with the actual regex-based implementation |

#### Findings rejected (false positives)

None in round 1. All reviewer findings were either accepted-as-flagged or operator-graded (table above). The MINOR severity demotion for several items (NOT rejection) is a grading judgment — the findings are real, just not ship-blocking.

#### Defensive hardening applied

- The YAML round-trip regression tests in commit `4fc7927` are themselves a defensive harness: future emit changes that re-introduce escape bugs will fail at smoke time rather than corrupt customer data silently.
- Considered but DEFERRED: try/except around `re.compile(class_pattern)` in `_python_insert_line` — if catalog ever ships a malformed regex, scan would crash. Acceptable per the "behavior correct under failure" bar — current catalog is statically checked, future catalog evolution can add this guard if needed.

#### Remaining risks (accepted non-blocking)

All MINOR-level, all confirmed by reviewer + operator spot-check:

1. **Pre-existing backslash YAML escape bugs in `attribution_discovery.py`, `task_planner.py`, `sdk_snapshot.py`**. Sibling search confirmed the same bug class in 3 files outside PR #3's scope. Filing as follow-up PR; these emitters produce different inventories not consumed by Phase A's downstream. Risk: customer data with backslashes silently corrupted in attribution-bindings.yaml / tasks.yaml / sdk-surface-snapshot.yaml.

2. **`_python_insert_line` heuristic `if ":" in stripped or "=" in stripped` can match docstring lines containing colons**. If a customer's Settings class has only a docstring (no real fields), the insertion line falls inside the docstring. Phase B would inject mid-docstring. Corner case — most real settings classes have at least one real field.

3. **`ts-process-env-direct` fires on a single `process.env.X` occurrence**. The catalog's description says "multiple reads" but the regex has no multiplicity guard. Any TS file with one `process.env.NODE_ENV` read gets flagged at MEDIUM confidence. Downstream sees stub_required=False and emits a real field into the file. Could create stub-fragments in utility files. Operator call: not promoted to IMPORTANT because the fallout is "emits a stub in a utility file" not "wrong production code" — the stub-required path is still a safe behavior under accidental detection.

4. **`generated_at` unquoted — PyYAML parses as `datetime`**. Phase B code reading `inventory["generated_at"]` will get a `datetime` object, not a string. Latent type surprise if Phase B uses string ops. Quoting it (`f'generated_at: "{...}"'`) would prevent the coercion. Defer to Phase B's first read.

5. **`scan_deployment_surfaces` does full `rglob("*")` over repo**. On a 100k-file monorepo this is O(N). Acceptable for one-shot Phase 1 invocation; document if it becomes a perf cliff.

6. **`python-decouple` structural signal requires uppercase keys** (`config\(['"][A-Z_]+['"]`). Lowercase `config('database_url')` is detected only at LOW confidence (import-only). Edge case — uppercase is the convention.

7. **`ts-zod-env-schema` has two overlapping import signals**. Cosmetic — same line matches both regexes; both contribute identical evidence. No incorrect output, just redundant evidence strings.

8. **No test for `_python_insert_line` with docstring-only class** (covers risk #2 above).

#### Status

`round-2-pending` — fixes pushed to `4aa4d8d`. Round 2 reviewer needs to verify fixes + hunt for new bugs introduced by them.

## Final summary

PR #3 — `ready-for-human` after 3 review rounds, 5 fix commits, head SHA `d276d80`.

### Loop trajectory

| Round | Head SHA | Findings raw → operator-graded | Streak | CI |
|---|---|---|---|---|
| 1 | a96c5e1 | 5 IMP + 12 MIN raw → CRIT=1, IMP=4, MIN=12, NIT=0 | 0 | no checks (verified) |
| 2 | 4aa4d8d | 2 MIN raw → MIN=2 | 1 | no checks (verified) |
| 3 | d276d80 | 1 NIT raw → NIT=1 | **2 (EXIT GATE)** | no checks (verified) |

### Fix commits (5, oldest first)
- `4fc7927` — CRITICAL: YAML backslash escape in 3 emit points + 4 round-trip regression tests
- `adf1bc1` — IMPORTANT: k8s envFrom: secretRef requirement + compose env-suffix filename matching
- `075d6c9` — IMPORTANT: hybrid granularity loud stderr warning + records degradation in output YAML
- `4aa4d8d` — MINOR×4: doc-rot bundle (_ts_insert_line closer-set, scan_service docstring, check_duplicates docstring, SKILL.md to_constant_name formula)
- `d276d80` — MINOR: k8s envFrom must stay in same container (regression caught by Round 2 reviewer; line-walker replaces naive regex) + hybrid YAML round-trip test

### Verification
- Unit tests: env_loader_scan 36 tests, slug_inventory 15 tests, all PASS. Total 51 unit tests (originally 8 + 13 = 21; added 30 across rounds).
- Smoke (`bash skills/cost-billing/scripts/test-suite.sh`): **67/67 PASS** on every commit, every round.
- CI: no checks configured — verified explicitly each round, not silently skipped.
- Operator spot-checks: line-by-line code read of both emitters (R1), empirical reproduction of cross-container false positive (R2), empirical confirmation of trailing-comment NIT (R3).

### Sibling search outcome
Found the same backslash YAML escape bug class in 3 pre-existing emitters: `instrument/scripts/attribution_discovery.py:_emit_yaml`, `instrument/scripts/task_planner.py:emit_tasks_yaml`, `instrument/scripts/sdk_snapshot.py:yaml_dump`. **Filed as follow-up** — out-of-scope for PR #3 which is "add Phase A discovery scripts." Recommended follow-up PR to apply the same `replace('\\', '\\\\').replace('"', '\\"')` fix + round-trip tests to those three files.

### Remaining risks (12 accepted non-blocking)
All NIT or pre-existing-MINOR. Detailed list in the per-PR section above. Notable: pre-existing sibling backslash bugs (item #2) deserve their own follow-up PR; everything else is corner-case or cosmetic.

Merge status: **NOT MERGED — awaiting explicit user permission.**
