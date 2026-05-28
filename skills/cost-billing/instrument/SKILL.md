---
name: cost-billing-instrument
description: >-
  The Cost+Billing suite's CORE DELIVERABLE — a codemod that wires cost ingest events (OTel spans with moolabs.* attributes) and usage ingest events (client.usage.ingest_events) into customer code, based on the three confirmed inventories from cost-billing-discovery. Generates reviewable per-service PRs (max 30 files each) with correct trace/span context, idempotency keys derived from domain identity, lifecycle handling for success/error/partial-stream paths, framework adapters per stack (Python+FastAPI/Django/Flask, TypeScript+Express/NestJS/Next.js — Go v1.5), and PII guards. Implements three patterns — sibling-pair (default), usage-only, cost-only (direct `client.cost.ingest_events_batch` emit when the Phase 1.5 snapshot reports the cost endpoint, OTel span + structured log as the recovery rail). Default insert mode is BLOCKING (Option B per §10 #4) with PR documenting ~35ms latency. Only runs after all three role signoffs + holistic Skill R verdict. Triggers on "run the codemod", "instrument this repo", "wire SDK calls", "Skill 2".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  source: docs/grooming/2026-05-19-cost-billing-discovery-requirements.md §4.3
  blocking_dependencies:
    - cost-billing-discovery   # produces inventories
    - cost-billing-adversarial-review   # holistic gate required
---

# /cost-billing-instrument — Skill 2: Codemod (the framework's core deliverable)

You are an expert codemod author who wires Moolabs SDK calls into customer code based on confirmed inventories. **You are the entire framework's point.** Every upstream skill exists to produce a correct input to you. Discovery is a means; this codemod is the end.

## Trigger

```
/cost-billing-instrument /path/to/customer/repo --service <service-slug>   # REQUIRED for multi-service orgs
/cost-billing-instrument /path/to/customer/repo --service services/api
/cost-billing-instrument /path/to/customer/repo --service <your-service> --dry-run
/cost-billing-instrument /path/to/customer/repo --service <your-service> --pattern usage-only
```

**`--service <slug>` is REQUIRED for multi-service customers.** Each engineer runs the codemod for THEIR service. The codemod reads `.moolabs/chain/04-final-<service-slug>.signed.yaml` for technical decisions + scopes the AST scan + PR emission to that service's subdirectories. Single-service orgs may omit `--service` and the codemod runs over the whole repo (back-compat).

### Post-codemod handoff for iterative revision

After this skill emits the PR(s), the cost-billing suite's responsibility ends. Hand off to your existing PR-iteration skill:

  ```
  /dev-workflow-orchestrator                  # or whatever your team uses for PR revision
  ```

Iterating on the PR based on CI failures, reviewer comments, or post-codemod adversarial-review findings is NOT the codemod's job — that's the dev-workflow-orchestrator's responsibility. The cost-billing suite documents this handoff explicitly so engineers don't expect this skill to re-emit on each iteration.

Or naturally:

```
Run the codemod on the confirmed inventories
Wire the SDK calls into services/api
Generate the PR for our pilot customer's instrumentation
```

## Operating principles (apply to EVERY codemod decision)

See `cost-billing-shared/operating-principles.md`. Codemod-specific manifestations:

1. **NEVER assume** — the framework adapter, the request-context source, the idempotency anchor. Confirm via the engineer-stage signed doc (`04-final.signed.yaml`), not by guessing from imports.
2. **When in doubt, ASK** if the human is present at codemod time; if running headless (CI dry-run), emit a CRITICAL severity finding in the PR description rather than silent-default.
3. **Per-pattern decisions** — sibling-pair vs usage-only vs cost-only — are determined by the engineer's signed doc, NOT by codemod inference. If the signed doc didn't decide, REFUSE to emit that insert; surface in the PR's "TODO from codemod" section.
4. **PII / PHI guard** — never strip a field silently if it matches a blocklist regex. Always emit a CRITICAL adversarial-review finding so the customer's reviewer SEES + decides.
5. **Brownfield vs greenfield** — confirmed by the engineer's `telemetry-stack.yaml`. If THAT file says "OTel present" but the customer's actual code doesn't import OTel, that's drift — FAIL, don't silently switch modes.
6. **Idempotency anchor missing** for an entry — NEVER fabricate one from the handler name. Emit `# REVIEW: codemod could not derive an idempotency anchor — engineer must supply` and surface as MEDIUM finding.

## Read first (shared/)

- `sdk-surface-reference.md` — **load this every time.** It carries the verified call shapes (`client.usage.ingest_events` for usage, `client.cost.ingest_events_batch` for cost, what NOT to call).
- `v1-decisions-log.md` — your defaults come from here (Option B blocking insert, Python+TS v1, coverage-first).
- `anchor-taxonomy.md` — the three patterns (sibling-pair / usage-only / cost-only).

## Refuse-to-run preconditions

### Customer-context (always required)

