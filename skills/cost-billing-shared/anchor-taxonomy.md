# Anchor taxonomy — load-bearing vocabulary for the suite

Every skill in the suite agrees on these terms. Imported verbatim from requirements doc §2, with annotations.

---

## Actors

- **End-user** — the customer's customer. The party whose activity the customer wants to bill for.
- **Customer** — an AI/SaaS startup integrating Moolabs. Three review roles inside the customer org:
  - **Engineer** — verifies code locations.
  - **PM** (Product Manager) — classifies meter candidates, decides billable units, **owns the output↔input mapping**.
  - **Finance** / **CFO** — reviews cost coverage, reconciliation report, billed-unit pricing.
- **Moolabs** — the platform. Core services: `moo-meter` (billing/invoicing), `moo-acute` (cost intelligence), `moolabs-app/bff` (gateway). Plus the SDK packages at `moolabs-hq/moolabs-{py,go,ts}`.

---

## Events (the central distinction)

The framework instruments **two** flavors of ingest event. They typically originate at the **same code site** (one handler emits both).

### Cost (ingest) event
One unit of **vendor/infra spend** the customer incurred to serve one transaction. Examples: OpenAI tokens consumed, GPU seconds, S3 PUT count, bandwidth used.

- **Lands in:** `moo-acute` via Tiers 1–4 (event-grain) or Tier 5 (bill-grain).
- **Today (2026-05-19) SDK surface:** **None directly**. Cost events are emitted via OTel spans with Moolabs custom attributes; `moo-acute` Tier 2/4 ingest reads the spans. See `sdk-surface-reference.md` §"acute SDK gap" for the path forward.

### Usage (ingest) event
One unit of **product output** the customer charges their end-user for. Examples: chat completion delivered, image rendered, video transcribed.

- **Lands in:** `moo-meter` via `client.meter.events.ingest_events([...])`.
- **Today SDK surface:** Confirmed in `moolabs-hq/moolabs-py` README (verified 2026-05-18).
- **Naming collisions to be aware of:**
  - Doc 1 calls these "billing events."
  - The internal `moo-meter` Python code names them `BillingEvent` (Doc 3 §1.7).
  - The customer-facing SDK calls them events / usage events.
  - **This skill suite uses "usage events" externally** (consistent with the SDK README).

---

## Concepts

- **Refund test** — working definition of "one billing event": the unit at which the customer might issue a refund. Validated 2026-05-11 against four scenarios:
  - **Agentic** (multi-step) — billing event fires on agent final-answer, not per-step.
  - **Streaming** (token-by-token) — billing event fires on stream-complete, not per-token.
  - **Hybrid** (some real-time, some async) — billing event fires per addressable output.
  - **Subscription** (no per-output billing) — **zero billing events fire**; cost events still emit. **Skill 2 cost-only pattern is required for these customers.**

- **Grain**
  - **Event-grain** — per-request fidelity. Required for per-end-user margin attribution.
  - **Bill-grain** — aggregated billed lines (Tier 5 cloud-bill imports). Required for reconciliation against the cloud invoice.

- **Attribution keys** — cascading ladder enforced by `moo-acute`'s `attribution_engine.py`:
  - `request_id` — financial-grade (confidence 1.0).
  - `trace_id` — operational-grade (confidence 0.75).
  - `customer_id` / `feature_id` — entity-grade.
  - `tags` — fallback (proportional / fixed-percentage / equal-split / amortization rules apply).

- **WAPE / Coverage gate**
  - **WAPE** (Weighted Absolute Percentage Error) — reconciliation error per service vs cloud invoice.
  - **Coverage** — non-fallback rows / total rows per period.
  - **Promotion gate today (uniform):** `WAPE < 10% AND Coverage > 80%` (logical AND).
  - **v1 default:** uniform thresholds. **§10 #14 open:** per-service or per-pattern thresholds may be more honest (financial-grade for Bedrock, best-effort for S3).

- **Chargeability map** — the durable hierarchical YAML artifact. Keyed by `domain → product → feature → endpoint`, with usage-event + cost-event mappings, confidence, and decision metadata. Lives in customer repo at `.moolabs/inventory/`. See `assets/output-input-map.schema.yaml` in `cost-billing-discovery/` for the schema.

