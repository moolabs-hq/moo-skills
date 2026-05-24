---
name: cost-billing-instrument
description: >-
  The Cost+Billing suite's CORE DELIVERABLE — a codemod that wires cost ingest events (OTel spans with moolabs.* attributes) and usage ingest events (client.meter.events.ingest_events) into customer code, based on the three confirmed inventories from cost-billing-discovery. Generates reviewable per-service PRs (max 30 files each) with correct trace/span context, idempotency keys derived from domain identity, lifecycle handling for success/error/partial-stream paths, framework adapters per stack (Python+FastAPI/Django/Flask, TypeScript+Express/NestJS/Next.js — Go v1.5), and PII guards. Implements three patterns — sibling-pair (default), usage-only, cost-only (TODO-annotated for v1 since client.acute.* namespace not yet exposed). Default insert mode is BLOCKING (Option B per §10 #4) with PR documenting ~35ms latency. Only runs after all three role signoffs + holistic Skill R verdict. Triggers on "run the codemod", "instrument this repo", "wire SDK calls", "Skill 2".
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
/cost-billing-instrument /path/to/customer/repo --service moo-acute --dry-run
/cost-billing-instrument /path/to/customer/repo --service moo-acute --pattern usage-only
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

- `sdk-surface-reference.md` — **load this every time.** It carries the verified call shapes (`client.meter.events.ingest_events`, cost-via-OTel pattern, what NOT to call).
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
  cost_only_blocked: 5   # annotated as TODO blocked on acute SDK
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
  - "5 cost-only inserts are TODO-annotated (blocked on acute SDK per §10 #3)"
  - "2 entries have framework=litestar (no v1 adapter); inserting TODO comments"
```

**Always show the plan to the user before proceeding.** If they say "go", continue to Phase 2.

### Phase 2: Per-pattern insert (the actual codemod)

For each insert, pick the right adapter and pattern.

**Pattern selection (deterministic, from `output-input-map.yaml`):**

| Condition | Pattern | What gets emitted |
|---|---|---|
| Usage event has inputs AND inputs are within the same handler call subtree | sibling-pair | One `client.meter.events.ingest_events([...])` call; cost is emitted as OTel span attributes on the existing or new span (e.g. `moolabs.cost.kind=openai-tokens`) |
| Usage event has no inputs in this handler (terminal-only) | usage-only | Just `client.meter.events.ingest_events([...])` |
| Inputs exist but no usage event in this handler (subscription customer; infra hot path) | cost-only **BLOCKED v1** | OTel-only emission + `# TODO: blocked on acute SDK (§10 #3)`; Skill R surfaces |

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
    - `pip install -U moolabs` (or `npm install moolabs@latest`, or `go get moolabs.com/sdk`) — codemod does NOT run this (per `v1-decisions-log.md`).
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
| Cost-only pattern (subscription customer; acute SDK absent) | Insert OTel-span emission + `# TODO: blocked on acute SDK (§10 #3); replace with client.acute.events.ingest_events when namespace ships`. |
| Existing OpenLLMetry / Helicone / Langfuse span | Extend existing span with `moolabs.*` attributes; do not wrap or duplicate (brownfield branch). |
| Idempotency key heuristic fails (no path param, no domain identity) | Insert key fallback `{handler}.{epoch_millis}` + comment `// REVIEW: idempotency key derivation — domain identity not detected`. Flag MEDIUM for adversarial review. |
| PR would exceed 30 files | Chunk by service; emit multiple PRs + an index PR. |

## What this skill MUST NOT do

- **Never** call `EventsApi` or `MetersApi` directly — always go through `client.meter.events.*` (per `sdk-surface-reference.md`).
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