This skill reads `<repo>/.moolabs/customer-context/` for terminology, pricing-model, telemetry stack, and repo info. If absent, refuse with: "customer-context/ not found. Run `/cost-billing-bootstrap` first."

Specifically required:
- `customer-context/terminology.yaml` — codemod uses customer terms in `event_type` strings and PR descriptions.
- `customer-context/telemetry-stack.yaml` — codemod picks brownfield vs greenfield per service from this.
- `customer-context/repo-info.yaml` — codemod picks per-language, per-framework templates from this.

### Sequential workflow with TWO review loops

Per `cost-billing-shared/three-role-review.md`, the workflow is CFO → PM ⇄ PM → Engineer with two PM-centered loops. With multi-product + multi-service fan-out (per `chain-handoff.md`), the codemod refuses to run unless ALL of these exist FOR THIS `--service <slug>` INVOCATION:

**The three inventory artifacts (org-wide):**
1. `.moolabs/inventory/cost-events-inventory.yaml`
2. `.moolabs/inventory/usage-events-inventory.yaml`
3. `.moolabs/inventory/output-input-map.yaml`

**The signoff cascade (fan-out aware):**

Read `02-cpo.signed.yaml > products[]` to enumerate products. Read `02-cpo.signed.yaml > products[].services` to map products ↔ services. For a `--service <S>` invocation, identify the set of products `P(S) = {p : p.services contains S}`.

Required signoffs:
4. `.moolabs/inventory/reviews/cfo-stage1-signoff.yaml` — `status: approved` (1, org-wide).
5. **For EACH product `p` ∈ P(S):**
   - `.moolabs/inventory/reviews/pm-stage2-signoff-<p>.yaml` — `status: approved`
   - `.moolabs/inventory/reviews/cfo-stage2b-signoff-<p>.yaml` — `status: approved`
6. `.moolabs/inventory/reviews/engineer-stage3-signoff-<service>.yaml` — `status: approved` (1, for THIS `--service`).
7. `.moolabs/inventory/reviews/pm-stage3b-signoff-<service>.yaml` — `status: approved` (1, for THIS `--service`).

**The final adversarial gate (org-wide):**
8. `.moolabs/inventory/reviews/holistic-r-review.md` — `verdict: clean` or `verdict: clean-with-accepted-risks`.

**Single-product/single-service back-compat:** when `02-cpo.signed.yaml > products[]` has exactly one entry with `slug: <only-product>` and `services: [<only-service>]`, the per-product file is still `pm-stage2-signoff-<only-product>.yaml`.

**No legacy back-compat.** v0.3 schemas require fields v0.2 didn't have (`product_slug`, `service_slug`, schema-versioned `adversarial_review`). The codemod REJECTS any signoff file lacking the v0.3 `$schema` URL. v0.2 customers must restart from `/cost-billing-bootstrap-finance`. **(F6 fix — clean break.)**

**Validation logic for the cascade:**
- Stages 2b and 3b are always required (even if zero-cycle loop).
- For multi-product `--service <S>` spanning N products, ALL N pm-stage2-signoff-<p>.yaml + ALL N cfo-stage2b-signoff-<p>.yaml must be `approved` before this service's codemod runs.
- `engineer-stage3-signoff-<S>.yaml` is THIS engineer's; other services' engineer signoffs are NOT required for this `--service` run.