- **Cell ③ finding** (Skill B) — cost that is real but cannot currently be attributed to a feature or customer. Example: untagged S3 PUT spend on render retries. Surfaced for PM/finance review with an absorb-vs-fix decision.

- **Cell ④ finding** (Skill A v1 extension, §10 #6) — a call site detected in customer code that doesn't match the Moolabs provider catalog. Surfaced for engineer/PM review with a decide-add-to-catalog / non-billable / future decision.

---

## Artifacts (the three durable outputs Skill A produces)

| Artifact | Role in the chargeability graph |
|---|---|
| `cost-events-inventory.yaml` | **Inputs.** One entry per detected vendor call site = a candidate cost-event emission point. |
| `usage-events-inventory.yaml` | **Outputs.** One entry per terminal-event candidate = a candidate usage-event emission point. Carries CFO-facing pricing metadata. |
| `output-input-map.yaml` | **Linkage** (the chargeability graph). For each output, the set of inputs that feed it. **PM owns and certifies this artifact.** |

The codemod (Skill 2) reads all three; the drift-lint (Skill 3) checks all three against current code on every PR.

---

## Roles in review (the three projections)

`three-role-review.md` covers this in detail. Summary:

| Actor | Projection (same graph, different lens) | Final-say dimension |
|---|---|---|
| **CFO / Finance** | Output-side: usage events with price, projected revenue. | Price + billed unit. |
| **PM** | Output↔input bridge: which inputs feed which output. | The linkage graph. |
| **Engineer** | Code-side: file-grouped inventory with `file:line`, framework adapter, confidence. | Code location + framework adapter. |

---

## Pipeline patterns

- **Sibling-pair** — one SDK call at one site emits both events (usage + cost). Default for AI/SaaS code paths.
- **Usage-only** — terminal event with no upstream cost-bearing call (e.g., billing on `seat.assigned`). Single emission.
- **Cost-only** — cost-bearing call with no usage event (subscription customers; non-AI infra hot paths). **Blocked on acute SDK v1.**

---

## Time and intervals

- Cloud-bill exports normalize to FOCUS half-open interval `[ChargePeriodStart, ChargePeriodEnd)` in UTC.
- AWS CUR 2.0 = hourly granularity (with `TIME_GRANULARITY=HOURLY`).
- GCP BigQuery billing export = hourly.
- Azure Cost Management Export = **daily only** (no hourly option). Communicated to customer up-front.
- 24–48h floor before first usable cloud-bill data — unavoidable per AWS/GCP/Azure first-party docs.

---

## Naming collisions (tracked, not yet resolved)

- "Billing event" = Doc 1 term for what Doc 2 + the SDK call "usage event" or just "event". **Suite uses "usage event" externally** to match SDK README.
- "BillingEvent" = internal `moo-meter` Python class name. Renaming refactor is captured as cleanup, not blocking (requirements §9, §6 #1.7).
- `client.usage.*` was referenced in `../moolabs/sdks/generator/configs/moolabs-python.yaml:18` (a comment) — **STALE. The live namespace is `client.meter.events.*`.**

---

## Out of scope (clarified) — never confuse these for in-scope work

- Margin computation logic (downstream of inventories; ACUTE + moo-meter handle it).
- Plan / pricing configuration UX (moo-meter's plan-builder is separate).
- v2 web UI for inventory review (v1 = markdown PR).
- Refund / credit reconciliation logic (ACUTE append-only adjustments unchanged).
- Multi-cloud cost-allocation rule engine (ACUTE's four rules unchanged; Skill B configures inputs only).
- Customer's internal cost-categorization taxonomy (customer-side transform on top of FOCUS export).
- Gateway URL unification (Doc 3 §7.4, §8.2; deferred indefinitely).
- Cross-region admin operations (Doc 3 §8.1.c; deferred until admin SDK surface is built).
- moo-meter `BillingEvent` rename refactor (Doc 3 §1.7; not blocking).
