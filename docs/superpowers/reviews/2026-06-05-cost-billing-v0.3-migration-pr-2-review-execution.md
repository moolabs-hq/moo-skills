# Adversarial PR Review — cost-billing v0.3 migration (PR #2)
Date: 2026-06-05
Operator: claude-opus-4-7 (main session)

## PRs in scope

| PR  | Branch                                       | Base | Head SHA  | Status      |
|-----|----------------------------------------------|------|-----------|-------------|
| #2  | spec/cost-billing-unified-surface-refactor   | main | b7e43e6   | in-progress |

Base SHA at start: `0b855d14c65c0317107e5d331fc3af0dd59d8b96`
Head SHA at start: `b7e43e6fc18c1789e88717d904cf2a46d52c1037`
CI status (Phase 5.0 pre-flight): **no checks configured for this PR (verified via `gh pr checks 2`)**.

## Cross-PR dependencies

None. PR #2 is standalone.

## Author / operator note

The operator (this session) authored the bulk of the migration. Adversarial review is especially valuable here because the author is also the reviewer-of-record — the loop catches what authorship blindness misses.

## Codebase profile (Phase 1.5)

- **Repo shape**: skills repo — agent skills + Jinja2 codemod templates + Python helper scripts. Not application code; the "production" output is Jinja2 templates that get rendered into CUSTOMER codebases by an agent following the SKILL.md prose.
- **No top-level CLAUDE.md / AGENTS.md** in this repo. Operator-level conventions live in `~/.claude-moolabs/` (rules/, skills/) — out of scope for code review of THIS repo.
- **No CI workflows configured** (`.github/workflows/` absent). Verified via `gh pr checks 2` returning "no checks reported."
- **Test runner**: `skills/cost-billing/scripts/test-suite.sh` — single bash script with 8 phases:
  1. SKILL.md frontmatter present
  2. YAML schema parsing
  3. Python script syntax (py_compile)
  4. install.sh syntax (bash -n)
  5. install.sh dry-run per persona (5 personas × shell)
  6. install.sh dry-run under `/bin/bash` (bash 3.2 customer-env compat)
  7. Template renders + adversarial-regression assertions (FR-3, no-v0.2-leakage, py-compile-equivalent for Python templates, gofmt for Go template, await checks for some TS branches)
  8. Python unit tests (test_*.py) — discovery/scripts only
- **Unit test coverage gaps**: `instrument/scripts/sdk_snapshot.py` and `instrument/scripts/task_planner.py` have NO unit tests. The smoke covers their parse-and-import only (Phase 3). Behavioral coverage relies entirely on Phase 7's downstream template assertions, which exercise the rendering side but NOT the introspector's contract surface.
- **Languages and tooling**:
  - Jinja2 templates (`.j2`) for codemod output
  - Python 3.10+ for the helper scripts
  - Bash for the test driver + installer
- **External tools**: `gofmt` (optional; smoke degrades gracefully when absent), `git` (for SDK shallow-clone in Phase 1.5 of the codemod), `gh` for repo interaction (operator-side, not codemod runtime).
- **Conventions docs found in scope**: SKILL.md per skill; cost-billing/shared/v1-decisions-log.md (decision matrix); cost-billing/shared/sdk-surface-reference.md (the SDK contract source); cost-billing/shared/gaps-tracker.md (open questions log).
- **CI quirks**: N/A (no CI).
- **Documented anti-patterns from conventions docs**: silent-skip in preflight scripts (the v1 decisions log notes Error A / Error B from prior reconciliation incidents); silently dropping cost events on transport failure (resolved by SDK-internal never-drop rail in v0.3).

## Per-PR detail

### PR #2 — Migrate cost-billing skill suite to SDK v0.3.0-rc1 unified-ingest surface

- Branch: `spec/cost-billing-unified-surface-refactor`
- Base: `main` @ `0b855d14`
- Head: `b7e43e6` (latest as of Phase 0)
- Stats: 33 files, +2243/-1465, 10 commits

#### Summary of changed areas