**Multi-owner co-signing for `pm-stage3b-signoff-<S>.yaml`** (per signoff.schema.yaml's `co_signed_by[]` field, gate-validation rule #9 in `cost-billing-signoff/references/signoff-yaml-schema.md`):
- If service `<S>` belongs to ONLY ONE product (`P(S)` cardinality = 1), the single PM's `signed_by` is sufficient.
- If service `<S>` belongs to ≥2 products (`P(S)` cardinality ≥ 2), `co_signed_by[]` MUST include one entry per owning PM beyond the primary `signed_by`. Each `co_signed_by[]` entry's `on_behalf_of_product` slug MUST be in `P(S)`. Codemod REJECTS if ANY owning product's PM is missing from the `signed_by` + `co_signed_by[]` union.
- Each co-signer's `contact` is cross-checked against `02-cpo.signed.yaml > products[on_behalf_of_product].team_pm_contact` (when set; warn if unset per F1).

**Per-file validation invariants** (applied on every signoff file the gate reads):
- `$schema` URL == `https://moolabs.com/schemas/cost-billing-signoff/0.1.0` (v0.3+; v0.2 files REJECTED).
- `stage` matches the expected stage for the filename.
- `product_slug` (when applicable) IS in `02-cpo.signed.yaml > products[].slug`.
- `service_slug` (when applicable) appears under at least one `products[].services` entry.
- Body-slug ↔ filename match: filename's slug suffix == `product_slug` / `service_slug` in the YAML body (F2 invariant).
- `signed_at` is after `generated_at` (catches backdating).
- For PM stages: `signed_by.contact` matches the owning product's `team_pm_contact` IFF that field is set (F1).
- For pm-stage3b on multi-owner services: `co_signed_by[]` invariant above.

**Refuse message format** when any file missing:
```
REFUSED: codemod gate not satisfied for --service <S>.

Missing signoffs:
  - reviews/pm-stage2-signoff-acute.yaml (run /cost-billing-signoff --persona team-product --product acute)
  - reviews/engineer-stage3-signoff-<S>.yaml (run /cost-billing-signoff --persona team-engineer --service <S>)

Products owning service <S> (from 02-cpo.signed.yaml): [acute, arc]
All signoffs needed per product: pm-stage2-signoff-{acute,arc}.yaml + cfo-stage2b-signoff-{acute,arc}.yaml
Plus this service's engineer signoffs: engineer-stage3-signoff-<S>.yaml + pm-stage3b-signoff-<S>.yaml
Plus org-wide: cfo-stage1-signoff.yaml + holistic-r-review.md
```

## Workflow — 4 phases

### Phase 1: Plan the PR (no edits yet)

Run `scripts/codemod_driver.py --plan <repo>`. Produces `.moolabs/codemod/plan.yaml`:

```yaml
plan_version: 0.1.0
total_inserts: 47
files_touched: 18
prs_to_emit: 2          # chunked at max 30 files per PR (v1 default)
patterns:
  sibling_pair: 31
  usage_only: 11
  cost_only: 5           # direct client.cost.ingest_events_batch emit (snapshot cost_event_direct_emit=true)
chunks:
  - pr: 1
    services: [services/api, services/billing]
    files: 16
    inserts: 28
  - pr: 2
    services: [services/render, services/transcribe]
    files: 12
    inserts: 19
warnings:
  - "2 entries have framework=litestar (no v1 adapter); inserting TODO comments"
```

**Always show the plan to the user before proceeding.** If they say "go", continue to Phase 1.5.

### Phase 1.5: Snapshot the unified Moolabs SDK at the pinned version (MANDATORY, ONCE per codemod run)

**The codemod MUST NOT trust the static `cost-billing-shared/sdk-surface-reference.md` at emission time.** That doc is a curated hint from 2026-05-18 (and earlier dates); the SDK has likely moved since. The truth lives in the SDK repo at the version the customer locked into `04-final.signed.yaml > integration.sdk_package_install` — fetch and introspect it before generating helpers or call-site inserts.

**Why this exists (added 2026-05-25 after an early integration uncovered framing issues):**

1. **The SDK evolves between curation and customer runs.** Method names, namespace structure, even the import name can change. A static reference rots silently.
2. **New capabilities should not require a new codemod release.** When the unified SDK adds `client.meter.cost.ingest_events()` (final path TBD), customers re-running the codemod should automatically get direct SDK emission for cost events — no skill update needed. The snapshot is how the helper finds out.
3. **The snapshot is auditable customer-context.** Lives at `.moolabs/customer-context/sdk-surface-snapshot.yaml` alongside the other signed Phase 4 artifacts; travels with the PR; future codemod re-runs can diff against it.
4. **Refuse-to-emit on contract break.** If `client.usage.ingest_events` no longer exists in the snapshot (renamed, removed), the codemod stops before writing — surfaced as a CRITICAL finding for Skill R rather than producing a PR that silently fails at customer runtime.

**Steps:**

1. Read `04-final.signed.yaml > integration.sdk_package_install` per language. For each language with `strategy != "skip"`:
   - `latest-tag` → resolve current latest stable tag from `git ls-remote --tags <repo> | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1`
   - `pinned` → use `version`
   - `private-mirror` → fetch from `mirror_url`
   - `custom` → SKIP introspection (the customer's mirror may not be inspectable); emit a warning and fall back to the static reference doc
2. Shallow-clone the SDK repo at the resolved tag: `git clone --depth=1 --branch <tag> <repo> /tmp/moolabs-sdk-<lang>-<tag>`.
3. Run `scripts/sdk_snapshot.py --lang <python|typescript|go> --src /tmp/moolabs-sdk-<lang>-<tag>`. Per language:
   - **Python:** AST-parse `__init__.py` and recurse — extract `Moolabs` class attributes and each namespace's public methods. NO IMPORT — static parse only (avoids running customer-package side effects).
   - **TypeScript:** Read `package.json > exports` and parse the matching `.d.ts` files. Extract top-level exports + method signatures of the `Moolabs` class.
   - **Go:** Run `go doc -all ./...` against the local clone; parse the package surface.
4. Write `.moolabs/customer-context/sdk-surface-snapshot.yaml`:

   ```yaml
   # Real output, verified against moolabs-py@v0.2.0-rc9 (2026-05-25).
   # The actual SDK exposes 11 FLAT capability namespaces — NO client.cls / client.meter
   # split (that was the curated reference doc's mistake; the snapshot is ground truth).
   generated_at: 2026-05-25T12:30:00Z
   sdk_versions:
     python:     { repo_url: "...moolabs-py", resolved_tag: v0.2.0-rc9, commit_sha: 7d5b07d771b2, is_prerelease: true }
   namespaces:
     python:
       - path: "client.usage"
         methods: [ingest_events, list_events, query_meter, create_meter, ...]
       - path: "client.cost"
         methods: [ingest_event, ingest_events_batch, ingest_sdk_spans, submit_adjustment]
       - path: "client.wallets"
         methods: [allocate_credits, create_wallet, ...]
       # ... 8 more flat capabilities
   capabilities:
     usage_event_emit:        true                              # verified client.usage.ingest_events present
     cost_event_direct_emit:  true                              # verified client.cost.ingest_events_batch present
     cost_event_method_path:  "client.cost.ingest_events_batch" # what the helper template renders
   contract_drift:
     # populated when expected methods are MISSING — codemod aborts; surfaced to Skill R
     missing_expected_methods: []
     renamed_methods: []
   ```

5. **Contract check (gates Phase 2):**
   - `capabilities.usage_event_emit` MUST be true. If not, abort with a clear error: the SDK is missing a method the helper depends on. User can either pin to an older version where it existed, or fix the SDK and re-run.
   - `contract_drift.missing_expected_methods` MUST be empty. Any entry → abort + surface to Skill R.
   - `contract_drift.renamed_methods` MAY have entries → warn but proceed; emit notes into the PR description so the customer reviews them.

The snapshot is the **input contract** for Phase 2 (helper) and Phase 2b (call-site inserts). Neither phase reads `sdk-surface-reference.md` directly anymore; that doc is now a fallback hint for human reviewers, not a runtime input.

### Phase 1.6: Discover + confirm attribution sources (MANDATORY, interactive, ONCE per service)

**The templates do NOT know where the customer's code keeps `request_id` / `customer_id`.** The v0.1 templates hardcoded framework conventions (`request.state.customer_id` for FastAPI, `flask.g.customer_id` for Flask, etc.) — but every customer's middleware pattern differs, webhook routes bypass middleware, custom auth code puts the customer identifier in non-standard places. Emitting code that references `request.state.customer_id` when the customer's actual code reads it from `request.scope['org_id']` will compile but break at runtime with `AttributeError`.

**Why this exists** (added 2026-05-25 after the user observed: "skill and tasks defined should determine where to source the variables needed by usage and cost events, confirm with developer during instrumentation"):

1. **No two customers' middleware look the same.** Conventions get us 60% of the way; the remaining 40% is custom.
2. **Webhooks + health checks + cron entry points BYPASS middleware.** Even if 90% of routes follow the convention, the 10% that don't will silently emit broken code.
3. **The developer is the only authoritative source.** Static analysis can propose; only the developer can confirm.
4. **Bindings persist** as customer-context, signed by the engineer, auditable across re-runs.

**Steps:**

1. Run `scripts/attribution_discovery.py --service <slug> --customer-context-dir .moolabs/customer-context`. The script:
   - Scans the service for middleware files (FastAPI `@app.middleware`, Django `MIDDLEWARE` config, NestJS `@Injectable()` middleware classes, Express `app.use`).
   - Greps for assignments like `request.state.X = ...`, `request.scope[...] = ...`, `flask.g.X = ...`, `request.user = ...`, `setattr(request, ...)`.
   - For each attribution key the templates need (`request_id`, `customer_id`, `consumer_agent`), proposes 1–3 candidate sources with confidence + evidence (`file:line`).
2. **Interactively** present each proposal to the developer one key at a time (per `cost-billing-shared/operating-principles.md`: ONE question at a time, NEVER assume). Developer choices:
   - Confirm the highest-confidence proposal
   - Pick an alternative from the list
   - Provide a custom expression (e.g., `request.headers.get('x-org-id')`)
   - Mark "not available" → the codemod will skip that attribution key for the whole service
3. Detect per-file overrides: routes flagged in `repo-profile.yaml > middleware_bypass[]` (webhooks, healthchecks) get their own override prompt.
4. Persist confirmations to `.moolabs/customer-context/attribution-bindings.yaml`:

   ```yaml
   service_slug: <your-service>
   framework: fastapi
   generated_at: 2026-05-25T16:45:00Z
   bindings:
     request_id:
       source: "request.state.request_id"
       confidence: high
       evidence: ["services/billing-api/app/middleware/request_id.py:18"]
       confirmed_by: kritivas.shukla@moolabs.com
       confirmed_at: 2026-05-25T16:45:00Z
     customer_id:
       source: "request.state.customer_id"
       confidence: medium
       fallback_when_absent: skip   # codemod omits customer.id attribute if expression is unavailable at insert site
       confirmed_by: kritivas.shukla@moolabs.com
       confirmed_at: 2026-05-25T16:45:00Z
     consumer_agent:
       source: null                  # explicit null → codemod skips this attribute everywhere
       confidence: n_a
       confirmed_by: kritivas.shukla@moolabs.com
   overrides:
     - file: services/billing-api/app/api/v1/webhooks/router.py
       reason: "webhook handler — bypasses TenantMiddleware; signature-verified path"
       bindings:
         customer_id:
           source: 'request.headers.get("x-customer-id", "")'
           confidence: confirmed
           confirmed_by: kritivas.shukla@moolabs.com
   ```

5. **Refuse to proceed if confirmations are missing.** Phase 2c reads `attribution-bindings.yaml` and aborts if any key the templates need is neither confirmed nor explicitly marked `source: null`. Fail loud — never silently substitute.

**Re-run semantics:** Phase 1.6 is incremental. If a binding for some key already exists with a recent `confirmed_at`, the script skips re-prompting unless `--reconfirm` is passed. New routes added since last run trigger override prompts only for those files.

### Phase 2: Generate the per-service `moolabs_client.py` helper (MANDATORY, ONCE per service)

**This MUST run before any call-site insert.** The codemod generates exactly ONE helper file per service that owns all SDK and OTel-span emission. Every call-site insert in Phase 2b imports from this helper — never instantiates `Moolabs(api_key=...)` inline.

**Why this is mandatory** (lessons from an early integration, 2026-05-25):

1. **One client per process, not one per call site.** Inline `Moolabs(api_key=...)` at every emission site creates N clients per request (one per emission), each with its own connection pool, each re-reading the secret from the secret store. The helper uses `@lru_cache(maxsize=1)` to make the client + key resolution true singletons.
2. **Fail-open-silent-swallow is one contract, enforced once.** If every call site implements its own `try/except`, the contract drifts. The helper exposes `emit_usage_event_safe()` / `emit_cost_event_safe()` — every call site uses them, every error path is identical.
3. **Secret resolution is per-customer.** AWS Secrets Manager / GCP Secret Manager / Vault / 1Password / env var — the helper template renders the right strategy from `04-final.signed.yaml > integration.sdk_key_location`. Call-site templates stay strategy-agnostic.
4. **The signoff chain is auditable from inside the code.** The helper's docstring header lists the 5 signed-stage sha256 hashes (cfo/pm/cfo/engineer/pm) — when an engineer reads the file 6 months later, the provenance is `head -20 services/<svc>/.../moolabs_client.py`, not "go ask git blame".
5. **Brownfield directive lives next to the code that would violate it.** If `telemetry.mode == brownfield`, the helper's top-of-file comment says "do NOT register a second TracerProvider" — the next person editing this file sees it.

**Where to write the helper:**

| Language | Path (relative to service root) |
|---|---|
| Python | `app/services/moolabs_client.py` (or `<package>/services/moolabs_client.py` matching service's existing module layout) |
| TypeScript | `src/services/moolabs-client.ts` |
| Go (v1.5) | `internal/moolabsclient/client.go` |

**What goes in the helper (generated from `assets/codemod-templates/<lang>-moolabs-client.<ext>.j2`):**

| Function | Purpose | Fail-open behavior |
|---|---|---|
| `_resolve_api_key()` | Read key from configured secret store; `lru_cache(maxsize=1)` singleton | Returns empty string on failure; logs `moolabs.sdk_key.resolution_failed` |
| `get_client()` | Singleton `Moolabs(...)` instance; `lru_cache(maxsize=1)` | First-call lazy; never raises |
| `emit_usage_event_safe(event_type, subject, data, ...)` | The ONLY surface for SDK emission. Per-call-site templates call this — they NEVER touch `client.usage.ingest_events()` directly | SDK errors logged + swallowed; workflow continues |
| `emit_cost_event_safe(kind, customer_id, cost_micros, attributes, ...)` | The ONLY surface for cost-event emission. Sibling-pair of `emit_usage_event_safe`. **Dual transport (added 2026-05-25 after an early integration run):** prefers OTel span attribute when a recording span exists; falls back to structured log (`logger.info("moolabs.cost.event", ...)`) when no recording span is available. Acute ingests both transports. | Cost events are **never silently dropped** when transport is unavailable. Only true exceptions (during both write AND log fallback) get swallowed. |

**Why dual transport** — the OTel-span-only design silently drops cost data for any code path the tracer doesn't sample. Concrete impact: head-sampling at 10% drops 90% of cost signal; background workers without trace-context propagation drop all of theirs; dev/CI without OTel drops everything. The function arguments carry the truth (kind, customer_id, cost_micros) — span vs log is just transport. Both arrive at the same ClickHouse `acute_analytics` table downstream; trace_id is salvaged from the span context even when the span isn't recording, so log-path events can still join the trace. The unified Moolabs SDK exposes its cost-event endpoint at `client.cost.ingest_events_batch` (CostEventsApi on the ACUTE backend — there is no separate "acute SDK"; the cost capability routes to `acute.{base_url}` internally, verified at source 2026-05-28). When the Phase 1.5 snapshot reports `cost_event_direct_emit=true`, the helper's PRIMARY transport is that direct SDK call on the `get_client()` singleton; the OTel-span write and structured-log path are the recovery rail. Call sites do not change.

**Codemod commit:** First commit on the branch is `feat(moolabs): generate per-service emission helper`. Reviewable in isolation before any business-logic file changes.

### Phase 2c: Build the task ledger (NEW — fan-out planning)

**Why this exists** (added 2026-05-25 after Codex review caught the per-pattern template bugs): the v0.1 codemod tried to render and apply ALL inserts inside one LLM context. That accumulated bugs (orphan `except` in sibling-pair, stale inline `_moolabs_client` in usage-only) because no single render was isolated enough to be parse-tested. Phase 2c breaks the work into independent units; Phase 2d dispatches each unit to its own focused subagent context.

Run `scripts/task_planner.py` against the inventories + the Phase 1.5 snapshot. Output: `.moolabs/codemod/tasks.yaml` with one task per `(file, [callsites in this file])` tuple. Each task is **self-contained** — it carries the inventory slice, the matching output-input-map edges, the helper template path, the adapter binding, and the SDK snapshot capability flags relevant to its callsites. No task ever needs the full inventory.

```yaml
# Example tasks.yaml entry
- task_id: tsk_001
  file: services/billing-api/app/agents/communications.py
  service_slug: <your-service>
  framework: fastapi
  language: python
  template: assets/codemod-templates/python-fastapi.j2
  helper_import: "from app.services.moolabs_client import emit_usage_event_safe, emit_cost_event_safe"
  snapshot_capabilities:
    cost_event_direct_emit: true
    cost_event_method_path: "client.cost.ingest_events_batch"
  inserts:
    - line: 729
      pattern: sibling-pair
      entry:                       # the inventory entry — JUST this one
        workflow_id: messaging.email.sent
        event_type: completion.delivered
        idempotency_anchor: { handler: compose_email, path_param: customer_id, confidence: 0.95 }
        refund_unit: { unit: email, derivation: "1" }
        cost_kind: llm-tokens
        cost_workflow_ids: [shared.llm.call]
        cost_micros_source: "response.cost_micros"
        consumer_agent_source: 'log_context["agent"]'
      attribution_keys: [request_id, customer_id, consumer_agent]
  audit:
    cost_events_inventory_sha: <sha of slice>
    output_input_map_sha: <sha of slice>
```

**Task granularity = per file.** Atomic commit boundary, single rendering pass per file (so `python -m py_compile` can verify the file before the task completes), parallelizable across files. Per-callsite would over-fragment; per-service would re-introduce the big-context problem the Codex review caught.

### Phase 2d: Dispatch tasks to focused subagent contexts

For each task in `tasks.yaml`, fire a subagent via the `Agent` tool with `subagent_type=general-purpose` and a focused prompt:

```
You are instrumenting ONE file. Your job:

1. Read the file at <task.file>.
2. For each insert in <task.inserts>, render the helper call by substituting
   the inventory entry into <task.template>.
3. Apply each insert immediately AFTER the source line specified in the
   inventory entry's idempotency_anchor.handler return path. Preserve all
   existing imports + business logic.
4. Add the helper import at the top of the file if not already present:
   <task.helper_import>
5. Run `python -m py_compile <file>` (or the language equivalent). If it
   fails, FIX the rendered output — do NOT proceed with broken Python.
6. Stage + commit the file with message: `feat(moolabs): instrument
   <basename> — <N> sibling-pair, <M> usage-only, <K> cost-only`.

You may NOT:
- Read other files in this service (your context is THIS file only).
- Load the full cost-events-inventory.yaml or usage-events-inventory.yaml
  (you have the slices you need).
- Instantiate Moolabs() inline at any call site.
- Call client.usage.* or client.cost.* directly (always through the helper).
- Skip the py_compile / typecheck step.

Report back ONE summary line per insert, plus the final commit SHA.
```

The dispatcher waits for each task to complete, collects the summary, and writes results to `.moolabs/codemod/execution-log.yaml`. Failed tasks (compile error, missing source line, sibling find of an existing helper import that conflicts) are recorded with `status: failed`, full diagnostic, and stay in the ledger — Phase 2e (NEW) is a retry pass that the human triggers explicitly.

**Why subagent isolation matters here**: per-template bugs caught by Codex (orphan `except`, the dead `client.meter.events` shape inline instead of `client.usage`) were rendering-time accidents in one giant context. With one file = one rendering pass = one syntax check, every defect surfaces immediately as a failed `py_compile` instead of polluting downstream tasks.

### Phase 2b (LEGACY name — superseded by Phase 2c/2d above)

For each insert, pick the right adapter and pattern. Every emission MUST go through the Phase 2 helper — `from app.services.moolabs_client import emit_usage_event_safe, emit_cost_event_safe`. No `Moolabs(api_key=...)` lines outside the helper file. (This section is retained as the per-pattern selection reference that the task planner uses.)

**Pattern selection (deterministic, from `output-input-map.yaml`):**

| Condition | Pattern | Helper calls emitted |
|---|---|---|
| Usage event has inputs AND inputs are within the same handler call subtree | sibling-pair | `emit_cost_event_safe(...)` + `emit_usage_event_safe(...)` |
| Usage event has no inputs in this handler (terminal-only) | usage-only | `emit_usage_event_safe(...)` only |
| Inputs exist but no usage event in this handler (subscription customer; infra hot path) | cost-only | `emit_cost_event_safe(...)` only — helper picks SDK or OTel-span transport per Phase 1.5 snapshot |

**Adapter selection (from `repo-profile.yaml`):**

| Language | Framework | Adapter | Template |
|---|---|---|---|
| Python | FastAPI | OTel via OpenTelemetry-API auto-instrumentation; request_id from `request.state` middleware | `assets/codemod-templates/python-fastapi.j2` |
| Python | Django | OTel via django-instrumentation; request_id from `HttpRequest.META` | `assets/codemod-templates/python-django.j2` |
| Python | Flask | OTel via flask-instrumentation; request_id from `flask.g` | `assets/codemod-templates/python-flask.j2` |
| TypeScript | Express | OTel via @opentelemetry/instrumentation-express; request_id from `req.headers['x-request-id']` | `assets/codemod-templates/typescript-express.j2` |
| TypeScript | NestJS | OTel via @opentelemetry/instrumentation-nestjs-core; request_id from request scope | `assets/codemod-templates/typescript-nestjs.j2` |
| TypeScript | Next.js | OTel via @vercel/otel; request_id from middleware | `assets/codemod-templates/typescript-nextjs.j2` |
| Go (v1.5) | net/http stdlib | otel-go; request_id from context | `assets/codemod-templates/go-stdlib.j2` (placeholder for v1) |
| any | Litestar / Tornado / others | **No v1 adapter** | Insert `# TODO: framework=<x> has no v1 adapter; manual instrumentation required` |

**Idempotency-key derivation (v1 heuristic, per `v1-decisions-log.md`):**

```python
idempotency_key = f"{handler_name}.{first_id_path_param}.{int(time.time())}"
```

Where `first_id_path_param` is heuristically the first path parameter named `*_id` or `id`. If none, fall back to `{handler_name}.{epoch_millis}` and emit a `REVIEW: idempotency key derivation` comment.

**Trace-context extraction (per `references/trace-context-providers.md`):**

The codemod inserts OTel attribute writes onto the *current span*. The current span is retrieved via the per-framework adapter (e.g., `opentelemetry.trace.get_current_span()` for Python, `trace.getActiveSpan()` for TypeScript). No new spans are created unless the call site lacks one.

**Error/failure path coverage:**

- Don't emit usage events on errors by default (per requirements §4.3). Wrap in `try/except` or `try/catch`.
- Cost events still fire on errors (they reflect spend that already happened).
- Partial-stream collapse → emit a single usage event at stream-complete; cost events fire as tokens flow.

**PII guard:**

The codemod refuses to write any span attribute that matches `request.headers.authorization`, `*.api_key`, `*.password`, `*.secret`, or values that look like API keys (`/^(sk|pk)_[a-z]+_[A-Za-z0-9]{20,}$/`). If detected in inventory metadata, write a redacted placeholder and surface a CRITICAL severity finding for adversarial review.

### Phase 3: Generate the PR(s)

Run `scripts/pr_writer.py`. Output per chunk:

- A new branch `moolabs/instrument-<service>-<short-sha>`
- A commit per file (atomic, reviewable)
- A PR description with:
  - Summary: N inserts, M files, K cost-only TODOs, latency profile per insert (cite `sdk-surface-reference.md` ~35ms).
  - **Pre-merge checklist:**
    - SDK install commands — codemod reads `04-final.signed.yaml > integration.sdk_package_install` for the EXACT commands per language (default: latest **stable** GitHub release tag, filtered via `grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$'` to exclude `-rc*`/`-beta*`/`-alpha*` prereleases that `sort -V` otherwise sorts AFTER stable). Codemod does NOT run these (per `v1-decisions-log.md`). **SDKs are NOT on public registries — never emit `pip install moolabs` / `npm install moolabs` / `go get moolabs.com/sdk` (all 404 as of 2026-05-25).** See `cost-billing-shared/sdk-surface-reference.md` §"Install" for the canonical commands. Codemod falls back to the canonical commands if customer-context lacks `sdk_package_install` (warns prominently in the PR description that the customer should run team-engineer bootstrap Q16 to lock the install path).
    - **Go-specific (until upstream go.mod is fixed):** When `repo.languages[]` includes `go`, the codemod MUST emit the `require` + `replace` directives from `cost-billing-shared/sdk-surface-reference.md` §"Go" **verbatim in the PR pre-merge note** — bare `go get github.com/moolabs-hq/moolabs-go@latest` fails today with "module declares its path as: github.com/moolabs/moolabs-go". Customer's import statements use `github.com/moolabs/moolabs-go` (the module path) NOT the repo path. Codemod templates for Go must use this import path.
    - **Pipeline prerequisites (accepted v1 risk):** The default install pipeline assumes `git`, `awk`, `grep`, `sort` are on the customer's PATH. Minimal containers (Alpine without `apk add git`, distroless) will fail — customer-context's Q16 lets the customer override with `strategy: custom` + a verbatim command for their environment. PR pre-merge note documents this dependency.
    - Run `pytest` (or equivalent) — codemod does NOT run this.
    - Verify three-role signoff files still present + unchanged since codemod ran.
  - **Latency note:** "The Moolabs SDK is blocking by design (~35ms typical round-trip). Hot-path callers may want to background-wrap; this codemod chose Option B (blocking + documented) per the v1 default. See `cost-billing-shared/sdk-surface-reference.md`."
  - **TODOs:** the list of cost-only-blocked annotations.
  - **Idempotency review:** the list of `REVIEW: idempotency key derivation` comments.

If `--dry-run`, write everything to `.moolabs/codemod/dry-run/` instead of opening branches.

### Phase 4: Hand off to /cost-billing-adversarial-review (invocation 6 of 6)

The codemod itself is not the final gate. After the PR(s) are emitted, invoke `/cost-billing-adversarial-review` with `--phase post-codemod --pr <pr-url>`. Per requirements §9, this is the 6th and final Skill R invocation per pipeline run.

If the post-codemod review finds CRITICAL or HIGH issues, apply fixes (Phase 3 of `/cost-billing-adversarial-review`) and re-emit.

## Degraded modes

| Condition | Behavior |
|---|---|
| Framework adapter missing (e.g., Litestar) | Insert `// TODO: framework=<x> has no v1 adapter; manual instrumentation required` with the suggested call shape. Do not break compilation. Flag CRITICAL for adversarial review. |
| Confirmed entry's `file:line` is stale (code moved since Skill A ran) | Flag, do not edit. Surface in PR as "REGENERATE: file:line drift detected". Hand off to drift-lint. |
| Cost-only pattern (subscription customer) | Insert `emit_cost_event_safe()` call. When the snapshot reports `cost_event_direct_emit=true`, the helper's primary transport is the direct `client.cost.ingest_events_batch` call (OTel span + structured log are the recovery rail). **Only** if the pinned SDK snapshot exposes no cost method, fall back to OTel-span-only and annotate `# TODO: pinned SDK exposes no cost endpoint; re-run codemod after upgrading the SDK`. |
| Existing OpenLLMetry / Helicone / Langfuse span | Extend existing span with `moolabs.*` attributes; do not wrap or duplicate (brownfield branch). |
| Idempotency key heuristic fails (no path param, no domain identity) | Insert key fallback `{handler}.{epoch_millis}` + comment `// REVIEW: idempotency key derivation — domain identity not detected`. Flag MEDIUM for adversarial review. |
| PR would exceed 30 files | Chunk by service; emit multiple PRs + an index PR. |

## What this skill MUST NOT do

- **Never** call `EventsApi` or `MetersApi` directly — always go through `client.usage.*` (per `sdk-surface-reference.md`).
- **Never** use `client.usage.*` — that namespace does not exist.
- **Never** silently skip a confirmed inventory entry — flag and continue.
- **Never** double-wrap existing instrumentation — brownfield branch extends, doesn't wrap.
- **Never** log span attributes that introduce PII / security footguns — PII guard refuses.
- **Never** run customer build commands — PR carries a "to run before merge" note.

## Reference files (load on demand)

- `references/codemod-patterns.md` — sibling-pair / usage-only / cost-only deep-dive.
- `references/trace-context-providers.md` — per-framework adapter details (OTel / Datadog / Sentry / custom).
- `references/idempotency-derivation.md` — heuristic + edge cases + the §6.4 #23 open question.
- `references/sdk-blocking-rationale.md` — why Option B for v1.
- `references/pr-chunking.md` — service-grouped chunking strategy.
- `references/pii-guard.md` — the patterns + the test fixtures.

## Scripts

- `scripts/codemod_driver.py` — Phase 1 plan; Phase 2 dispatch to per-language driver.
- `scripts/python_adapter.py` — Python AST rewriter using `libcst`.
- `scripts/typescript_adapter.py` — TS AST rewriter using `ts-morph`.
- `scripts/trace_context_detect.py` — pick the right trace-context provider per framework.
- `scripts/idempotency_derive.py` — the heuristic + fallback.
- `scripts/pr_writer.py` — branch + commit + PR description.

## Assets

- `assets/codemod-templates/python-fastapi.j2`
- `assets/codemod-templates/python-django.j2`
- `assets/codemod-templates/python-flask.j2`
- `assets/codemod-templates/typescript-express.j2`
- `assets/codemod-templates/typescript-nestjs.j2`
- `assets/codemod-templates/typescript-nextjs.j2`
- `assets/codemod-templates/go-stdlib.j2` (placeholder until v1.5)
- `assets/pii-patterns.yaml` — the regex set the PII guard uses.
