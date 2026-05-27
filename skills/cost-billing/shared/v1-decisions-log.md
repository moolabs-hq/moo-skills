# V1 decisions log — the 11 §10 decisions resolved for the suite

**Status:** v1 defaults — every entry is a defensible call, not a final commitment. Each is marked with the originating §10 number from the requirements doc. **HLD revisits these.**

---

## Decision matrix

| # | Topic | v1 default | Rationale (1 line) | Revisit at |
|---|---|---|---|---|
| 1 | "Optimal manner" optimization target | **Coverage-first** (instrument every emission site detected with confidence ≥ MEDIUM). Latency secondary. | Framework's stated purpose is "cut dev time" — missing emission sites costs the most time downstream. | HLD; revisit if customer reports >5% latency regression on hot paths. |
| 2 | Codemod v1 language scope | **Python + TypeScript v1.** Go in v1.5. Java in v2. | All three GitHub SDKs already ship (`moolabs-py`/`-ts`/`-go`); v1 prioritizes the two most-requested. Go is mechanically similar — easy v1.5 add. | After v1 ships to 3 customers. |
| 3 | Cost-event SDK endpoint status | **There is no separate "acute SDK" — the Moolabs SDK is unified.** The unified `Moolabs` client already exposes `client.cls.*` + `client.meter.*`; cost-event ingestion will land on the SAME client when the platform team ships it (final method path TBD — likely `client.meter.cost.ingest_events()` or sibling namespace). v1 emits cost via **dual transport** (REVISED 2026-05-25 after an early integration run): preferred = OTel span attributes (when a recording span exists); recovery rail = structured log line (when no recording span) preserved by the customer's log pipeline. Both events (cost AND usage) follow the same never-drop contract — usage uses SDK-then-log, cost uses span-then-log. Subscription-customer cost-only paths still get `# TODO: blocked on unified SDK's cost-event endpoint` annotations until that endpoint ships, at which point the helper's PRIMARY cost transport swaps to direct SDK emission on the same `get_client()` singleton (call sites unchanged; recovery-rail log stays). | Per `sdk-surface-reference.md` — unified SDK exposes `cls`+`meter` namespaces only today. Original v1 default (span-only) would silently drop ~90% of cost signal under head-sampling (real production default), 100% under non-traced background workers / dev / CI; the never-drop contract makes the attribution engine actually trustworthy. The "acute SDK" framing was wrong — there is one SDK, multiple endpoints. | When unified SDK adds the cost-event endpoint (same `Moolabs` client). |
| 4 | Codemod hot-path insert default | **Option B — blocking insert, document latency in PR.** Per Doc 3 §7.1 lean. | The customer's engineer decides background-wrap policy; codemod stays conservative. | If 3+ customers report needing async. |
| 5 | Framework invocation surface + permission model | **CLI v1, local-only.** Skill runs on integrator's machine. Customer code never leaves their environment. v1.5: GitHub Action. v2: IDE extension. | Trust-tone — first-impression is "this reads your code on your machine." No SaaS dependency for code reading. | When usage warrants hosted (>10 customers). |
| 6 | Catalog miss behavior | **Always surface as "unclassifiable call site" — never silently skip.** Reviewable as cell ④ findings. | Silent skip = silent wrongness. Surface forces engineer/PM to decide: add to catalog / mark as non-billable / mark as future. | HLD §6.5 #26. |
| 7 | Three-role review UX surface (v1) | **Markdown PR + YAML inventories.** A static HTML preview generator for non-engineer reviewers (CFO/PM read HTML, engineer reviews PR directly). | Doc 3 §3.6 — markdown PR is v1; web UI is v2. HTML preview gives CFO/PM a readable rendering without requiring git literacy. | v2 web UI design. |
| 8 | Skill R reviewer-model + iteration cap + severity rubric | **Cross-model default** (run reviewer with a different model than codegen — e.g., codegen=opus, reviewer=sonnet or claude-via-different-model). **Hard cap = 5 rounds.** **Severity = CRITICAL/HIGH/MEDIUM/LOW; stop criterion = no CRITICAL or HIGH remaining.** | Doc 3 §5.1, §5.2, §5.4, §5.5. Same-model self-review is a known weak spot; 5 rounds bounds cost; "no HIGH+" prevents indefinite Phase 5 loops. | After 10 invocations; tune cap based on real iteration counts. |
| 9 | Skill C customer-facing exposure | **REMOVED FROM SUITE.** Skill C (attribution-engine reconciliation harness — validates the Moolabs attribution-engine's algorithm ladder against real customer cloud bills) is Moolabs-engineering-internal infrastructure. It has no business running in a customer environment and was removed from this customer-portable suite. Tracked separately by the Moolabs platform team. | Doc 3 Appendix B Q5 + the skill content itself (paths to Moolabs platform internals, attribution_engine.py refs, ACUTE-Tier nomenclature) made clear it didn't belong here. | When Moolabs decides where the engineering-internal version lives (separate repo or moolabs-internal skills namespace). |
| 10 | Cell ③ severity / routing | **Always surface — no monetary threshold v1.** PM decides per-row in three-role review. | Doc 2 §3.2 — auto-thresholding silently hides spend that PM may care about for strategic reasons (compliance, customer optics). Always-show, PM filters. | Once cell ③ list size becomes ergonomically painful (>50 entries typical). |
| 11 | Skill R vs. Skill 3 v1-vs-v2 split | **Both ship v1.** Skill R = adversarial gate per pipeline run (one-shot). Skill 3 = continuous drift watch (per-PR CI). Not redundant — different cadence, different scope. | Doc 3 §4.2 instinct confirmed by requirements doc §10 #11 "Skill R essential in v1, Skill 3 v2 if needed" — overridden because Skill 3 is mechanically cheap once Skill A's code-graph is reusable. | If Skill 3 false-positive rate exceeds 10% — fold into Skill R review. |

---

## Cross-cutting decisions (carried from §6 gaps to support v1)

These came from §6.4a (three-role review gaps surfaced 2026-05-19) and §6.4b (framework-on-unknown-repo gaps). They are not in §10 but are needed for v1 to ship.

| Gap | v1 default | Why |
|---|---|---|
| §6.4a #19j Disagreement resolution | **Per-dimension final say: CFO on price, PM on output↔input mapping, engineer on `file:line`.** Disagreement on overlap = re-propose with all three view diffs. | Matches "three lenses on one graph" model — each role owns its lens. |
| §6.4a #19k Async review | **Yes — review state persists; later changes re-open earlier-confirmed views.** | Real teams don't sit together. |
| §6.4a #19l Many-to-many output↔input | **Schema supports M:N natively** (`output-input-map.yaml` is a graph, not paired lists). Attribution weight per edge defaults to equal split; PM overrides. | Required for correctness — a shared GPT-4 call across two features is the canonical case. |
| §6.4a #19m Fair-usage data placement | **Carried in `usage-events-inventory.yaml`** (CFO-facing metadata block). Survives Skill 3 drift. | First-class CFO data; downstream Moolabs metering backend config reads from here. |
| §6.4a #19n PM mapping persistence | **Match by `workflow_id`** (Doc 3 §3.8). Re-derive `workflow_id` per entry; renames preserved if `workflow_id` stable. | Single mechanism; doesn't compete with `file:line` matching. |
| §6.4a #19o Linkage confidence | **Each edge in `output-input-map.yaml` carries its own confidence**, separate from per-entry confidence. | Linkage is a claim Skill A makes; PM signal needs its own truth scale. |
| §6.4b #19b Repo-shape discovery | **Poly first: discover service boundaries via manifest files (`go.mod`, `package.json`, `pyproject.toml`); fall back to monorepo treating the root as one service.** | Simplest correct default. |
| §6.4b #19c Language detection | **Manifest-first** (`pyproject.toml` → Python; `package.json` → TS/JS; `go.mod` → Go); fall back to file-extension dominance. Multi-language services: scan each language independently, produce per-language inventories merged at the service level. | Manifest is canonical; extension is fallback. |
| §6.4b #19d Existing-SDK detection | **Detect existing `moolabs` import; if present, ask engineer "upgrade in place" or "fresh re-instrument".** | Don't silently re-instrument. |
| §6.4b #19e Read/write permissions | **Read = entire repo; write = a single new branch + PR. No file outside the branch is written.** Tested via dry-run mode (default ON for first invocation). | Standard codemod hygiene. |
| §6.4b #19g Greenfield vs. brownfield | **Branch on existing-SDK / existing-OTel detection in Skill A.** Brownfield default = extend existing spans with `moolabs.*` attributes. Greenfield default = introduce Moolabs SDK + OTel. | Doc 1 §13 "pipeline doesn't branch" is about discovery; codemod insertion default explicitly does branch. |
| §6.4b #19h Build-system integration | **Codemod does NOT run customer build commands.** PR carries a "to run before merge: install SDK + run tests" note. The exact SDK install command per language comes from `04-final.signed.yaml > integration.sdk_package_install` (engineer captures during team-engineer bootstrap Q16; default = latest GitHub release tag via git URL since SDKs aren't on public registries as of 2026-05-25). See `cost-billing-shared/sdk-surface-reference.md` §"Install". | Reproducibility for review. |
| §6.5 #28 Versioning of chargeability map | **Match historic→current by `workflow_id`. Rename/merge/split semantics: rename = `workflow_id` preserved; split = parent `workflow_id` retired, two child entries with new IDs reference parent; merge = two parent `workflow_id`s retired, one child references both.** | Codifies §6.4a #19n into rename/merge/split semantics. |
| §6.5 #29 Multi-tenant / multi-environment | **One chargeability map per environment (dev/staging/prod); cross-environment consistency check warns but does not block.** | Environments diverge for real reasons (test vendors in dev); cross-env enforcement is too brittle. |
| §6.5 #30 Cell ③ + Tier 5 reconciliation | **In Skill C scope — Phase 1 (Moolabs's own bill).** | Already implied by Skill C's reconciliation purpose; making it explicit. |

---

## What's still open after v1

These remain open and should drive HLD agenda:

- §6.2 #2 Permission model details (IAM role / cross-account) — Skill B specifics, not blocking for Skill A/2/3.
- §6.2 #5 Tag-schema alignment with customer-existing FinOps schema — defer until first customer with conflict.
- §6.3 #11 Local-only run model for Skill C — aggregate-metrics list needs codification.
- §6.4 #21 Revert / rollback model for codemod PRs — initial answer = `git revert`. Codemod will not carry "regenerate-removing-feature-X" capability in v1.
- §6.4 #22 Coexistence with existing OpenLLMetry / Helicone — Skill A detects; Skill 2 extends existing spans (per §6.4b #19g brownfield branch).
- §6.4 #23 Idempotency-key derivation policy — v1 heuristic: first path parameter that looks like an ID + handler name + epoch second; ask integrator for `usage_only` cases. Doc 3 §1.2 remains unresolved at the platform level.

---

## Maintenance rules — lessons from v1 wire-format iterations

These are **process rules**, not decisions about specific defaults. They exist because the same class of mistake recurred three times during v1 implementation and each time required a forward-fix commit. Future maintainers touching helper templates, attribution envelopes, or cost/usage emission routing MUST follow these before pushing.

### MR-1: SDK Pydantic types don't tell you what the platform stores

The cost/usage SDK's Pydantic models tell you what passes **client-side validation**. They do NOT tell you:
- Which fields are routed by ACUTE's mapping engine vs. stored as-is
- Which columns expect specific types in the database (UUID vs. slug vs. free-form string)
- Which fields drive analytics joins/MVs vs. are stored for audit only
- Which fields are extracted from `tags` / `otel_attributes` / `provider_metadata` vs. read at the top level

**Before adding, removing, or re-routing any wire-format field in the helper templates**, read:

1. `services/moo-acute/app/services/cost_enricher.py` — which fields the cost enricher reads from incoming events; how it maps `tags["X"]` → storage columns; which fields feed `usage_event_log` vs. `cost_events`.
2. `services/moo-acute/app/services/clickhouse.py` — which fields are first-class analytics columns vs. dimension dictionaries; which materialized views group by which fields.
3. `services/moo-acute/app/services/mapping_engine.py` — what mapping rules can extract (OTel attrs, headers, Kafka properties, static values) and which target fields they support.
4. `services/moo-acute/app/api/v1/cost/analytics/*` — which `feature_key` / `customer_id` / `meter_slug` / `feature_id` slices the analytics endpoints actually return — those endpoints are the consumer contract that drives helper-emit decisions.

If you cannot find the field's storage destination after consulting all four, the field does not belong on the wire envelope — open a platform-team question first.

### MR-2: Read the storage model before redesigning wire routing

For any wire-format decision (which field is top-level, which rides in a dict, which is required, which is omitted-when-null), confirm:
- **Storage column type**: UUID columns reject non-UUID strings; slug columns reject UUIDs that look like slugs.
- **Whether the field is JOIN-able**: top-level fields support direct FK joins; dict-extracted fields require mapping rules to run first.
- **Which materialized view groups by it**: MVs are pre-aggregated; getting the field there means it's queryable in seconds, not minutes.

**Example failure mode that justified this rule** (commit `b87db37`, 2026-05-26): the cost SDK declared `feature_id: Optional[StrictStr]`. The helper template emitted `feature_id="recommendation"` (a slug derived from workflow_id). Wire validation passed. But ACUTE's `cost_events.feature_id` is a UUID column — the slug would have been rejected at insert OR stored as garbage that would never JOIN against the Feature catalog. Fix: route slugs via `tags["feature_key"]` (where ACUTE's mapping engine extracts them); reserve top-level `feature_id` for actual UUIDs.

### MR-3: Three-commit pattern as the warning signal

Each of these three commits was a forward-fix of a wire-format mistake that passed all SDK type checks but failed against the actual platform behavior:

| Commit | What I assumed | What I missed |
|---|---|---|
| `0552ad0` | "acute SDK is a separate roadmap item not yet shipped" | The unified SDK already routes `client.cost.*` to ACUTE via `_dx_routing.CAPABILITY_MAP` — I didn't read the routing table |
| `0cc98e4` | "`tenant_id` collides with Moolabs internal term; drop it" | `CostEventIngest.tenant_id: StrictStr` (required, not Optional) — I didn't run the helper end-to-end against the SDK's validation |
| `b87db37` | "feature slug binds to `feature_id` wire field" | `cost_events.feature_id` is a UUID column; slugs route via `tags["feature_key"]` per ACUTE's mapping engine — I didn't read `cost_enricher.py` |

Every one of these passed `python -m py_compile` on the rendered template and 36/36 render smoke-test assertions. **None of those checks tells you the field will be stored, joined, or analytics-aggregated correctly.** The smoke tests verify the helper-call shape; the platform's storage and mapping behavior is what determines whether the emitted event is actually useful downstream.

If you're about to push a fourth commit in this category, stop and re-run MR-1 first.

---

## How to update this log

When HLD revisits a decision, update the row's "Revisit at" to "REVISITED" and append a new dated entry with the new default + rationale. **Never delete old rows.** Audit trail.

```
| 4 | Codemod hot-path insert default | REVISITED 2026-06-15 → Option A (background-wrap default) | 3 customers reported latency regression at p99. | continuing |
```
