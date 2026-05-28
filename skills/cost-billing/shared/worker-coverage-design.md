# Design — comprehensive scan coverage: workers, consumers, schedulers

**Status:** DESIGN (no code). Authored 2026-05-28 after a moo-meter dogfood run
surfaced that the discovery scan ignored the Kafka ingest consumer entirely.
**Owner decision pending:** approve the cost-call-anchored pivot before any
implementation (see "Phased task list" + "Open questions").

---

## 1. Problem

The discovery + instrument pipeline is **HTTP-request-shaped at every layer**. Any
emission site that is not inside an HTTP request handler — background queue workers,
stream consumers, scheduled jobs, CLI batch jobs, in-process background tasks — is
invisible to the scan, un-attributable, and un-instrumentable.

This is not a missing config entry. Workers are dropped at **all five layers**:

| Layer | File:line | What it does today | Worker outcome |
|-------|-----------|--------------------|----------------|
| 1. Repo-shape detection | `discovery/scripts/repo_scan.py:51-75` | Matches only HTTP-server framework signatures (fastapi/django/flask/tornado/litestar/aiohttp; express/nestjs/nextjs/fastify/koa/hono; gin/echo/fiber/chi) | A worker-only service reports **no framework** → deprioritized/treated as non-instrumentable |
| 2. Cost-call match | `discovery/SKILL.md:269` (`catalog_match.py`, agent-driven; `provider-catalog.starter.yaml`) | Catalog patterns ARE context-agnostic (good), but the agent is steered by SKILL.md's HTTP framing | Vendor call inside a Celery task *may* be found, but nothing downstream can use it |
| 3. Terminal-event / refund test | `discovery/assets/terminal-event-heuristics.yaml` | Scores HTTP handlers: URL patterns (SIGNAL 5), `http_method` (SIGNAL 6); `consumer_weight: 0.05` vs `server_weight: 0.25` | Worker-emitted usage events (e.g. `transcription_completed` in a task) score below the 0.50 threshold |
| 4. Attribution sourcing | `instrument/scripts/attribution_discovery.py:62-69` | Extractors are 100% request-object: `request.state.X`, `request.META[...]`, `req.headers[...]` | A worker has no `request` object → cannot source `customer_id` from a job payload, Kafka header, or task arg |
| 5. Emission templates | `instrument/assets/codemod-templates/*.j2` | 6 HTTP adapters only | No worker template — nothing to instrument a `@celery.task` / consumer loop with |

**The irony:** `instrument/SKILL.md:319` justifies the entire dual-transport
never-drop contract with "background workers without trace-context propagation
drop all of theirs." The skill built the safety net *for* workers but never built
the on-ramp that routes worker call sites onto it.

**Concrete moo-meter miss:** moo-meter is an ingest → transform → store → query
pipeline. The scan would report the HTTP query API's framework and silently skip
the Kafka ingest sink (`kafkaingest` / `sink_to_storage`), a stream consumer —
which is where every ingested event (the densest cost/usage signal) flows.

---

## 2. Root cause

The scan is **framework-anchored**: it answers *"what HTTP framework is this
service?"* The right question for a cost/billing instrumenter is:

> **Where are the billable operations, and what execution context wraps each one?**

Framework-anchoring structurally cannot see anything that is not an HTTP server.
Across arbitrary customer codebases the *most expensive* operations (LLM batch
jobs, transcoding, ETL, embeddings backfills) live disproportionately in workers.

---

## 3. Design principle: cost-call-anchored discovery + execution-context classification

Two moves:

### 3a. Anchor on the cost call, not the framework
The `provider-catalog.starter.yaml` already encodes what an expensive call looks
like per language (`openai.chat.completions.create`, `anthropic.messages.create`,
`replicate.run`, `boto3 ... invoke_model`, …). Find those call sites **anywhere in
the repo**, regardless of what wraps them. This is library-count-independent — you
are not chasing the next queue framework, you are anchoring on the cost event,
which is the actual deliverable. New queue libs need zero new detectors.

### 3b. Classify the enclosing execution context
For each detected cost/usage call site, walk **up** the AST/call-graph to classify
the enclosing entry point into a fixed taxonomy. The execution context is the join
key between *where the cost happens*, *how to attribute it*, and *which transport
is viable*.

**Execution-context taxonomy (the new vocabulary):**

