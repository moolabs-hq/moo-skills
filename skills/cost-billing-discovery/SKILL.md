---
name: cost-billing-discovery
description: >-
  Scan a customer's unknown Python/TypeScript/Go repository to identify where cost ingest events (OpenAI tokens, GPU seconds, S3 PUT) and usage ingest events (completion.delivered, image.rendered) should be emitted via the Moolabs SDK. Produces three reviewable YAML artifacts — cost-events-inventory.yaml (engineer's doc), usage-events-inventory.yaml (CFO's doc), output-input-map.yaml (PM's doc, the chargeability graph). Drives the sequential three-role review (CFO → PM ⇄ PM → Engineer with two PM-centered loops). Handles degraded modes for missing docs/telemetry, catalog misses surfaced as cell ④ findings, brownfield vs greenfield detection. First skill in the Cost+Billing Discovery suite. Use when starting Moolabs SDK integration in a new customer repo or re-running after refactors. Triggers on "discover ingest events", "find where to emit cost/usage events", "scan repo for Moolabs instrumentation", "build chargeability map", "Skill A".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  source: docs/grooming/2026-05-19-cost-billing-discovery-requirements.md §4.1
  dependencies:
    - url: https://github.com/moolabs-hq/moolabs-py
      name: Moolabs Python SDK
      type: sdk
    - url: https://github.com/moolabs-hq/moolabs-ts
      name: Moolabs TypeScript SDK
      type: sdk
    - url: https://github.com/moolabs-hq/moolabs-go
      name: Moolabs Go SDK
      type: sdk
---

# /cost-billing-discovery — Skill A: Cost + usage ingest-event discovery

You are an expert in scanning unknown customer codebases to identify where Moolabs cost and usage ingest events should be emitted. You produce three reviewable YAML inventories (cost-events, usage-events, output-input map) for a three-role review (CFO / PM / engineer). The downstream codemod (`/cost-billing-instrument`) reads these inventories to wire actual SDK calls into customer code.

## Trigger

User invokes `/cost-billing-discovery` followed by a target customer repo:

```
/cost-billing-discovery /path/to/customer/repo
/cost-billing-discovery /path/to/customer/repo --service services/api
/cost-billing-discovery /path/to/customer/repo --refresh   # re-run after code changes
/cost-billing-discovery /path/to/customer/repo --doc-tree docs/ --pricing-page https://example.com/pricing
```

Or naturally:

```
Discover cost and usage events in /path/to/customer/repo
Run Skill A on this repo
Build the chargeability map for our pilot customer
```

## Read first (shared/)

Before scanning a repo, load these from `cost-billing-shared/`:

- `anchor-taxonomy.md` — vocabulary (cost vs usage event, refund test, attribution keys, chargeability map, cells ③/④)
- `sdk-surface-reference.md` — what to emit (`client.meter.events.ingest_events` for usage; OTel spans with `moolabs.*` attributes for cost until acute SDK ships)
- `three-role-review.md` — CFO/PM/engineer projection model — your outputs feed this surface
- `v1-decisions-log.md` — the §10 v1 calls that shape your defaults (coverage-first, Python+TS, OTel-for-cost, etc.)
- `gaps-tracker.md` — the §6 open questions you may hit in customer code

## Workflow — 5 phases

### Phase 1: Repo-shape + language detection

**Goal:** know what you're scanning before you scan it. Per `gaps-tracker.md` §6.4b #19b/c, manifest-first then file-extension fallback.

Run `scripts/repo_scan.py <repo_path>` which produces `.moolabs/discovery/repo-profile.yaml`:

```yaml
repo_type: polyrepo | monorepo | microservices
services:
  - path: services/api
    languages: [python]
    manifests: [pyproject.toml]
    frameworks_detected: [fastapi]      # via dependency scan
  - path: services/web
    languages: [typescript]
    manifests: [package.json]
    frameworks_detected: [nextjs, express]
existing_instrumentation:
  - opentelemetry-api  v1.27.0
  - openllmetry-sdk    v0.18.0
existing_moolabs_sdk:
  - none                                # or: moolabs==0.2.0-rc9
```

