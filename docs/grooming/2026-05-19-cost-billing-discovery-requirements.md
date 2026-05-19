# Cost + Billing Discovery & Instrumentation Skills — Requirements Grooming

> **Status:** First grooming step in the chain (requirements → HLD → contracts → BE/FE → tasks).
> **Date:** 2026-05-19
> **Author:** kritivasrocks (grooming session)
> **Strictly out of scope at this step:** implementation, schemas, code, timelines, task breakdown.

---

## 0. Goal & purpose (clarified by user 2026-05-19)

**Primary goal.** A skill family that helps customers implement Moolabs's generated SDKs in their own repo such that **ingest events flow correctly and optimally** for both **cost** and **usage** paths.

**Why this exists.** The skills are a **framework that cuts time** for the customer's engineering team. The customer's repo is unknown territory — arbitrary languages, frameworks, architectures, code styles. Without the framework, a senior engineer spends days-to-weeks grep-ing for handlers, conferring with PM on what to bill, hand-wiring trace context, and gets it wrong. The framework collapses that into hours-to-days of high-confidence guided work.

**Two ingest-event flavors the framework must instrument.** This is the central distinction; everything else in the doc flows from it:

| Flavor | SDK surface | Lands in | Drives |
|--------|-------------|----------|--------|
| **Cost ingest event** | acute SDK namespace (cost ingest path) | moo-acute (Tier 1–4 event-grain or Tier 5 bill-grain) | Per-request cost attribution; margin computation. |
| **Usage ingest event** (also called "billing event" in Doc 1, `meter.events.*` in code) | `client.meter.events.ingest_events()` | moo-meter | Aggregation by meter rule → invoice line. |

**Both flavors originate at the same code site** in most cases (one SDK call carries both — Doc 1 §2 makes this point explicitly). The framework's central job is to identify those sites in unknown customer code, classify them correctly, and produce a codemod that wires both flavors in optimally.

**The end-state of a successful integration:**
- Every code path that should emit a usage ingest event does so, with correct quantity/lifecycle/idempotency.
- Every cost-bearing API call (vendor + infra) emits a cost ingest event with correct attribution keys (`request_id`, `trace_id`, `customer_id`, `feature_id`).
- The customer's CI catches drift on every PR (Skill 3).
- The customer's finance team has a defensible reconciliation report against their cloud bill (Skills B + C).

**Side benefits (real but secondary):** chargeability audit for PM/CFO; review-spec audit trail for compliance; per-customer-pattern confidence map. The grooming below treats these as outputs of the framework, not its purpose.

---

## 0.5 Source documents — both rough frameworks, neither is committed scope

User clarified 2026-05-19: **both source docs are rough exploratory frameworks**, not committed implementation scope. They serve complementary purposes — one for the *path*, one for the *taxonomy*.