| Context | Detection shape (examples) | Attribution source | Default transport |
|---------|---------------------------|--------------------|-------------------|
| `http_request` | route decorator / handler in an HTTP framework (current sole coverage) | `request.state` / headers / `META` | span preferred (usually traced) |
| `queue_worker` | `@celery.task`, `@shared_task`, `@dramatiq.actor`, `@huey.task`, RQ `Queue.enqueue` target, BullMQ `new Worker()`, Sidekiq `perform`, `asynq` handler | the task fn args / job payload dict | **log-rail primary** (rarely traced) |
| `stream_consumer` | Kafka `consumer.run` / `@KafkaListener` / kafkajs `eachMessage`, Kinesis/PubSub/NATS/Redis-streams loop | message key / headers / value fields | **log-rail primary** |
| `scheduled_job` | Celery beat, `@scheduler.scheduled_job`, node-cron, k8s CronJob entrypoint | usually none per-invocation → derive per-row from the data processed | log-rail primary |
| `cli_batch` | `argparse`/`click`/`cobra` main; one-shot script | argv / config / per-row data | log-rail primary |
| `background_task` | `asyncio.create_task`, threadpool submit, FastAPI `BackgroundTasks` | inherited from spawning request if capturable, else payload | span if context propagated, else log |

Context drives two decisions the current design hardcodes to `http_request`:
1. **Attribution model** — which extractor set to apply (request vs payload vs message vs data-row).
2. **Transport default** — workers usually lack a recording span, so the structured-log rail is *primary*, not fallback. (Alternatively the worker template can *open* a span first; see Open Questions.)

---

## 4. Per-layer changes

### Layer 1 — `repo_scan.py`: add a worker/runtime axis, decouple from "framework"
- Keep HTTP framework signatures. **Add** an `execution_runtimes` detection axis:
  worker/queue/scheduler/stream library signatures (celery, rq, dramatiq, huey, arq,
  kombu; bullmq, bee-queue, agenda, node-cron, kafkajs, @nestjs/microservices;
  asynq, machinery, sarama/segmentio-kafka, robfig/cron).
- A service with **no HTTP framework but a worker runtime** must be flagged
  instrumentable, not skipped. The `ServiceScan` dataclass (`repo_scan.py:106`) gains
  `execution_runtimes: list[str]` alongside `frameworks_detected`.
- Signatures are a *hint*, not the gate — Layer 2 is the real detector.

### Layer 2 — cost-call scan becomes context-aware (`catalog_match.py`, currently agent-driven/absent)
- Run the provider-catalog AST patterns over **all** source files (not just files in
  HTTP routers). For each hit, run the **context classifier** (3b) to tag
  `execution_context`.
- Output gains `execution_context` per call site. This is the field everything
  downstream branches on.
- Note: `catalog_match.py` / `refund_test.py` / `inventory_build.py` are referenced in
  `discovery/SKILL.md:269-271` but **do not exist on disk** — Phases 3-5 run
  agent-guided today. This design assumes we either materialize those scripts or
  give the agent explicit context-classification instructions in SKILL.md. (Decide in
  Open Questions.)

### Layer 3 — terminal-event heuristics: context-aware scoring
- `terminal-event-heuristics.yaml` already has `producer_weight: 0.10` /
  `consumer_weight: 0.05` (SIGNAL 2) — vestigial worker awareness. Promote it: when
  `execution_context != http_request`, the HTTP-only signals (SIGNAL 5 URL patterns,
  SIGNAL 6 http_method) are **N/A**, not negative; add worker-shaped signals (task
  name verbs already partly covered: `_published`, `_sent`, `_transcribed`; message
  topic name; "emit on ack/commit" shape).
- Add a 5th refund-test scenario: `stream/worker` alongside agentic/streaming/hybrid/
  subscription (`terminal-event-heuristics.yaml:128`).

### Layer 4 — attribution: non-request source extractors (`attribution_discovery.py`)
- Today every regex targets `request.*` (`attribution_discovery.py:62-69`). Add an
  extractor set per execution context:
  - `queue_worker`: task-fn parameter / `payload["customer_id"]` / `kwargs.get(...)`.
  - `stream_consumer`: `message.headers["customer_id"]` / `message.key` / a field in
    the deserialized value. (moo-meter carries `tenant_id` as a **Kafka header** — see
    the global testing-pipeline rule; the consumer's attribution source is that header,
    not a request.)
  - `scheduled_job` / `cli_batch`: per-row field in the data being processed; if no
    per-invocation identity exists, mark the site cost-only/internal.