If `existing_moolabs_sdk` is present, ask the user: "Upgrade in place, or fresh re-instrument?" (per §6.4b #19d).

### Phase 2: Doc-tree ingestion (graceful degradation)

**Goal:** understand the *product*, not just the code. Build a hierarchical doc-tree (domain → product → feature → workflow) if docs exist; degrade gracefully if not.

**Detection priority:**
1. Docusaurus / Mintlify / GitBook / Notion (if URL provided via `--doc-tree`)
2. `/docs/`, `/documentation/` directories
3. OpenAPI specs (`openapi.yaml` / `openapi.json`)
4. READMEs + framework registries (FastAPI routers, Django urls.py, Express routes)
5. Pricing page (if `--pricing-page` URL provided) — high signal for usage-event candidates

Output: `.moolabs/discovery/doc-tree.yaml` with `confidence: high|medium|low` per branch.

**Degraded mode** (no docs): proceed with OpenAPI + READMEs + framework registries only. Surface "thin doc-tree, more review iterations expected" in the next phase.

### Phase 3: Code-graph fusion

**Goal:** know every cost-bearing call site and every plausible usage-event handler.

Run `scripts/catalog_match.py` against each detected service. It performs:

1. **AST scan** for catalog-matched vendor calls (deterministic). Catalog at `assets/provider-catalog.starter.yaml`. Examples:
   - `openai.chat.completions.create` → cost (LLM token spend)
   - `replicate.predictions.create` → cost (GPU seconds)
   - `boto3.client('s3').put_object` → cost (storage / bandwidth)
   - `pinecone.index.query` → cost (vector search)
   - `anthropic.messages.create` → cost (Claude tokens)
2. **Discard-pattern filter** — drop SDK internals, retry loops, auth refreshes, health checks via attribute-presence allowlist (e.g., must have `gen_ai.usage.input_tokens` set for OTel-instrumented LLM calls to count).
3. **Catalog miss surfacing** — any vendor SDK call detected by AST but NOT in catalog → cell ④ finding (per `v1-decisions-log.md` #6, never silently skip).

**Output:** intermediate `.moolabs/discovery/code-graph.yaml` (call sites by file, with confidence).

### Phase 4: Refund-test heuristic → usage-event candidates

**Goal:** identify *terminal events* — the unit at which the customer would issue a refund.

Run `scripts/refund_test.py` which applies auxiliary signals from `assets/terminal-event-heuristics.yaml`:

- **Verb pattern** in handler name: `_completed`, `_delivered`, `_returned`, `_generated`, `_finished`
- **OpenTelemetry span.kind = server** at the parent of the call site (vs span.kind = client/internal)
- **Pricing-page intersection** — if `--pricing-page` was provided and the handler name appears (substring match) in the pricing copy, +0.20 confidence
- **Response-shape heuristic** — handlers returning user-addressable artifacts (URLs, IDs, content blocks) more likely terminal than internal handlers
- **Subscription edge case** — handlers under recognized "subscription"/"plan" URL patterns may emit ZERO usage events (cost-only customers); flag, don't drop

Each candidate gets a confidence ∈ [0, 1] and a `refund_unit` (per-token, per-render, per-minute, per-seat, per-completion).

### Phase 5: Output-input-map proposal + three-role surface

**Goal:** propose the linkage graph and emit the three inventories.

Run `scripts/inventory_build.py` which produces three files under `.moolabs/inventory/`:

```
cost-events-inventory.yaml      # inputs (engineer-ordered: by file path)
usage-events-inventory.yaml     # outputs (CFO-ordered: by projected revenue desc)
output-input-map.yaml           # linkage graph (PM-ordered: by output)
```

Linkage proposal uses three signals:
1. **Call-graph proximity** — cost events in the call subtree rooted at a usage-event handler are likely inputs.
2. **Trace co-occurrence** (if OTel telemetry available) — events sharing `trace_id` more than 80% of the time are likely linked.
3. **Naming signals** — co-located in same file, similar handler names.

Each edge in `output-input-map.yaml` carries its own `confidence` (separate from per-entry confidence, per §6.4a #19o):

```yaml
edges:
  - output_id: completion.delivered
    inputs:
      - cost_event_id: openai.chat.completions.create
        weight: 1.0           # equal split default; PM overrides
        confidence: 0.92
        rationale: "co-located in handler; OTel trace co-occurrence 0.97"
```

Then run `scripts/three_role_views.py` to project the three role views:

- `reviews/cfo-view.html` — usage-events with billed unit, price, projected revenue
- `reviews/pm-view.html` — output→inputs graph (editable YAML form)
- `reviews/engineer-view.html` — file-grouped inventory with file:line, framework adapter, confidence

The HTML previews are static (no server). CFO and PM can read them; engineer reviews the YAML directly.

## Degraded modes (recover gracefully)

| Condition | Behavior |
|---|---|
| No docs / no doc-tree | Fall back to OpenAPI + READMEs + framework registries. Thinner signal; more review iterations expected. |
| No telemetry (no OTel) | Skip trace-co-occurrence signal in Phase 5; lean on call-graph proximity + naming only. |
| Catalog miss | Surface as cell ④ finding (`reviews/cell-4-unclassifiable.yaml`). Engineer/PM decides: add-to-catalog / non-billable / future. |
| AST parse failure on a file | Log + skip; continue with rest of repo (per requirements §5.4). |
| Customer's repo language not Python/TS/Go | Fall back to doc-driven discovery only; codemod skipped; flag clearly to the user. |
| Existing OpenLLMetry / Helicone / Langfuse | Detected in Phase 1; recorded; Skill 2 codemod will extend their spans rather than wrap (brownfield branch per §6.4b #19g). |

## Outputs (consumed by downstream skills)

| File | Consumed by |
|---|---|
| `.moolabs/inventory/cost-events-inventory.yaml` | `/cost-billing-instrument` (codemod), `/cost-billing-drift-lint` (CI), `/cost-billing-reconcile` (validation) |
| `.moolabs/inventory/usage-events-inventory.yaml` | `/cost-billing-instrument`, `/cost-billing-drift-lint`, `moo-meter` rule synthesis |
| `.moolabs/inventory/output-input-map.yaml` | All downstream — the chargeability graph |
| `.moolabs/discovery/repo-profile.yaml` | All downstream (informs codemod template selection) |
| `.moolabs/discovery/cell-4-unclassifiable.yaml` | PM review surface |

## Mandatory hand-off contract — sequential CFO → PM ⇄ PM → Engineer with TWO loops

See `cost-billing-shared/three-role-review.md` for the full Y-shaped workflow. The codemod (`/cost-billing-instrument`) refuses to run unless ALL of these files exist in `.moolabs/inventory/reviews/`:

1. `cfo-stage1-signoff.yaml` — `status: approved` (CFO generated cfo-spec.md + filled cfo_metadata).
2. `pm-stage2-signoff.yaml` — `status: approved` (PM generated pm-spec.md + built output-input-map.yaml).
3. `cfo-stage2b-signoff.yaml` — `status: approved` (CFO approved PM's spec — present even if no loop iterations occurred, indicating a one-pass approval).
4. `engineer-stage3-signoff.yaml` — `status: approved` (Engineer generated engineer-spec.md + verified file:line / adapter / idempotency).
5. `pm-stage3b-signoff.yaml` — `status: approved` (PM approved engineer's spec — present even on one-pass).
6. `holistic-r-review.md` — `verdict: clean` or `verdict: clean-with-accepted-risks` (Skill R final gate).
7. No CRITICAL or HIGH severity finding remains open in any review spec.

The workflow is **sequential with two PM-centered loops** (CFO ⇄ PM upstream; Engineer ⇄ PM downstream). PM is the apex; engineer never talks directly to CFO in v1.

## Reference files (load on demand)

- `references/repo-detection.md` — manifest-based discovery details for Python (pyproject.toml, requirements.txt, setup.py), TypeScript (package.json, tsconfig.json), Go (go.mod, go.sum).
- `references/provider-catalog.md` — catalog structure + how to extend with new vendors.
- `references/refund-test-heuristics.md` — terminal-event signals + the 4-scenario validation.
- `references/degraded-modes.md` — recovery details per failure mode.
- `references/three-role-projection.md` — how the same graph projects three ways (links to shared `three-role-review.md`).

## Scripts

- `scripts/repo_scan.py` — Phase 1 repo-shape + language detection.
- `scripts/catalog_match.py` — Phase 3 AST scan + catalog matching.
- `scripts/refund_test.py` — Phase 4 terminal-event heuristic.
- `scripts/inventory_build.py` — Phase 5 inventory emission.
- `scripts/three_role_views.py` — render CFO/PM/engineer HTML previews.

## Assets

- `assets/provider-catalog.starter.yaml` — initial vendor catalog (OpenAI, Anthropic, Replicate, Pinecone, AWS S3/Bedrock, GCP Vertex, Azure OpenAI, Stripe, Twilio).
- `assets/cost-events.schema.yaml` — JSON-Schema for cost-events-inventory.yaml.
- `assets/usage-events.schema.yaml` — JSON-Schema for usage-events-inventory.yaml.
- `assets/output-input-map.schema.yaml` — JSON-Schema for the linkage graph.
- `assets/terminal-event-heuristics.yaml` — verb patterns, response-shape rules, pricing-page signal weights.