- **PRD** (`docs/superpowers/specs/2026-06-01-unified-surface-refactor-design.md`) — 1 added file documenting the v0.3 migration contract with pre-mortem.
- **Helper templates** (3 modified `.j2` files) — Python, TypeScript, Go helpers rewritten around three ergonomic singular SDK methods.
- **Framework callsite templates** (6 modified `.j2`) — FastAPI, Django, Flask, Express, NestJS, Next.js routed through the new helpers.
- **Phase 1.5 introspector** (`sdk_snapshot.py`) — re-grounded on the v0.3 wrapper layer (`_dx_namespaces.{py,ts}`); MODE A extended to merge wrapper-class methods; introspect_typescript rewritten with balanced-brace block extractor.
- **Task planner** (`task_planner.py`) — schema/import updates for v0.3 helper names.
- **Smoke test** (`scripts/test-suite.sh`) — Phase 7 assertions rewritten for v0.3 contract (presence of singular methods, absence of v0.2 tokens, FR-3 surgical checks, py-compile / gofmt syntax gates).
- **Operational docs** (10 SKILL.md + shared/*.md + schema YAML) — prose updated to v0.3 capability flags, method names, and helper API.
- **Examples** (`sdk-surface-snapshot.yaml`, `tasks.yaml`) — updated to v0.3 schema.

#### Original intention (Phase 1c)

The v0.2.0-rc9 codemod produced per-service helper modules and framework callsite inserts that:
- Hard-coded a `cost_event_direct_emit` capability gate (from Phase 1.5 introspection) that branched the cost lane between a direct SDK call (`client.cost.ingest_events_batch`) and an OTel-span + structured-log dual-transport fallback.
- Required `tenant_id` as an explicit kwarg on every helper signature and refused to emit when it was None.
- Generated `usage_event_id` (a UUID minted client-side) and threaded it manually as a sibling-pair join key between the usage and cost emissions (TWO calls per sibling-pair: `emit_usage_event_safe(...)` followed by `emit_cost_event_safe(...)`).
- Built CloudEvent envelopes by hand, with the helper's never-drop guarantee implemented via the dual-transport rail.
- Walked the openapi-generated backing classes (`EventsApi`, `CostEventsApi`) via `CAPABILITY_MAP` to verify the SDK exposed the expected v0.2 plural methods.

Contract held with downstream: helpers swallow SDK errors and emit a structured-log line on every failure. Callsites pass `tenant_id` explicitly. Phase 1.5 emits the boolean `cost_event_direct_emit` and `cost_event_method_path` for the helper template's Jinja branch to consume.

#### New intention (Phase 1c)

After v0.3.0-rc1:
- The SDK exposes three **singular ergonomic methods** on a customer-facing wrapper layer (`_dx_namespaces.{py,ts}`): `client.usage.ingest_event(args)`, `client.cost.ingest_event(args)`, `client.events.ingest(args)`. The third (US-008) is mounted as a `@property` on `Moolabs` and is NOT in `CAPABILITY_MAP`.
- Helpers expose three corresponding functions (`emit_usage_event_safe`, `emit_cost_event_safe`, `emit_event_safe`) that are unconditional pass-throughs to the SDK methods, plus a single env-gated `handleEmitErr` decision point: `SDK_DEVELOPMENT` non-empty → throw/raise/panic (strict dev mode); unset → structured-log recovery rail (never-drop, prod default).
- `tenant_id` is removed from every helper signature and envelope (FR-3 — server derives from API key). The codemod's discovery side (Phase 1.6 attribution-bindings.yaml) may still capture it for internal use, but it never reaches the wire.
- `entity_id` replaces `usage_event_id` as the sibling-pair join key. Customers bind it from `request_id` (or a local uuid fallback when no request_id binding exists).
- Sibling-pair callsites collapse from two calls to **one** call (`emit_event_safe(args)` → `client.events.ingest(args)`), with the usage and cost lanes carried in a single envelope via per-span cost breakdowns.
- The `cost_event_direct_emit` capability flag is gone. Phase 1.5 emits `unified_ingest_present` (the AND of all three lanes' ergonomic-method presence) and refuses-to-run if any lane is missing — there is no fallback rendering path.
- The introspector merges wrapper-class methods (`_UsageNamespace`, `_CostNamespace`, `_EventsNamespace`) into the per-capability namespace AND adds `client.events` explicitly, then verifies all three expected ergonomic methods are present.

Contract held with downstream: helpers throw on dev mode for SDK errors; emit a structured-log line in prod. Callsites do NOT pass `tenant_id`. Phase 1.5 emits `unified_ingest_present` and refuses-to-run when False.

#### Success criteria (Phase 1d)

1. **All three helper templates** (Python, TS, Go) render output that calls EXACTLY the v0.3 singular ergonomic methods (`client.X.ingest_event` / `client.X.ingestEvent` / `client.X.IngestEvent`) and NOT the v0.2 plural methods.
2. **Sibling-pair callsites** make exactly ONE helper call (`emit_event_safe` / `emitEventSafe`), not two. Cost and usage lane data are carried in the same envelope.
3. **FR-3 is enforced in rendered output**: no `tenant_id=` Python kwarg, no `tenantId:` TS field, no `TenantID:` Go struct field anywhere in helpers or callsite renders.
4. **`unified_ingest_present` correctly gates the codemod**: when the snapshot reports False, the introspector exits with CRITICAL and the codemod cannot proceed. When True, the codemod proceeds.
5. **Phase 1.5 introspector correctly discovers all three lanes** against the real v0.3.0-rc1 SDK source (verified: `gh api` reading of `_dx_namespaces.py` and the corresponding TS .d.ts produces `usage.ingest_event`, `cost.ingest_event`, `events.ingest`).
6. **Env-gated error handling is wired**: rendered helpers contain a `SDK_DEVELOPMENT` env check that controls strict vs lax error behavior, with a single decision point per helper (no duplicate branches).
7. **The smoke suite is honest**: Phase 7 assertions catch a v0.2-style mistake if anyone ever re-introduces it (negative-leakage assertions present for `cost_event_direct_emit`, `ingest_events_batch`, `tenant_id` field usage, etc.).
8. **Operational SKILL.md docs match v0.3 reality**: an agent reading instrument/SKILL.md to execute the codemod won't find references to v0.2 capability flags, plural methods, or the obsolete dual-transport branch.

#### Codebase-specific challenges (Phase 1e)

1. **Introspector silently fails MODE A → falls through to MODE B**: if a future SDK ships with `_dx_routing.py` slightly malformed (e.g. trailing-comma syntax error, parser version skew), `_extract_capability_map` returns None and the introspector falls through to MODE B/C — which DOES NOT merge wrapper methods. Result: `client.usage.ingest_event` not discovered → `unified_ingest_present=False` → CRITICAL abort. The customer sees the abort but the message doesn't explain that MODE A was bypassed. **Observable failure**: codemod refuses-to-run with a misleading "missing expected methods" error when the actual root cause is parser fall-through.

2. **`parse_signed_yaml` silently returns empty config**: the hand-rolled YAML parser in `sdk_snapshot.py` requires specific indentation (4+ / 6 / 8 spaces). If a customer's `04-final.signed.yaml` is generated with 2-space indentation (the standard YAML convention), the regex at L654 won't match and `parse_signed_yaml` returns `{}`. The main loop at L709 then falls through to `strategy="latest-tag"` for every language, ignoring the customer's pinning preferences. **Observable failure**: customer pinned moolabs-py to v0.2.0-rc9 explicitly; codemod silently snapshots whatever the latest tag is, possibly v0.3.0-rc1, and applies v0.3 templates against a customer who isn't ready.

3. **`_wrapper_class_for` capitalization breaks on multi-word capabilities**: the convention assumes `_<Capitalized>Namespace`, computed as `f"_{capability.capitalize()}Namespace"`. If a future SDK adds a multi-word capability like `span_ingest`, this yields `_Span_ingestNamespace` (capitalize() only uppercases the first letter), while the actual wrapper class would be `_SpanIngestNamespace`. **Observable failure**: codemod fails to discover the new wrapper's ergonomic methods → `unified_ingest_present=False` if the new capability is a lane the helper needs.

4. **Python introspector lacks `seen_paths` guard**: `introspect_typescript` de-duplicates discovered namespaces via `seen_paths` set, but `introspect_python` does not. If a future SDK ever lists `events` in CAPABILITY_MAP (i.e. removes the special-casing US-008 used), the python introspector would emit `client.events` TWICE — once from the loop merge and once from the explicit add at L324-326. Downstream `yaml_dump` would emit both, and `LanguageSnapshot.has_method` would still work (it finds the method in the first match) — but the snapshot YAML would have duplicate entries and any tool that reads them sequentially could misbehave.

5. **Customer-id fallback to literal `"unknown"`**: when a customer's service doesn't bind `customer_id` (no Phase 1.6 binding for it), the template renders `customer_id=str("unknown")` literally. Every emission for that service is then bucketed to a single "unknown" customer downstream. The `task_planner.py` refuse-to-run gate only verifies the binding KEYS are present in `attribution-bindings.yaml` (per test-suite L444-447), not that the bound expression resolves to non-null at runtime. **Observable failure**: customer ships the codemod expecting per-customer billing; downstream sees one "unknown" customer aggregating all events; billing reports are unusable.

6. **Phase 1.6 still requires `tenant_id` despite FR-3**: the test-suite check at L184 lists `tenant_id` as an attribution source, and at L444-447 requires `tenant_id` in the bindings yaml. The helper templates correctly drop it from the wire, but the discovery side still demands the binding. If a customer correctly omits it (since FR-3 says SDK derives server-side), the planner refuses-to-run with "missing required key tenant_id." **Observable failure**: customer-facing inconsistency — discovery says "tenant_id required" while runtime says "tenant_id ignored."

#### Phase 1f self-review rounds

##### Round 1
- **Intentions**: 1 edit. The original-intention paragraph initially missed the `cost_event_direct_emit` capability-flag mechanism that branched the helper Jinja template at v0.2; added it explicitly so the contract change reads "AND the flag-branched dual-transport is gone."
- **Success criteria**: 1 added (criterion #7 about smoke-suite honesty). Initially missed because the operator was thinking about runtime behavior, but the negative-leakage assertions are themselves a contract — if the smoke regresses, a future regression won't be caught. 1 rewritten (criterion #4: original wording "snapshot.yaml is correct" was vague; rewrote to specify `unified_ingest_present` gates the run).
- **Challenges**: 1 sharpened (#3 multi-word capability — initially "convention might break"; sharpened to name the specific `.capitalize()` failure mode and the specific class name pattern). 1 added (#5 customer-id "unknown" fallback — discovered by re-reading the FastAPI template's L41 and noticing the fallback chain bottoms out at a literal string).
- Re-read of diff after edits: confirmed alignment.

##### Round 2
- **Intentions**: no edits.
- **Success criteria**: 1 added (criterion #8 about SKILL.md operational docs matching v0.3 reality — discovered while writing the introspector challenge; an agent reading stale docs is a real-world failure mode the smoke can't catch).
- **Challenges**: 1 added (#6 about Phase 1.6 still requiring `tenant_id` — discovered while writing criterion #8; this is a doc-vs-runtime inconsistency hiding in the codemod's discovery side that the helper migration didn't address).
- Re-read of diff after edits: confirmed alignment.
- Suspicions deferred to Phase 2:
  - The `_ts_collect_dts_text` reads every `.d.ts` under `src`. If the SDK repo ever ships a vendored `node_modules` with its own `class Moolabs` declaration (unlikely but possible for a workspace-style monorepo), the regex would find the wrong class first. Defer to reviewer.
  - The TS introspector regex `method_pat = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9]*)\s*[<(]", re.MULTILINE)` accepts both `<` (generic) and `(` (call) — but TS methods can have decorators (`@foo`) on the line above. Defer to reviewer.
  - The Go introspector is still a stub returning `[]`. The default lang list excludes Go. But if a user manually passes `--lang go`, the run aborts with CRITICAL. Defer to reviewer to flag if this is severe enough.
- Round 2 finding count: 2 (1 criterion + 1 challenge added). Per the skill: "Round 2 found substantial new items (more than 1 edit in any category)" → record this and add the operator signal to Phase 2 reviewer brief: "Self-review was still finding issues at round 2."

#### Risk map by subsystem (Phase 1g)

- **`sdk_snapshot.py` introspection layer** — highest blast radius. If wrong, every customer run either aborts (fail-closed, acceptable) or proceeds against the wrong SDK contract (fail-open, dangerous). Specific risks: MODE A → MODE B fallthrough, capitalize() convention break, multi-word capability handling, duplicate `client.events` emission, hand-rolled YAML parser silent fall-through.
- **Helper Jinja templates (3)** — render directly into customer code that ships. Wrong helper = wrong production billing. Specific risks: fallback chain producing `"unknown"` customer_id, env-gated decision point duplicated or missing, gofmt-clean Go template that nonetheless contains a logic bug (gofmt only checks syntax).
- **Framework callsite templates (6)** — render per-callsite inserts; mistakes silently produce wrong billing data. Specific risks: sibling-pair accidentally emits two calls (would regress to v0.2 shape), `_moolabs_event_id` declared but not referenced (orphan var), unawaited TS callsites (smoke catches cost but NOT usage or sibling-pair).
- **Phase 7 smoke assertions** — guard against regression but only test what the assertions specifically check. Specific risks: missing await checks for usage-only and sibling-pair TS patterns, only `tenant_id=` colon check (no `tenant_id =` assignment check), AST-compile wrap inside `def _fn()` might mask module-level constructs.
- **Operational SKILL.md prose** — an agent reading these treats them as current truth. Risk: any v0.2 mention not yet polished could mislead a future codemod execution. Confidence is moderate after the polish commit (2988ea1) but uncertainty remains where v0.2 mentions are legitimately historical (Decision 3 supersede chain in v1-decisions-log.md).
- **Discovery/Phase 1.6 surface** — not touched by this PR, but the migration broke its alignment with FR-3 (it still requires tenant_id binding). Risk: customer-facing inconsistency between discovery and helper contracts.

#### Verification commands used

_To be filled as the loop progresses._

#### Verification commands used

- `bash skills/cost-billing/scripts/test-suite.sh` — the suite-wide smoke (60 checks)
- `python3 -c "from sdk_snapshot import parse_signed_yaml; ..."` — empirical test of parse_signed_yaml against 2/4/6-space YAML inputs
- `gh pr checks 2 --repo moolabs-hq/moo-skills` — CI verification

#### Review rounds

##### Round 1 (HEAD b7e43e6 — initial state)

Reviewer: `code-reviewer` agent with Phase 2 adversarial brief including the 8 success criteria, 6 codebase-specific challenges, Phase 1.5 codebase profile, and the operator signal "Self-review was still finding issues at round 2 — apply extra scrutiny on Pass 1."

Total raw findings: 13. Operator-adjusted severity (Phase 3a):

| Reviewer severity | Operator-adjusted severity | Count | Notes |
|---|---|---|---|
| CRITICAL | CRITICAL | 1 | tenant_id contract inversion — CONFIRMED |
| IMPORTANT | IMPORTANT | 3 | parse_signed_yaml, smoke-await, customer_id null gate — all CONFIRMED |
| IMPORTANT | MINOR | 1 | MODE B/C wrapper miss — fail-closed safety net, only error message unclear — DEMOTED |
| HIGH | MINOR | 2 | `unit=` leading-space guard fragility, SKILL.md v0.2 annotation — DEMOTED (cosmetic / very rare trigger) |
| MEDIUM | MINOR | 3 | _wrapper_class_for multi-word, events-dup latent, _ts_extract_class_block nested-brace regex — DEMOTED (latent, no current trigger) |
| MINOR | NIT | 3 | structlog event name, placeholder commit_sha, stale entry_base keys — DEMOTED |

CONFIRMED counts after operator severity adjustment: CRIT=1, IMP=3, MIN=6, NIT=3.

**Pass 1 contract verification per success criterion:**
1. Helpers call v0.3 singular methods → **PASS** (all three helpers verified)
2. Sibling-pair makes one call → **PASS**
3. FR-3 enforced in rendered output → **PARTIAL — discovered the planner/smoke contract inversion (CRITICAL); rendered output IS clean**
4. `unified_ingest_present` gates correctly → **PASS**
5. Introspector discovers all three lanes against real v0.3 → **PASS** for MODE A; **PARTIAL** for MODE B/C fallback (demoted MINOR)
6. Env-gated error handling wired → **PASS**
7. Smoke catches v0.2 regression → **PARTIAL — discovered the await coverage gap (IMPORTANT); other negative-leakage assertions hold**
8. Operational SKILL.md matches v0.3 reality → **PARTIAL — one stale annotation at instrument/SKILL.md:197 (demoted MINOR)**

**Challenge verification:**
1. MODE A→B fallthrough → **UNHANDLED but safety-net'd** (demoted to MINOR — fail-closed)
2. parse_signed_yaml 2-space → **UNHANDLED → CONFIRMED IMPORTANT, FIXED**
3. _wrapper_class_for multi-word → **LATENT** (demoted MINOR, no current trigger)
4. Python introspector dedup → **LATENT** (demoted MINOR)
5. customer_id null fallback → **PARTIALLY HANDLED → CONFIRMED IMPORTANT, FIXED**
6. Attribution-bindings tenant_id contradiction → **UNHANDLED → CONFIRMED CRITICAL, FIXED**

CI status round 1: no checks configured for this PR (verified via `gh pr checks 2`).

**Operator spot-check round 1**: I personally verified Challenge #2 (parse_signed_yaml indent fragility) by writing an empirical Python harness that calls `parse_signed_yaml` against 2-space, 4-space, and 6/8-space inputs. The harness confirmed both 2-space AND 4-space YAML return empty config against the original parser, while 6/8 (the only format the parser accepts) returns correctly. Read at file `skills/cost-billing/instrument/scripts/sdk_snapshot.py:647-671`.

Low-only streak after round 1: **0** (confirmed CRIT and IMP in this round).

##### Round 2 (HEAD 7d301b4 — after Round 1 fixes)

Reviewer: `code-reviewer` agent with re-review brief naming the four Round 1 fix commits and asking for (a) fix verification, (b) hunt for new bugs introduced by fixes, (c) re-check accepted remaining risks, (d) re-run Pass 1 contract verification, (e) re-run Pass 2 generic lenses on the freshest delta.

Reviewer findings: 4 raw NITs.

| Reviewer severity | Operator-adjusted | Count | Disposition |
|---|---|---|---|
| NIT | NIT | 1 | SKILL.md:417 pattern-selection table stale OTel-span text — actionable (FIXED in round 2) |
| NIT | NIT | 1 | Smoke await-loop under-reports multi-failure case (`break` after first hit) — cosmetic report-style observation, ACCEPTED |
| NIT | NIT | 1 | parse_signed_yaml degenerate-input: parent-level comment at the block-indent — accepted (warning fires loudly) |
| NIT | NIT | 1 | parse_signed_yaml tab-indent — accepted (YAML 1.2 forbids; warning fires) |

CONFIRMED counts: CRIT=0, IMP=0, MIN=0, NIT=4.

Pass 1 contract: all 8 criteria PASS, with #8 marked "mostly-pass" because of the L417 finding which was then fixed.

Challenge verification: all 6 challenges either FIXED (round 1: #2, #5, #6) or LATENT-and-accepted (#1 fail-closed, #3 #4 no current trigger).

CI status round 2: no checks configured (verified via `gh pr checks 2`).

Operator spot-check round 2: I personally read `instrument/SKILL.md` lines 410-490 to verify the L417 fix. During the read I caught a SECOND stale cell in the same row that the reviewer missed (sibling-pair was still labeled as two-call v0.2 shape `emit_cost_event_safe(...) + emit_usage_event_safe(...)`). Both cells were fixed in commit `82b05a3`.

Low-only streak after round 2: **1** (no confirmed CRIT/IMP/MIN; CI no-checks-verified).

##### Round 3 (HEAD 82b05a3 — after round 2 fix, exit-gate eligible)

Reviewer: `code-reviewer` agent with exit-gate brief focusing on (a) verifying the 82b05a3 fix, (b) hunting NEW bugs introduced by it, (c) confirming round 2 latent risks unchanged, (d) final scan for missed v0.2 references.

Reviewer findings: 2 raw NITs.

| Reviewer severity | Operator-adjusted | Count | Disposition |
|---|---|---|---|
| NIT | NIT | 1 | SKILL.md L343 example tasks.yaml + L409 Phase 2b prose — helper_import strings missing `emit_event_safe` (same incomplete-polish pattern as the L417 fix). Actionable (FIXED in round 3 polish). |
| NIT | NIT | 1 | SKILL.md L197 sample snapshot header — "verified against moolabs-py@v0.2.0-rc9" with v0.3 fields in the body. Carried-from-round-2 latent. Also FIXED in round 3 polish. |

CONFIRMED counts: CRIT=0, IMP=0, MIN=0, NIT=2.

Pass 1 contract: all 8 criteria PASS.

Challenge verification: unchanged from round 2; all FIXED or accepted-latent.

V0.2-reference final scan (reviewer): 0 hits in operational docs across all 7 patterns checked (`client.usage.ingest_events`, `client.cost.ingest_events_batch`, `cost_event_direct_emit`, `usage_event_id` as current wire field, `tenant_id` as wire field, two-call sibling-pair language, OTel-span fallback presented as current). All historical references (decisions log, gaps tracker, PRD design doc) are correctly labeled and dated.

CI status round 3: no checks configured (verified).

Operator spot-check round 3: I personally read `instrument/SKILL.md` lines 335-348 and 405-415 to verify the helper_import findings against the actual file. Confirmed both NIT findings are accurate. Both were fixed in commit `67a06aa`.

Low-only streak after round 3: **2** — **EXIT GATE PASSES**.

#### Bugs fixed

| Commit | Severity (operator-adjusted) | Description |
|---|---|---|
| `c47cb51` | CRITICAL | Planner gate honors FR-3 (drop tenant_id from required) + catches null-source bindings (Phase 1d criterion #3, Challenge #5, #6) |
| `89d973b` | IMPORTANT | Smoke await coverage extended to `emitEventSafe` and `emitUsageEventSafe` (Phase 1d criterion #7) |
| `e47a654` | IMPORTANT | `parse_signed_yaml` indent-tolerant + warns on empty parse (Challenge #2) |
| `7d301b4` | MINOR (Phase 4a sibling) | Drop tenant_id from `_attribution_keys_for` legacy defaults — FR-3 sibling consistency |
| `82b05a3` | NIT | SKILL.md L417 pattern-selection table — both sibling-pair and cost-only cells corrected to v0.3 (operator spot-check caught the sibling-pair issue the reviewer missed) |
| `67a06aa` | NIT (×3) | SKILL.md L343 + L409 helper_import strings + L197 sample-snapshot header — final v0.3 polish stragglers from the prior docs commit |

#### Findings rejected (false positives)

One reviewer-finding rejected across the three rounds:

- Round 1 reviewer flagged `shared/sdk-surface-reference.md:212` as a stale v0.2.0-rc9 annotation. **Rejected** — that line is inside a "Known upstream SDK issues (as of 2026-05-25)" historical section. The dating is intentional historical record, not a current-truth claim.

No other findings were rejected. Severity downgrades (HIGH→MINOR, MEDIUM→MINOR) are recorded in the per-round tables; those are grade adjustments, not rejections.

#### Defensive hardening applied

None applied. Considered (round 4b):
- Clarifying the MODE B/C error message — DEFERRED per skill's "behavior correct under failure" bar (the fail-closed gate already prevents the bad outcome).
- Hardening the MODE B/C fallback to ALSO call `_dx_namespace_methods` — DEFERRED for the same reason; would be net new resilience capability, not just keeping existing behavior correct.

#### Remaining risks (accepted non-blocking)

All NIT-level, all confirmed via Round 1-3 operator spot-checks:

1. **MODE A → MODE B/C fallback wrapper-method miss** (`sdk_snapshot.py:329+`). If `_dx_routing.py` is unparseable, MODE B/C runs and doesn't merge wrapper methods — `unified_ingest_present=False` → CRITICAL exit (fail-closed). Error message names "missing lanes" rather than "parser failure"; operator may misdiagnose. **Reason accepted**: safety net works; diagnostic improvement only. Track as follow-up.
2. **`_wrapper_class_for` `.capitalize()` breaks on multi-word capabilities** (`sdk_snapshot.py:281-282`). Currently latent — no multi-word capability in CAPABILITY_MAP. **Reason accepted**: future-proof; trivial PascalCase converter fix when needed.
3. **`introspect_python` lacks `seen_paths` guard if `events` lands in CAPABILITY_MAP** (`sdk_snapshot.py:322-328`). Currently latent — events is special-cased via @property on Moolabs. **Reason accepted**: latent until SDK schema changes.
4. **`_ts_extract_class_block` regex may misparse nested-brace generics** (`sdk_snapshot.py:430-448`). Rare in practice; no observed instance. **Reason accepted**: not currently triggered.
5. **Smoke await loop reports under-counts** when multiple TS helpers are un-awaited in one render (`test-suite.sh` round-2 NIT). **Reason accepted**: cosmetic — build still goes red, first offender is named.
6. **`parse_signed_yaml` degenerate-input edge cases**: parent-level comment at block-indent OR tab-indented YAML → empty parse. **Reason accepted**: the new loud warning makes it operator-visible; both inputs are YAML-spec-malformed.

#### Status

`ready-for-human`

Exit gate: **PASS**. Streak = 2 consecutive LOW-only rounds with CI verified. Loop closed at HEAD `67a06aa`.

#### Bugs fixed

| Commit | Severity | Description |
|---|---|---|
| `c47cb51` | CRITICAL | Planner gate honors FR-3 (drop tenant_id from required) + catches null-source bindings (Phase 1d criterion #3, Challenge #5, #6) |
| `89d973b` | IMPORTANT | Smoke await coverage extended to `emitEventSafe` and `emitUsageEventSafe` (Phase 1d criterion #7) |
| `e47a654` | IMPORTANT | `parse_signed_yaml` indent-tolerant + warns on empty parse (Challenge #2) |
| `7d301b4` | MINOR (Phase 4a sibling) | Drop tenant_id from `_attribution_keys_for` legacy defaults — FR-3 sibling consistency |

#### Findings rejected (false positives)

None in round 1. All reviewer findings were either accepted-as-flagged or operator-demoted by severity (recorded in the Round 1 table above; the demotions are about the *grade*, not about rejecting the finding).

The reviewer's mention of `shared/sdk-surface-reference.md:212` as a stale v0.2.0-rc9 annotation is **rejected** — that line is explicitly inside a "Known upstream SDK issues (as of 2026-05-25)" historical section. The dating is intentional historical record.

#### Defensive hardening applied

None in round 1. Considered: clarifying the MODE B/C error message; deferred per skill's "behavior correct under failure" bar (the fail-closed gate already prevents the bad outcome; only diagnostic improvement would result).

#### Remaining risks (accepted non-blocking)

_To be finalized at exit._ Provisional list:

- MODE A → MODE B/C fallback misses wrapper methods (sdk_snapshot.py:329+). Fail-closed safety net (unified_ingest_present=False → CRITICAL). Error message doesn't name parser failure as the root cause.
- `_wrapper_class_for` capitalize() breaks on future multi-word CAPABILITY_MAP keys. Latent (no current trigger).
- Python introspector lacks dedup guard if `events` ever lands in CAPABILITY_MAP. Latent.
- TS introspector `_ts_extract_class_block` may misparse nested-brace generics. Rare in practice.
- SKILL.md:197 stale v0.2.0-rc9 annotation in an example block that otherwise describes v0.3. Cosmetic doc inconsistency.

#### Status

`round-2-pending`

## Final summary

PR #2 — `ready-for-human` after 3 review rounds, 6 fix commits, head SHA `67a06aa`.

### Loop trajectory

| Round | Head SHA | Findings (raw → confirmed/operator-graded) | Streak | CI |
|---|---|---|---|---|
| 1 | b7e43e6 | 13 raw → CRIT=1, IMP=3, MIN=6, NIT=3 | 0 | no checks (verified) |
| 2 | 7d301b4 | 4 raw → NIT=4 | 1 | no checks (verified) |
| 3 | 82b05a3 | 2 raw → NIT=2 | **2 (EXIT GATE)** | no checks (verified) |

Final HEAD `67a06aa` includes the round-3 polish commit on top of round 3's review.

### Fix commits (6, oldest first)
- `c47cb51` — CRITICAL: planner gate honors FR-3 + null-source detection
- `89d973b` — IMPORTANT: smoke await coverage for all three TS helpers
- `e47a654` — IMPORTANT: parse_signed_yaml indent-tolerant + warning
- `7d301b4` — MINOR: drop tenant_id from legacy attribution-key defaults
- `82b05a3` — NIT: SKILL.md L417 pattern-selection table v0.3 polish
- `67a06aa` — NIT×3: SKILL.md L343/L409/L197 final v0.3 polish stragglers

### Verification
- Smoke (`bash skills/cost-billing/scripts/test-suite.sh`): **60/60 PASS** on every commit, every round.
- Empirical `parse_signed_yaml` test (operator-written harness): all four indent shapes parse correctly post-fix (2-space, 4-space, legacy 6/8-space, empty-block).
- CI: no checks configured — verified explicitly each round, not silently skipped.

### Remaining risks (accepted non-blocking — all NIT)
6 items documented above. Each fail-closed, latent, or operator-visible via warning. None ship-blocking.

Merge status: **NOT MERGED — awaiting explicit user permission.**