- The interactive Phase 1.6 confirmation prompt becomes context-specific: instead of
  "which middleware sets request.state.customer_id?" it asks "this Celery task — which
  payload key carries customer_id?"

### Layer 5 — emission: worker codemod templates
- New templates keyed by `(language, execution_context)`:
  - `python-celery.j2`, `python-stream-consumer.j2` (Kafka/generic), `python-generic-worker.j2`
  - `typescript-bullmq.j2`, `typescript-kafkajs.j2`, `typescript-generic-worker.j2`
- Worker templates differ from HTTP templates in two ways:
  1. Attribution comes from the payload/message, not a request object.
  2. Transport default is **log-rail primary** (no recording span), OR the template
     opens a span around the task body first so the span path stays primary — decision
     in Open Questions.
- drift-lint must learn the worker call-site shapes too (so it doesn't flag
  worker-instrumented sites as drift).

---

## 5. Phased task list

| Phase | Scope | Effort | Risk | Depends on |
|-------|-------|--------|------|------------|
| **W0** | Land the `execution_context` taxonomy as shared vocabulary (`anchor-taxonomy.md` + a schema enum). No detection yet — just the contract every layer will branch on. | S | Low | — |
| **W1** | `repo_scan.py`: add `execution_runtimes` axis + signatures; stop skipping HTTP-less worker services. Unit tests per runtime. | M | Low | W0 |
| **W2** | Context classifier: given a cost/usage call site, walk up AST to emit `execution_context`. Python first (decorators + known consumer loops), then TS, then Go. This is the core new capability. | L | **High** (AST heuristics, false positives) | W0 |
| **W3** | Wire W2 into the Phase-3 cost-call scan (materialize `catalog_match.py` or give SKILL.md explicit classify instructions); add `execution_context` to the cost/usage inventories. | M | Med | W2 |
| **W4** | `attribution_discovery.py`: per-context extractor sets + context-specific Phase 1.6 prompts. | M | Med | W0, W3 |
| **W5** | Worker codemod templates (celery, kafka/stream-consumer, generic) for Python + TS; transport-default decision applied. | L | Med | W4, Open-Q transport |
| **W6** | Terminal-event heuristics: context-aware scoring + `stream/worker` refund scenario. | S | Low | W0, W3 |
| **W7** | drift-lint worker call-site detection; extend smoke matrix with worker template renders (mirror the existing per-framework × per-pattern matrix). | M | Low | W5 |

Recommended order: **W0 → W1 → W2** (de-risk the classifier early — it gates W3-W7).
W2 is the make-or-break; if AST classification proves too noisy, fall back to the
stopgap (worker library signatures + templates without true call-anchoring).

---

## 6. Open questions (need a decision before W5)

1. **Worker transport default.** Two options:
   (a) log-rail primary for all non-`http_request` contexts (simplest; matches
   "workers aren't traced"); or
   (b) worker template opens a span around the task body so the span path stays
   primary and trace correlation is preserved. (b) gives better observability but
   assumes the worker process has an OTel SDK configured. **Lean: (a) default, (b)
   opt-in when Phase 1 detects an OTel runtime in the worker.**
2. **Materialize the absent Phase 3-5 scripts** (`catalog_match.py` etc.) vs keep
   Phases 3-5 agent-driven with stronger SKILL.md instructions? Materializing makes
   context classification testable in the smoke suite (preferred), but is more code.
3. **Scheduled/CLI jobs with no per-invocation identity.** When a cron job processes
   N customers' rows, is each row a separate usage event (per-row attribution) or is
   the job itself cost-only/internal? Likely per-row, but confirm the attribution
   model can express "loop variable carries customer_id."
4. **Go worker coverage** — Go is v1.5 for HTTP already; do workers wait for v1.5 or
   ship Python+TS workers in v1.x?

---

## 7. Relationship to Error B (usage-namespace inversion) — RESOLVED 2026-05-28

The usage-namespace prose was inverted across the suite (docs said
`client.meter.events.*` was live and `client.usage.*` was the anti-pattern; source
proves the opposite). **Reconciled 2026-05-28** against the oracle
`sdk-surface-reference.md:147` — all guidance now points to `client.usage.*`, and
drift-lint's anti-pattern list is flipped. W5 worker templates therefore use the
correct `client.usage.ingest_events` / `client.usage.ingestEvents` shape; no longer
a blocker.