| Doc | Role | What it provides |
|-----|------|------------------|
| [Cost/Billing Discovery & Instrumentation Skills — Design](https://docs.moolabs.com/doc/costbilling-discovery-instrumentation-skills-design-JRsb8oZxxd) (`JRsb8oZxxd`) | **Rough framework for the path taken** — methodology / journey sketch | 7-stage pipeline view (doc ingest → code graph → feature mapping → integrator review → adversarial review → codemod → drift lint). The shape of the work, not the committed shape of skills. |
| [Cost + Billing Discovery Skills — Design Doc](https://docs.moolabs.com/doc/cost-billing-discovery-skills-design-doc-uFNMOWABYB) (`uFNMOWABYB`) | **Rough framework for understanding what is what** — taxonomy / concepts sketch | Anchors the vocabulary: grain, attribution keys, 12-algorithm ladder, WAPE/Coverage gate, dogfood pattern. Proposes a 3-skill grouping (A/B/C) as one possible scoping. |
| Companion: [Cost/Billing Events Skill — Open Questions](https://docs.moolabs.com/doc/3ef884d4-399c-4022-949f-8f3591a58c09) | Open-questions index | 30+ questions, plus a §6 verified-facts block (2026-05-11 SDK audit) and §8 future-state region routing decision. |

**What is actually locked from this grooming session** (user-confirmed, used as anchor for HLD):

1. **Goal** — Framework that helps the customer's engineering team implement Moolabs's generated SDKs in their own (unknown) repo such that ingest events flow optimally. Primary value: cut developer time.
2. **Two ingest-event flavors** — Cost ingest events (→ moo-acute) AND usage ingest events (→ moo-meter). Both must be instrumented.
3. **Three primary actors with distinct cognitive lenses** — CFO (output / billing economics), PM (output ↔ input mapping), engineer (code locations). Each views the same artifact through their own lens.
4. **PM owns the output↔input linkage** — the bridge that converts a flat detection list into a chargeability graph.

**What is illustrative / exploratory** (everything else in this doc, drawn from the rough frameworks):
- The 7-stage breakdown from Doc 1 is one *path* — HLD may collapse or expand it.
- The 3-skill (A/B/C) grouping from Doc 2 is one *grouping* — HLD may regroup.
- Skill R as a separate adversarial-review skill is one *quality pattern* — HLD may absorb it into each skill rather than ship as its own thing.
- Skill 2 (codemod) and Skill 3 (drift lint) are *conceptual commitments* drawn from the path — HLD will decide whether they ship as discrete skills or are subsumed.

**No Figma artifacts** are referenced in any of the three docs. **No `app.nrev.ai` / `localhost:3000` surface** is in scope — the deliverables are YAML inventories, markdown PRs, and engineering reports. The grooming-requirements skill's UI-checklist (empty states, sort orders, hover messages, destructive-action confirmations) is largely **N/A**; the equivalent for skill design is graceful-degradation modes, hand-off contracts, and human-in-the-loop checkpoints, which are groomed below.

---

## 1. Components proposed by the rough frameworks (NOT committed scope)

This is the union of components the two rough frameworks propose. **HLD will decide the actual skill boundaries.** Treat the table below as a checklist of capabilities to cover, not a list of skills to ship as-is.

| Component | Source framework | Capability |
|-----------|------------------|------------|
| Cost + usage ingest-event discovery in code | Doc 2 §3.1; subsumes Doc 1's Skills 1A + 1B + 1C + 1D | Scan customer's unknown repo, match against Moolabs provider catalog, identify usage-event candidates via refund-test, propose output↔input linkage for PM to certify. |
| Cloud bill integration | Doc 2 §3.2 | Walk customer through AWS CUR / GCP / Azure exports; audit tag propagation; surface untagged spend ("cell ③"). |
| Codemod / instrument | Doc 1 §10 | Apply confirmed inventories into code: insert SDK calls (both cost and usage flavors), wire trace/span context, derive idempotency keys. **The framework's core dev-time-savings deliverable per the §0 goal.** |
| Drift lint | Doc 1 §11 | CI step that re-runs discovery on every PR and diffs against saved inventory; flags new/renamed/deleted endpoints. |
| Adversarial review pattern | Doc 1 §9 | Quality gate; 5-phase pattern (spec → adversarial pass → fix → robustness sweep → repeat/stop). Doc 1 ships it as a separate "Skill R" invoked 6×; HLD may absorb it into each skill instead. |
| Reconciliation validation harness | Doc 2 §3.3 | Engineering-internal; validates ACUTE's 12-algorithm ladder against real bills (Moolabs's, then friendly customers, then permanent CI). |

Together these capabilities cover the full lifecycle the §0 goal demands: discover in unknown repo → wire cloud data → instrument both event flavors → review for quality (three roles) → ship → keep correct over time → empirically validate the algorithm.

---

## 2. Anchor taxonomy (the vocabulary the requirements depend on)

These are the load-bearing definitions every downstream skill (HLD, contracts, BE/FE, tasks) must agree on.

* **End-user** — the customer's customer. The party whose activity the customer wants to bill for.
* **Customer** — an AI/SaaS startup integrating Moolabs. Three review roles inside the customer org: **engineer** (verifies code locations), **PM** (classifies meter candidates, decides billable units), **finance** (reviews cost coverage and reconciliation report).
* **Moolabs** — the platform: moo-meter (billing/invoicing), moo-acute (cost intelligence), moolabs-app/bff (gateway).
* **Ingest event** — umbrella term for any SDK-emitted event the framework instruments. Two flavors below.
* **Cost (ingest) event** — one unit of vendor/infra *spend* the customer incurred to serve one transaction (OpenAI tokens, GPU seconds, S3 PUT, bandwidth). Flows into moo-acute via Tiers 1–4 (event-grain) or Tier 5 (bill-grain). SDK surface for direct emission **does not exist today** (Doc 3 §6.3, see §6 gap #25).
* **Usage (ingest) event** — one unit of *product output* the customer charges their end-user for. Flows into moo-meter via the SDK's `client.meter.events.ingest_events()` call. Doc 1 calls these "billing events"; the customer-facing SDK and moo-meter code call them "events" / "usage events." Naming collision tracked in Doc 3 §1.7.
* **Refund test** — working definition of "one billing event": the unit at which the customer might issue a refund. **Promoted to "validated" by Doc 3 §6.4** (pressure-tested against agentic, streaming, hybrid, subscription scenarios on 2026-05-11). Subscription scenario surfaced a key edge: subscription customers emit *zero* billing events, so cost events must travel via direct acute-SDK emission rather than riding on billing events.
* **Grain** — event-grain (per-request fidelity) vs bill-grain (aggregated billed lines). Required for both per-end-user margin and reconciliation against the cloud invoice.
* **Attribution keys** — `request_id` (financial-grade, 1.0), `trace_id` (operational-grade, 0.75), `customer_id` / `feature_id`, `tags`. Cascading ladder enforced by ACUTE's `attribution_engine.py`.
* **WAPE / Coverage gate** — `WAPE < 10% AND Coverage > 80%` in ACUTE today; logical AND. The promotion gate for any attribution algorithm change.
* **Chargeability map** — the durable hierarchical YAML artifact (`inventory.yaml`) keyed by domain → product → feature → endpoint, with billing-event + cost-event mappings, confidence, and decision metadata.
* **Cell ③ finding** (Skill B) — cost that is real but cannot currently be attributed to a feature or customer (e.g., untagged S3 PUT spend on render retries). Surfaced for PM/finance review with absorb-vs-fix decision.

---

## 3. Actors and review roles (clarified by user 2026-05-19)

There are **three primary actors** in the customer's review process. Each has a distinct cognitive lens onto the same underlying artifact; the review surface is one graph projected three ways.

| Actor | Cognitive lens | What they certify | Primary view |
|-------|----------------|-------------------|--------------|
| **CFO / Finance** | **The output side** — what the customer sells. | For each usage event: what output it represents, how much it's billed at (price per chargeable unit), what its fair-usage value is. Revenue economics per chargeable unit. | Usage-events list with price, projected revenue, fair-value assessment per entry. |
| **Product Manager** | **The link between output and inputs** — the bill-of-materials. | For each output (usage event), which inputs (cost events) feed it. PM owns the mapping; PM's review converts a flat list of detected vendor calls into a structured output→inputs graph. | Output-input graph: per usage event, the set of cost events that produce it. |
| **Engineer** | **The code** — where the events go. | For each event (cost or usage), the correct `file:line`, the right framework adapter, trustworthy idempotency-key derivation, no false positives. | File-grouped inventory with confidence, trace-context source, framework adapter per entry. |

**The PM's bridge role is load-bearing.** Without the PM's certification of which cost events feed which usage event, the artifact is just two parallel lists — useful for engineers but not for finance. The output→input graph IS the chargeability map. Skill A's primary output must support this projection natively, not as a post-hoc join.

**Secondary readers** (real but downstream):
- **Customer's audit / compliance** — dated review specs as audit trail; verification commands per fix; residual-risk acceptance log.
- **ACUTE / moo-meter engineering (internal to Moolabs)** — Skill C's WAPE/Coverage reports per PR; algorithm-regression detection.

---

## 4. Functional requirements per capability

> The sub-sections below preserve the rough frameworks' skill naming (Skill A / B / 2 / 3 / R / C) for traceability to source docs. HLD may regroup these capabilities into different skill boundaries.

### 4.1 Skill A — Cost + usage ingest-event discovery

**Primary outcome.** Identify where in the customer's unknown codebase **cost ingest events** and **usage ingest events** should be emitted. Produce two reviewable inventories that drive the codemod (Skill 2). Discovery is a means; the codemod is the end.

**Required inputs (richness varies; pipeline does not branch):**
- Customer source code (filesystem-readable for AST).
- Moolabs-maintained provider catalog (versioned, central). Determines deterministic cost-event matches.
- Optional: customer's product docs (Docusaurus / Mintlify / GitBook / Notion / GitHub `/docs` / OpenAPI / wiki / PDF).
- Optional: customer's pricing-page URL (signal for likely meter candidates).
- Optional: existing OTel / OpenLLMetry / Helicone / Langfuse / Moolabs SDK install (detected).

**Required outputs:**
- `cost-events-inventory.yaml` — deterministic, mostly auto-confirmed. One entry per detected vendor call site = a candidate cost-ingest-event emission point (an **input** in PM's terms).
- `usage-events-inventory.yaml` (was: `meter-candidates-inventory.yaml`) — semantic, requires three-role review. One entry per terminal-event candidate = a candidate usage-ingest-event emission point (an **output** in CFO's terms).
- **`output-input-map.yaml`** — the linkage artifact. For each entry in `usage-events-inventory.yaml`, the set of `cost-events-inventory.yaml` entries that feed it. **PM owns this mapping**; Skill A proposes; PM certifies. This is the chargeability map's natural form.
- Each entry carries: `file:line`, vendor / operation, standard event shape, refund-grouping key, chargeable quantity options, lifecycle states (succeeded / failed / cancelled / partial-stream-collapse), confidence, recommended attribution method (OpenLLMetry vs Helicone vs Langfuse vs direct Moolabs SDK), rate source.
- Each usage-event entry carries CFO-facing metadata: proposed billed unit (per token / per minute / per render / per seat), proposed unit price, fair-usage threshold (if applicable), projected monthly revenue.
- Each cost-event entry must declare whether it can ride along on a sibling usage event (shared call-site) or requires direct cost-only emission (subscription-customer pattern, no usage event fires). Direct cost-only emission requires the acute SDK — see §6 gap #25.

**Required behaviors:**
- AST scan must be **deterministic** for catalog-matched vendor calls (e.g., `openai.chat.completions.create`).
- Discard-pattern filter must drop SDK internals / retry loops / auth refreshes / health checks via an allowlist on required attribute presence (e.g., `gen_ai.usage.input_tokens` must be set).
- Terminal-event heuristic must apply auxiliary signals: verb pattern (`_completed`, `_delivered`, `_returned`), `span.kind=server` parent, pricing-page intersection.
- Trace-cluster analysis must run *if* telemetry is available (group cost events by `trace_id` to project per-candidate monthly cost).
- Doc-ingest must produce a hierarchical doc-tree (domain → product → feature → workflow) when docs exist; degrade gracefully to OpenAPI tags + READMEs + framework registries when they don't.
- Mapping from doc-tree to code (Doc 1's Skill 1C) **must assemble full context before fan-out** — the parent must NOT spawn one subagent per leaf without first holding doc-tree + code-graph + framework conventions + telemetry signals in one model.
- **Three-role review surface must project the same underlying graph three ways:**
  - **CFO view** — usage-events list with proposed billed unit, price, projected revenue, fair-usage notes. Question per row: "Is this the right billed unit at the right price?"
  - **PM view** — output→input graph editor. For each usage event, the proposed set of cost events that feed it; PM accepts / rejects / re-links. Question per output: "Are these the inputs that produce this output? Anything missing?"
  - **Engineer view** — file-grouped inventory with `file:line`, framework adapter, trace-context source, confidence. Question per row: "Is this the right code location? Pick another?"
- All three views must reach consensus before Skill 2 codemod runs. Disagreement at any view blocks the holistic Skill R gate.

**Required degraded modes:**
- No docs → fall back to OpenAPI tags + READMEs + framework registries (deeper review effort, same artifact shape).
- No telemetry → lean on framework registries + decorators (thinner signal, more review iterations).
- Catalog miss (long-tail vendor not in catalog) → **GAP, see §6.**

### 4.2 Skill B — Cloud bill integration

**Primary outcome.** Configured cloud-bill ingestion per detected cloud; tag-propagation report; cell ③ findings.

**Required inputs:**
- Customer's AWS / GCP / Azure account access.
- Customer's existing tag schema (or its absence).

**Required outputs per detected cloud:**
- AWS: CUR 2.0 export configured with `TIME_GRANULARITY=HOURLY`, `INCLUDE_RESOURCES=TRUE`, `INCLUDE_IAM_PRINCIPAL_DATA=TRUE` (gated April 8, 2026+ for Bedrock IAM-principal attribution). Delivered to S3 (Parquet preferred).
- GCP: BigQuery billing export with `bigquery.dataViewer` + `bigquery.jobUser` service account.
- Azure: Cost Management Export to Storage Account; daily granularity (Azure does not offer hourly — must be communicated).
- Tag-propagation report flagging per-cloud gotchas:
  - AWS: 48h tag-activation lag; services that don't propagate tags to child resources.
  - Azure: resource-group-only tagging excluded from exports.
- Cell ③ findings list — untagged spend with monthly cost estimate.

**Required behaviors:**
- Must detect cloud-provider SDK imports (boto3, google-cloud-*, azure-*) and cross-reference with actual customer cloud accounts before recommending exports.
- Must communicate the **24–48h floor** before first usable data clearly; first-export scan only fires after delivery + tag activation.
- First-export scan must detect: untagged AI spend (`AmazonBedrock` with empty `resourceTags/*`), broken tag propagation, granularity caps.
- Must produce a per-cloud connector configuration that ACUTE Tier 5 + the existing `cloud_cost_imports` table can consume.

**Required degraded modes:**
- Customer refuses one cloud's setup → continue with the others; report incomplete coverage.
- Customer's tag schema is resource-group-only on Azure → cannot lift to financial grade; report best-effort daily-grain attribution.

### 4.3 Skill 2 — Instrument (codemod) — **the framework's core deliverable**

**Primary outcome.** A PR that wires both **cost ingest** and **usage ingest** SDK calls into the customer's code with correct trace/span context, idempotency keys, lifecycle handling, and error paths. This is *the* point of the entire framework — every upstream skill exists to produce a correct input to this codemod.

**Required inputs:**
- Confirmed `cost-events-inventory.yaml` + `usage-events-inventory.yaml` from Skill A.
- Detected framework(s) per service.
- Detected trace-context provider(s): OTel / Datadog / Sentry / custom.

**Required behaviors:**
- Per-framework adapter for trace-context extraction (mandatory at minimum: OTel and Datadog; others TBD — **GAP, see §6**).
- For each emission site, the codemod MUST decide between three patterns:
  1. **Sibling-pair** — one SDK call at one site that produces both a usage event AND attaches cost-bearing span telemetry → cost event auto-attributes via `trace_id`. Default for AI/SaaS code paths.
  2. **Usage-only** — terminal event with no upstream cost-bearing call (e.g., billing on `seat.assigned` actions where no vendor call is made). Single usage-event emission.
  3. **Cost-only** — cost-bearing call with no usage event (subscription customers; non-AI infra hot paths). Requires acute SDK direct emission — blocked on acute-SDK gap §6 #25.
- Idempotency keys must be derived from each handler's **domain identity**, never from the API call's identity (Doc 3 §1.2 unresolved — replay/dedup window not defined).
- Failure-path coverage: don't emit usage events on errors by default; emit partials per customer policy. Cost events still fire on errors (they reflect spend that actually happened).
- PII guard: must not log span attributes that introduce security footguns.
- **SDK is blocking by design** (Doc 3 §6.1) — the codemod MUST surface a default decision for hot-path inserts: background-wrap or document-and-leave (Doc 3 §7.1 leans Option B, **needs confirmation, see §6**).

**Required degraded modes:**
- Framework adapter missing → emit `// TODO` annotation in PR, do not break compilation, flag for manual completion.
- Confirmed entry has stale `file:line` (upstream code changed since Skill A run) → flag, do not edit, surface in Skill R hand-off.

### 4.4 Skill 3 — Drift lint (CI)

**Primary outcome.** A CI step that flags divergence between code and saved inventory on every PR.

**Required flags:**
- New endpoint matching billing-event signals but no SDK call.
- Confirmed endpoint that has been renamed, moved, or deleted.
- Existing SDK call with stale event type or quantity field.

**Required behaviors:**
- Must run against the same code-graph fusion as Skill A's discovery (AST + OpenAPI + framework registries + telemetry).
- Must produce a delta report with severity (block PR / warn / informational) and a one-click "regenerate inventory entry" suggestion.
- Match strategy on inventory regeneration: by `workflow_id` (Doc 3 §3.8 not yet codified, **see §6**).

**Required degraded modes:**
- Customer's CI lacks the source needed for full code-graph (e.g., monorepo subset CI) → degrade to AST-only checks within the changed files.

### 4.5 Skill R — Adversarial review

**Primary outcome.** A dated review-spec artifact per invocation (`docs/superpowers/reviews/YYYY-MM-DD-<short-name>-pr-review-execution.md`) that itself becomes part of the integration audit trail.

**Required invocations (6 per pipeline run, per Doc 1):**
1. After Skill 1A doc-tree.
2. After Skill 1B code-graph.
3. After Skill 1C unconfirmed inventory.
4. After Skill 1D confirmed inventory.
5. Holistic gate before Skill 2 (cross-cutting).
6. After Skill 2 PR.

**Required 5-phase pattern:**
- Phase 1: Spec the review (summary of changes, risk map, verification commands, pre-recorded notes).
- Phase 2: Adversarial pass (findings = candidates, not facts; correctness / crashes / migrations / dependency / broken routes / bad assumptions / security footguns).
- Phase 3: Fix confirmed bugs (verification per fix; three-column spec update: what was wrong / what changed / what we ran to confirm).
- Phase 4: Robustness sweep (search related routes / handlers / service calls / error paths; consistency with existing app patterns).
- Phase 5: Repeat OR stop (stop when no real bugs OR remaining items are accepted non-blocking risks with rationale).

**Required per-invocation risk classes** (Doc 1 §9):
| Invocation | Primary risk class |
|------------|--------------------|
| After 1A | Hallucinated features, malformed hierarchy, missed sections. |
| After 1B | False call edges, missed handlers, misclassified routes, framework-idiom blind spots. |
| After 1C | Hallucinated `file:line`, double-mapped handlers, low-confidence false positives, missed non-HTTP entrypoints. |
| After 1D | Refund-test violations, sibling-feature inconsistencies, stale `file:line`. |
| Holistic (before 2) | Orphan features, double-counted endpoints, refund-unit drift, cross-feature trace-context conflicts. |
| After 2 | Compilation breaks, wrong framework adapter, idempotency-key sloppiness, error paths un-instrumented, security footguns introduced by codemod. |

### 4.6 Skill C — Reconciliation validation harness

**Primary outcome.** WAPE/Coverage measurements per cloud service, per algorithm, per customer pattern. Validation report. Engineering-internal only.

**Required inputs:**
- Real customer corpus: CUR / Billing Export / Cost Management for ≥1 billing month.
- Customer's actual monthly invoice totals (ground truth).
- Event-grain telemetry for the same period (from ACUTE Tier 2/4 ingest).
- Customer's tag schema.

**Required behaviors:**
- Normalize bill-grain to FOCUS half-open interval `[ChargePeriodStart, ChargePeriodEnd)` in UTC.
- Bucket event-grain `cost_events` to matching intervals per cloud (AWS hourly, GCP hourly, Azure daily).
- Run ACUTE's 12-algorithm ladder; record per row: algorithm fired, confidence, match grade.
- Measure WAPE per cloud service, weighted by cost.
- Measure Coverage per period (non-fallback / total).
- Per-algorithm empirical accuracy vs. claimed confidence.
- Categorize unexplained deltas into known failure patterns: timezone/clock skew, billing-period boundary, Azure daily-only granularity, CUR mid-month refinalization, tag dark window, Bedrock 4-token splitting, cross-region routing premium, untagged AI infrastructure.

**Required three-phase dogfood:**
- Phase 1: Moolabs's own bill — fail-here-fail-everywhere; ~1–2 engineer-weeks.
- Phase 2: 3–5 friendly customer corpora (at least one each AWS-heavy / GCP / Azure / polyglot / Bedrock-heavy); ~4–6 engineer-weeks; NDA-gated.
- Phase 3: Permanent CI gate — every PR to `attribution_engine.py` re-runs against the accumulated corpus.

**Required degraded modes:**
- Customer refuses to share corpus → Skill C runs locally in customer's environment; only aggregate validation metrics return to Moolabs.
- WAPE/Coverage gate fails on a service → block customer go-live for that service; communicate the per-service confidence map.

---

## 5. System-level behavioral requirements

These cut across all six components.

**5.1 Hand-off contracts between components.**
- Skill A → Skill 2: confirmed inventory YAML is the only input the codemod reads. Codemod must never re-derive feature mappings.
- Skill B → Skill C: Skill B's first usable export is also Skill C's input corpus for that customer. Skill C does not need a separate ingestion path.
- Skill R → next stage: review spec must include a "verified clean" sentinel and a list of accepted non-blocking risks; downstream skill must not proceed without it.

**5.2 Human-in-the-loop checkpoints (the destructive-action equivalents).**
- Three-role review before Skill 2 codemod runs — mandatory. PR is held until engineer + PM + finance all sign off (Doc 2's recurring requirement).
- Holistic Skill R gate before Skill 2 — mandatory. Catches cross-cutting issues no per-stage review can see.
- Customer go-live decision based on Skill C's first-month validation report — mandatory for any customer wanting financial-grade confidence in their invoices.

**5.3 Audit trail.**
- Every Skill R invocation produces a dated review spec at `docs/superpowers/reviews/YYYY-MM-DD-<short-name>-pr-review-execution.md`.
- Every inventory entry links to (a) the doc section it came from, (b) the code commit at discovery time, (c) the review spec that confirmed it.
- Every algorithm change in ACUTE (post-GA) carries the Skill C WAPE/Coverage delta as a CI artifact.

**5.4 Error / fallback surfaces.**
- AST parse failure on a file → log + skip; do not block discovery for the rest of the repo.
- Subagent timeout (Skill 1C fan-out) → escalate to parent; parent surfaces "incomplete mapping" for that feature in inventory.
- LLM-reviewer (Skill R) loops without converging → hard iteration cap with escalation to human (Doc 3 §5.5 unresolved, **see §6**).
- Catalog miss for a vendor SDK detected by AST → surface as "unclassifiable call site for review" in Skill A's review surface (**see §6**).
- Customer's repo has language Moolabs does not support → degrade to doc-driven discovery only; codemod skipped; flag clearly.

**5.5 Privacy / IP.**
- Customer code must not leave their environment unless explicitly authorized.
- Default review spec location is the customer's repo; opt-out path exists for customers with stricter IP policies (Doc 3 §5.6 unresolved, **see §6**).

**5.6 Cost / iteration budget.**
- Each Skill R invocation has an iteration cap (Doc 3 §5.2 unresolved, **see §6**).
- Phase 4 robustness sweep has a scope cap measured in graph hops (Doc 3 §5.3 unresolved, **see §6**).

---

## 6. Gaps and open questions

### 6.1 Already tracked in Doc 3 (`3ef884d4`) — cite, do not duplicate

The 30+ open questions in Doc 3 cover Doc 1's framing fully. The high-leverage subset for downstream HLD/contracts work:

| Doc 3 ref | Question | Why it matters for HLD |
|-----------|----------|------------------------|
| §1.1, §4.1 | Refund-test edge cases beyond §6.4's four scenarios | Skill A's terminal-event heuristic depends on this. §6.4 already validated four; promote refund test from "working" to "validated" — done. |
| §1.2 | Idempotency dedup window + replay semantics | Codemod (Skill 2) cannot pick a default idempotency key derivation without this. |
| §1.3 | Long-running / mid-stream billing pattern (completion-only / progress-tick / heartbeat) | SDK shape choice — affects every codemod insert for long-running paths. |
| §1.4 | Time mismatch between cost & billing events at invoice time | Affects Skill C's reconciliation algorithm and customer-facing margin reports. |
| §1.7 | Internal "billing event" terminology collision | Naming hygiene; affects every code review forever. |
| §2.4 | Trust asymmetry — how does Skill 3 give customers confidence it's complete? | Customer-facing presentation of coverage report. |
| §3.4 | `@colbymchenry/codegraph` capability ceiling (half-hour spike) | Determines how much lift Skill 1C's subagents have to provide. |
| §3.6 | Integrator review UX (markdown PR file v1 / web UI v2 / IDE) | First-impression for the integrator persona. |
| §5.1 | Reviewer-model persona for Skill R (same-model self-review is a known weak spot) | Determines whether Skill R needs cross-model orchestration. |
| §5.2 | Per-integration cost budget for Skill R iterations | Could be substantial at customer scale. |
| §5.3 | Phase 4 robustness-sweep scope cap (graph-hop radius) | Without it, Phase 4 either takes forever or gets skipped silently. |
| §5.4 | Severity rubric — what "low-level only" means for the stop criterion | Critical / High / Medium / Low — needs codification. |
| §5.5 | Convergence guarantees if Phase 5 never converges | Hard iteration cap + escalation. |
| §5.6 | Review spec location vs. customer IP policy | Default `docs/superpowers/reviews/` may not be acceptable. |
| §6.1 | SDK is blocking by design — implication for codemod | Background-wrap vs. blocking insert default. |
| §6.3 | Acute SDK does not exist today | Required for subscription-customer cost-event emission. |
| §7.2 | Subscription customers need direct acute-SDK emission | Skill 1D review must ask "billable / cost-only / both". |

### 6.2 New gaps for Skill B (Cloud bill integration) — not in Doc 3

Doc 3 has no companion for Skill B. These need answers before HLD:

1. **Multi-account / multi-org setup.** AWS Organizations, GCP folders, Azure management groups. Does Skill B walk through linking accounts or assume single-account/single-project? Most AI startups outgrow single-account fast.
2. **Permissions and IAM.** What's the minimum permission set Skill B asks for? Read-only on cost-explorer + S3 read on the export bucket? Does it require a Moolabs-owned cross-account IAM role, or does the customer create one? The skill's first prompt has trust-tone implications.
3. **First-export wait UX.** The 24–48h floor is unavoidable. What is the customer doing during that window? Does the skill park, polling? Does the integrator come back? Does Skill A run in parallel?
4. **Cell ③ severity / action.** Doc 3 §7.x doesn't address: should a cell ③ finding always surface to PM/finance, or only above a cost threshold? Auto-route or always-show? (Doc 3 question 6 in the original Appendix B is the closest match but unresolved.)
5. **Tag-schema enforcement.** Skill B recommends `tenant_id`, `product`, `feature`, `environment`. What if the customer already has a tag schema (e.g., FinOps-team-defined)? Does Skill B map between them, or require alignment to Moolabs's schema?
6. **Cross-cloud customers.** A customer on AWS + Azure simultaneously: does Skill B handle them in parallel or sequentially? Tag schema consistency between clouds?
7. **Re-running Skill B after tags change.** If customer fixes tag propagation per Skill B's recommendation, does Skill B auto-re-scan the next export and update the cell ③ list? Trigger / cadence?
8. **Empty-export / no-spend.** If a brand-new customer has zero spend in the first export window, what does Skill B output? "No findings; re-run after 30 days"?
9. **Cost-allocation rule selection.** ACUTE's four allocation rules (proportional_usage, fixed_percentage, equal_split, amortization). Who picks which rule applies per service — Skill B? PM during three-role review? Engineering default?

### 6.3 New gaps for Skill C (Reconciliation validation) — not in Doc 3

10. **Customer NDA template.** Phase 2 requires 3–5 friendly customer corpora. NDA coordination is called out as "the long pole." Is the NDA template prepared, or is each customer bespoke? Affects calendar feasibility.
11. **Local-only run model.** Doc 2 §3.3 mentions Skill C can run locally in the customer's environment with only aggregate metrics returning. What aggregate metrics specifically? Service-level WAPE/Coverage? Algorithm firing rates? Failure-pattern counts? The list is unspecified.
12. **Corpus retention policy.** The accumulated corpus grows. Three-year retention? Per-customer purge-on-request? GDPR/CCPA implications if any customer is EU/CA?
13. **CI runtime cost.** Skill C must run on every PR to `attribution_engine.py`. If the corpus has 20 customer-months of bills, full re-runs may be expensive. Sampling strategy?
14. **WAPE/Coverage thresholds for diverse customer patterns.** 10%/80% is uniform today. Doc 3 question 3 asks if these are right for production. Per-service or per-pattern thresholds may be more honest (e.g., financial-grade for Bedrock, best-effort for S3) — but the gate is currently logical-AND uniform. Decision needed.
15. **Algorithm versioning.** When an algorithm change ships, does Skill C re-attribute historical data or only run forward? Affects margin-report stability for existing customers.
16. **Phase 1 (Moolabs own bill) success criteria.** Doc 2 §4.1 shows an example WAPE/Coverage breakdown. What is the actual go-no-go for Phase 1 → Phase 2? "All services pass" or "≥3 of 5 services pass"?
17. **Phase-3 customer-onboarding-gate UX.** If a new customer's first month fails WAPE/Coverage gate, what does the customer see? Block go-live? Soft-launch with caveats? Tied to question 4 above.
18. **Customer-facing exposure of Skill C.** Doc 3 question 5 (Appendix B) — engineering-internal vs. aggregated public dashboard vs. per-customer report. Affects whether Skill C is purely internal infra or a customer-facing trust mechanism.

### 6.4 New gaps for Skill 2 (Codemod) — not in Doc 3

19. **Language scope for v1.** Both docs reference Python (FastAPI, Django), TypeScript (Express, NestJS), Go, possibly Java (Spring). v1 cannot ship all of these. Decision needed: which two or three for v1?
20. **Codemod review surface.** A 200-file diff PR is unreviewable. Does the codemod chunk by service / by feature / by file count? What is the maximum acceptable PR size, and how does it split if a customer's codebase exceeds it?
21. **Revert / rollback model.** A codemod-generated PR that breaks something — is the answer "git revert", or does Skill 2 carry a "regenerate-removing-feature-X" capability? Affects integrator trust.
22. **Coexistence with existing instrumentation.** Customers with existing OpenLLMetry or Helicone setups already emit some cost events. Does the codemod detect and skip? Does it add Moolabs custom OTel attributes (`moolabs.request.id` etc.) onto existing spans rather than wrapping?
23. **Idempotency-key derivation policy.** Per Doc 3 §1.2, key must derive from domain identity. Does Skill 2 ask the integrator per feature, or apply a heuristic (e.g., "first path parameter that looks like an id")? The latter risks silent wrongness.
24. **Background-wrap default (Doc 3 §7.1).** Confirm Option B for v1 (insert blocking, document latency in PR) — or override?

### 6.4a Three-role review gaps (surfaced by user clarification 2026-05-19)

The output→input→code three-projection model raises questions the original spec didn't address:

19j. **Disagreement resolution.** What happens when CFO accepts a usage event, PM rejects the input mapping, engineer flags the code location as wrong? Does Skill A re-propose? Is there a tie-breaker? Who has final say per dimension (instinct: CFO on price, PM on mapping, engineer on code)?
19k. **Asynchronous review.** All three roles rarely sit in the same room. Does the review surface support async progress (CFO signs off before PM finishes; PM's later change re-opens CFO's view)?
19l. **Many-to-many output↔input.** One cost event can feed multiple usage events (a shared `gpt-4` call across two features). One usage event can have multiple cost-event inputs. The linkage graph must support many-to-many; the codemod must derive correct attribution weights when one input feeds several outputs.
19m. **Fair-usage as first-class CFO data.** Some pricing models include "fair usage thresholds" (free up to N tokens then overage rates). Does the usage-events inventory carry fair-usage data, or is that downstream config in moo-meter? If carried here, it must survive Skill 3 drift detection.
19n. **PM's mapping persistence across regeneration.** When Skill A re-runs after code changes, how is the PM's output→input mapping preserved? Match by `workflow_id` (Doc 3 §3.8) — extend the matching strategy to cover the linkage graph, not just individual entries.
19o. **Confidence at the linkage level.** Each cost-event entry has confidence; each usage-event entry has confidence. The linkage between them is itself a claim (Skill A says "we think these inputs feed this output"). The linkage needs its own confidence score, separate from the entry confidences.

### 6.4b Framework-on-unknown-repo gaps (surfaced by user clarification 2026-05-19)

These are central to the "skills run in customer's repo where everything is unknown" framing and were under-explored in both source docs:

19b. **Repo-shape discovery.** Monorepo vs. polyrepo vs. microservices. Does the framework expect one entry point (single repo root) or many (one per service)? How does it discover service boundaries when no compose / k8s manifest exists?
19c. **Language detection.** Without prior knowledge, how does Skill A pick AST parsers? File-extension heuristic? `package.json` / `pyproject.toml` / `go.mod` discovery? What if multiple languages coexist in one service?
19d. **Existing-SDK detection.** A customer may already have partial Moolabs SDK installed (manual / earlier integration attempt). Does Skill A detect and avoid re-instrumenting? Does it offer to upgrade in-place?
19e. **Read vs. write permissions in customer repo.** The framework needs to *read* the entire repo and *write* a branch + PR. What's the minimum permission the customer grants? Is the framework local-only (runs on integrator's machine, no Moolabs network egress) or SaaS-with-read-access?
19f. **"Optimal manner" — define the optimization target.** Latency overhead on customer's request path? Coverage (% of cost/usage emission sites instrumented)? Developer-time-to-merge? Code-review PR size? Each leads to different codemod defaults.
19g. **Greenfield vs. brownfield.** A customer who already has rich OTel + Datadog vs. a customer with bare `console.log`. Doc 1 §13 says "pipeline doesn't branch" — but the *codemod's default insertion pattern* probably should branch (extend existing spans vs. introduce new ones).
19h. **Build-system integration.** Some codemod outputs require regenerating lockfiles / vendor folders / proto-generated code. Does Skill 2 trigger build-system commands, or leave that to the customer engineer? CI implications.
19i. **Where does the framework get invoked from?** Customer's IDE / CLI / GitHub Action / Moolabs-hosted web flow? Determines first-impression UX and integrator persona (Doc 3 §3.9 unresolved, sharpened here).

### 6.5 Cross-cutting new gaps

25. **The acute SDK gap (Doc 3 §6.3).** Subscription customers and any customer needing direct cost-event emission for non-AI spend require an acute SDK that does not exist today. Is the acute SDK in scope of this skill family, or a prerequisite that must ship separately first?
26. **Catalog miss (long-tail vendor).** A customer uses a niche LLM provider not in Moolabs's provider catalog. Skill A's deterministic match fails. Behaviors: silently skip / surface as "unclassifiable call site" / LLM-only heuristic fallback? Each has different cost-attribution implications.
27. **Skill R applies to Skill B and C?** Doc 2 is silent. Should the adversarial-review pattern run after Skill B's first-export scan (catches misclassified untagged spend) and after Skill C's WAPE/Coverage report (catches arithmetic errors)? Likely yes for Skill B, probably no for Skill C (since Skill C *is* a validation skill). Confirm.
28. **Versioning of the chargeability map.** When inventory regenerates (e.g., a year later, after Skill 3 has detected drift), how does it match historical entries to current code? By `workflow_id` (Doc 3 §3.8 working answer) — confirm and codify rename/merge/split semantics.
29. **Multi-tenant / multi-environment customers.** A customer with dev + staging + prod accounts and multiple tenant namespaces inside the product. Does the chargeability map span environments, or one per environment? Tag-schema consistency across them?
30. **Cell ③ + Tier 5 reconciliation (Doc 3 §7 in Appendix B, restated).** No code in ACUTE today compares aggregate Tier 5 imports to summed event-grain cost_events. Is that comparison in Skill C's scope or a separate engineering effort?

---

## 7. Constraints and dependencies

- **ACUTE today.** 12-algorithm attribution ladder + WAPE/Coverage gate exist in code (`services/moo-acute/app/services/attribution_engine.py`, §3.2 in Doc 2). Tier 5 connectors for AWS/GCP/Azure are NOT implemented — gap to fill for Skill B to land usefully.
- **Acute SDK does not exist.** Stitcher (`sdks/generator/scripts/stitch-specs.py`) accepts only `--bff` and `--meter`. Closure is mechanical (Doc 3 §6.3): stitch acute openapi + add namespace + patch X-API-Key.
- **SDK is blocking by design.** Doc 3 §6.1; affects every codemod insert.
- **24–48h floor before first usable cloud-bill data.** Unavoidable per AWS / GCP / Azure first-party docs. Skill B sequencing must accommodate.
- **Azure daily granularity.** Per-customer attribution within a day is interpolated, not measured. Customer-facing communication must be honest.
- **Region-encoded API keys (Doc 3 §8.1).** Decided 2026-05-11; SDK does the regional routing. Skill A's "where does your API key live" prompt can surface region detection. Forward-compatible.
- **moo-meter `BillingEvent` internal naming collision.** Doc 3 §1.7 unresolved; risks downstream confusion in every code review.

---

## 8. Artifacts (durable outputs)

| Artifact | Owner | Consumers |
|----------|-------|-----------|
| `cost-events-inventory.yaml` (inputs) | Skill A | Skill 2 codemod, Skill 3 drift lint, ACUTE Tier 1–4 config, moo-acute rate-catalog populator. Engineer review. |
| `usage-events-inventory.yaml` (outputs) | Skill A | Skill 2 codemod, Skill 3 drift lint, moo-meter rule synthesis. CFO + engineer review. |
| `output-input-map.yaml` (linkage) | Skill A; **PM certifies** | Chargeability map; margin-per-output computation; Skill 3 drift detection of broken links. All three roles review. |
| Per-cloud connector configuration | Skill B | ACUTE Tier 5, ACUTE `cloud_cost_imports`. |
| Tag-propagation report | Skill B | Customer engineer, finance. |
| Cell ③ findings | Skill B | PM, finance. |
| Codemod PR | Skill 2 | Customer engineer (review). |
| Drift-lint delta report | Skill 3 | Customer CI. |
| Review spec (`docs/superpowers/reviews/YYYY-MM-DD-*.md`) | Skill R | Audit / compliance; downstream skill (sentinel). |
| Per-customer validation report | Skill C | Customer go-live decision; engineering. |
| Per-algorithm regression delta (CI) | Skill C | ACUTE engineering. |

---

## 9. Out of scope (consolidated)

Carried over from both docs and the open-questions companion:

- Margin computation logic itself (downstream of inventories + cost data; ACUTE reconciliation engine + moo-meter invoice generator handle it).
- Plan / pricing configuration UX (moo-meter's plan-builder is separate).
- v2 web UI for inventory review (v1 is markdown PR).
- Refund / credit reconciliation logic (ACUTE append-only adjustments unchanged).
- Multi-cloud cost-allocation rule engine (ACUTE's four rules unchanged; Skill B configures inputs only).
- Customer's own internal cost-categorization taxonomy (customer-side transform on top of FOCUS export).
- Gateway URL unification (Doc 3 §7.4 + §8.2; deferred indefinitely unless edge-routing benefits become required).
- Cross-region admin operations (Doc 3 §8.1.c; deferred until admin SDK surface is built).
- moo-meter `BillingEvent` rename refactor (Doc 3 §1.7; not blocking, captured as cleanup).

---

## 10. Decisions needed before HLD

Ordered by leverage. The first six gate the codemod itself; the rest gate skills around it.

1. **"Optimal manner" — pick the optimization target** (gap §6.4b #19f) — coverage / latency / PR-size / time-to-merge.
2. **v1 language scope for the codemod** (gap §6.4 #19) — Python + TypeScript + Go? Pick two for v1.
3. **Acute SDK status** (Doc 3 §6.3, §7.2; gap #25) — in scope of this skill family or a hard prerequisite? Without it, cost-only emission patterns (subscription customers) cannot be codemodded.
4. **Codemod hot-path insert default** (Doc 3 §7.1) — confirm Option B (blocking + documented) for v1.
5. **Framework invocation surface + permission model** (gaps §6.4b #19e, #19i) — CLI / IDE / GitHub Action / hosted; local-only vs. read-access SaaS.
6. **Catalog miss behavior** (gap §6.5 #26) — silently skip / surface for review / LLM fallback?
7. **Three-role review UX surface** (Doc 3 §3.6) — markdown PR v1 vs. CLI vs. local web; pick v1.
8. **Skill R reviewer-model pattern + iteration cap + severity rubric** (Doc 3 §5.1, §5.2, §5.4, §5.5) — cross-model default; hard cap; escalation.
9. **Skill C customer-facing exposure** (Doc 3 Appendix B Q5; gap §6.3 #18) — engineering-internal only / per-customer report / public dashboard?
10. **Cell ③ severity / routing** (gap §6.2 #4) — threshold-based vs. always-surface.
11. **Skill 3 vs. Skill R v1-vs-v2 split** (Doc 3 §4.2) — instinct: Skill R essential in v1, Skill 3 v2 if needed. Confirm.

---

## 11. Appendix — Verified facts (carried from Doc 3 §6, kept here for HLD continuity)

- SDK blocking by design — `urllib3.PoolManager`, ~35ms median round-trip. No async variant.
- `client.meter.events.*` = billing events. `client.cls.*` = account/wallet/lifecycle. No third namespace today.
- Acute server publicly reachable on `acute.{prod,dev}.moolabs.com`; SDK has no acute namespace.
- Refund test passes 4-scenario pressure test (agentic / streaming / hybrid / subscription). Subscription edge: zero billing events, requires direct acute-SDK cost emission.
- Region-encoded API keys (`sk_use1_*`, `sk_apse1_*`) — SDK handles routing; no edge compute.

---

*End of requirements grooming. Next skill in chain: HLD (`hld-tech-specs-creator`) — but only after decisions §10 are made.*
