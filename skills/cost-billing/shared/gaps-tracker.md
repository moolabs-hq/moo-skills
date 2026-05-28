# Gaps tracker — §6 open questions, status as of 2026-05-19

Every gap from requirements doc §6 is here, with v1 status. Mark "RESOLVED" when v1 ships a path; "OPEN" when HLD must decide; "DEFERRED" when explicitly out of v1 scope.

---

## §6.1 — Tracked in Doc 3 (`3ef884d4`)

| Doc 3 | Question | v1 status | Path |
|---|---|---|---|
| §1.1 §4.1 | Refund-test edge cases beyond 4-scenario validation | **RESOLVED** (validated 2026-05-11) | Promoted to "validated" per requirements §2. |
| §1.2 | Idempotency dedup window + replay semantics | **OPEN** (HLD) | v1 codemod uses heuristic: `{handler}.{first-id-path-param}.{epoch_second}`. Surfaces in PR comment "REVIEW: idempotency key derivation". |
| §1.3 | Long-running / mid-stream billing pattern | **OPEN** (HLD) | v1 codemod targets stream-complete (single emission). Per-tick / heartbeat patterns deferred. |
| §1.4 | Time mismatch between cost & billing at invoice time | **DEFERRED** (Skill C internal) | Skill C harness handles per-period bucketing; surfaces drift in WAPE breakdown. |
| §1.7 | Internal "billing event" terminology collision | **DEFERRED** (cleanup) | Suite uses "usage event" externally. Internal `BillingEvent` rename not blocking. |
| §2.4 | Drift-lint trust asymmetry | **OPEN** (HLD) | v1 ships a per-PR coverage report alongside the delta; customers can grade trust. |
| §3.4 | `@colbymchenry/codegraph` capability ceiling | **OPEN** (engineering spike) | v1 uses Tree-sitter + per-language AST parsers; codegraph integration as v1.5 follow-up. |
| §3.6 | Integrator review UX | **RESOLVED for v1** (markdown PR + YAML inventories + static HTML preview). | Web UI = v2. |
| §5.1 | Reviewer-model persona for Skill R | **RESOLVED for v1** (cross-model: codegen ≠ reviewer model). | If only one model available, log a warning. |
| §5.2 | Per-integration cost budget for Skill R iterations | **RESOLVED for v1** (hard cap = 5 rounds; severity stop at "no HIGH+"). | Tune after 10 invocations. |
| §5.3 | Phase 4 robustness-sweep scope cap | **RESOLVED for v1** (graph-hop radius = 2). | Phase 4 expands sibling routes, sibling handlers, immediate callers/callees. |
| §5.4 | Severity rubric | **RESOLVED for v1** (CRITICAL = data corruption / compilation break; HIGH = wrong attribution / missing emission; MEDIUM = wrong adapter, low-confidence accept; LOW = style, idempotency-derivation suboptimal). | |
| §5.5 | Phase 5 convergence guarantee | **RESOLVED for v1** (5-round cap + human escalation). | |
| §5.6 | Review spec location vs. customer IP policy | **RESOLVED for v1** (default `docs/superpowers/reviews/` in customer repo; `--review-spec-out=<external-path>` flag for opt-out). | |
| §6.1 | SDK blocking → codemod insert default | **RESOLVED for v1** (Option B = blocking + documented). | |
| §6.3 | Unified SDK cost-event endpoint | **RESOLVED — endpoint shipped.** `client.cost.ingest_events_batch` (CostEventsApi + SdkIngestApi on the `acute` backend per `_dx_routing.CAPABILITY_MAP`), verified at source 2026-05-28. The dual-transport helper uses the direct SDK call as primary transport when the Phase 1.5 snapshot reports `cost_event_direct_emit=true`; OTel span + structured log are the recovery rail (never-drop). One unified client — no separate "acute SDK". | See `sdk-surface-reference.md` §"Direct cost-event emission". |
| §7.2 | Subscription-customer cost-only emission path | **RESOLVED** via the same dual-transport helper — `emit_cost_event_safe()` works for cost-only call sites too. With the cost endpoint shipped, cost-only sites emit the direct `client.cost.ingest_events_batch` call when `cost_event_direct_emit=true`; the `# TODO` annotation remains only when a customer's pinned SDK predates the endpoint. | |

---

## §6.2 — Skill B (Cloud bill integration) new gaps

| # | Question | v1 status | Path |
|---|---|---|---|
| 1 | Multi-account / multi-org setup | **OPEN** (HLD) | v1 supports single-account; multi-account = v1.5. AWS Organizations support is the highest-leverage first add. |
| 2 | Permissions and IAM | **OPEN** (HLD) | v1 documents minimum permission set per cloud; does not create IAM roles automatically. Customer creates and provides ARN/SA email. |
| 3 | First-export wait UX | **RESOLVED for v1** (Skill A runs in parallel; Skill B parks with a "come back in 24-48h" status file). | |
| 4 | Cell ③ severity / action | **RESOLVED for v1** (always surface; no threshold). | Per `v1-decisions-log.md` #10. |
| 5 | Tag-schema enforcement | **OPEN** (HLD) | v1 recommends Moolabs schema; if customer has existing schema, generate a mapping report (no auto-rewrite). |
| 6 | Cross-cloud customers | **OPEN** (HLD) | v1 handles each cloud sequentially; cross-cloud tag consistency = warning, not error. |
| 7 | Re-running Skill B after tags change | **RESOLVED for v1** (cell ③ list auto-re-scans on next export delivery if `--watch` flag set). | |
| 8 | Empty-export / no-spend | **RESOLVED for v1** (emit "no findings; re-run after 30 days" + record export config in `.moolabs/cloud-bill-config.yaml`). | |
| 9 | Cost-allocation rule selection | **OPEN** (HLD) | v1 default = proportional_usage; PM overrides per-service in three-role review. |

---

## §6.3 — Skill C (Reconciliation validation) new gaps

| # | Question | v1 status | Path |
|---|---|---|---|
| 10 | Customer NDA template | **OPEN** (legal) | Required before Phase 2. Not skill-suite-blocking, but Phase 2 cannot start without it. |
| 11 | Local-only run model | **MOVED OUT** | Skill C is no longer in this customer-portable suite (engineering-internal Moolabs infrastructure). Local-only run model is now a question for the separate Moolabs-internal validation harness, not this suite. |
| 12 | Corpus retention policy | **DEFERRED** (legal) | v1 = customer-opt-in 1-year; per-customer purge-on-request. GDPR/CCPA review at GA. |
| 13 | CI runtime cost | **OPEN** (engineering) | v1 = no sampling; full re-run on every PR. Sampling if runtime > 30 min. |
| 14 | WAPE/Coverage thresholds per service/pattern | **OPEN** (HLD) | v1 = uniform (10% / 80%); per-service thresholds = post-GA. |
| 15 | Algorithm versioning | **OPEN** (HLD) | v1 = forward-only (no historical re-attribution); flagged in customer-facing release notes when algorithm changes. |
| 16 | Phase 1 success criteria | **RESOLVED for v1** (all services within 10%/80% gate; per-service overrides logged). | |
| 17 | Phase 3 customer-onboarding-gate UX | **OPEN** (HLD) | v1 = soft-launch with caveats (customer sees the per-service confidence map); customer decides go-live. Hard block = v2. |
| 18 | Customer-facing exposure of Skill C | **RESOLVED for v1** (engineering-internal only; aggregated per-customer reports on-request; no public dashboard). | Per `v1-decisions-log.md` #9. |

---

## §6.4 — Skill 2 (Codemod) new gaps

| # | Question | v1 status | Path |
|---|---|---|---|
| 19 | Language scope for v1 | **RESOLVED for v1** (Python + TypeScript). Go = v1.5. Java = v2. | Per `v1-decisions-log.md` #2. |
| 20 | Codemod review surface (large diffs) | **RESOLVED for v1** (chunk by service; max 30 files per PR; multi-PR if needed with index PR). | |
| 21 | Revert / rollback model | **RESOLVED for v1** (`git revert`; no regenerate-removing-feature-X). | Per `v1-decisions-log.md`. |
| 22 | Coexistence with existing instrumentation | **RESOLVED for v1** (Skill A detects OpenLLMetry / Helicone / Langfuse / OTel; Skill 2 extends existing spans with `moolabs.*` attributes — does not double-wrap). | |
| 23 | Idempotency-key derivation policy | **PARTIALLY RESOLVED for v1** (heuristic = `{handler}.{first-id-path-param}.{epoch_second}`; PR comment "REVIEW: idempotency key derivation" surfaces every insert). | Doc 3 §1.2 stays open. |
| 24 | Background-wrap default | **RESOLVED for v1** (Option B = blocking). | Per `v1-decisions-log.md` #4. |

---

## §6.4a — Three-role review gaps

| # | Question | v1 status | Path |
|---|---|---|---|
| 19j | Disagreement resolution | **RESOLVED for v1** (per-dimension final say: CFO=price, PM=mapping, engineer=code; overlap → re-propose; full conflict → holistic Skill R). | |
| 19k | Asynchronous review | **RESOLVED for v1** (yes; state persists; later changes re-open earlier views). | |
| 19l | Many-to-many output↔input | **RESOLVED for v1** (graph schema, not paired lists; per-edge weight + confidence). | |
| 19m | Fair-usage data placement | **RESOLVED for v1** (carried in `usage-events-inventory.yaml`). | |
| 19n | PM mapping persistence | **RESOLVED for v1** (match by `workflow_id` per Doc 3 §3.8). | |
| 19o | Linkage confidence | **RESOLVED for v1** (per-edge confidence separate from per-entry). | |

---

## §6.4b — Framework-on-unknown-repo gaps

| # | Question | v1 status | Path |
|---|---|---|---|
| 19b | Repo-shape discovery | **RESOLVED for v1** (manifest-based; falls back to monorepo). | |
| 19c | Language detection | **RESOLVED for v1** (manifest-first; extension fallback; multi-language services = independent scans). | |
| 19d | Existing-SDK detection | **RESOLVED for v1** (detect existing `moolabs` import; ask "upgrade in place" / "fresh re-instrument"). | |
| 19e | Read/write permissions | **RESOLVED for v1** (read = entire repo; write = single new branch + PR; dry-run default ON first invocation). | |
| 19f | "Optimal manner" optimization target | **RESOLVED for v1** (coverage-first; latency secondary). | Per `v1-decisions-log.md` #1. |
| 19g | Greenfield vs. brownfield | **RESOLVED for v1** (codemod branches: brownfield extends existing spans; greenfield introduces full SDK + OTel). | |
| 19h | Build-system integration | **RESOLVED for v1** (codemod does NOT run build; PR adds "to run before merge: install + test"). | |
| 19i | Framework invocation surface | **RESOLVED for v1** (CLI, local-only). | Per `v1-decisions-log.md` #5. |

---

## §6.5 — Cross-cutting gaps

| # | Question | v1 status | Path |
|---|---|---|---|
| 25 | Cost-event endpoint on unified SDK | **RESOLVED — shipped.** `client.cost.ingest_events_batch` verified at source 2026-05-28; the helper's primary cost transport is the direct SDK call when `cost_event_direct_emit=true`, OTel span + structured log as the recovery rail. Call sites unchanged. | — |
| 26 | Catalog miss | **RESOLVED for v1** (surface for review as cell ④ findings; never silent skip). | Per `v1-decisions-log.md` #6. |
| 27 | Skill R applies to B and C? | **RESOLVED for v1** (Skill R applies to Skill B's first-export scan; Skill R does NOT apply to Skill C — Skill C is itself a validation skill). | |
| 28 | Versioning of chargeability map | **RESOLVED for v1** (match by `workflow_id`; rename = stable ID; split/merge = parent retire + child reference). | |
| 29 | Multi-tenant / multi-environment | **RESOLVED for v1** (one map per environment; cross-env consistency = warning). | |
| 30 | Cell ③ + Tier 5 reconciliation | **RESOLVED for v1** (in Skill C Phase 1 scope). | |

---

## Net open after v1 ships

These remain unresolved and should drive HLD's agenda:

- **§1.2** Idempotency dedup window — platform-level call, not skill-suite scope.
- **§1.3** Long-running / mid-stream billing pattern — affects codemod templates for streaming endpoints.
- **§2.4** Drift-lint trust asymmetry — customer-facing presentation.
- **§3.4** `@colbymchenry/codegraph` capability — engineering spike, may upgrade Skill A's call-graph fidelity.
- **§6.2 #1** Multi-account / multi-org setup — Skill B v1.5 scope.
- **§6.2 #2** IAM permission model details — needs cloud-provider write-up per cloud.
- **§6.2 #5** Tag-schema alignment with customer's existing FinOps schema — defer until first conflict.
- **§6.2 #6** Cross-cloud tag-schema consistency — defer until first multi-cloud customer.
- **§6.2 #9** Cost-allocation rule selection — needs PM-facing UX per service.
- **§6.3 #11** Skill C local-only run aggregate-metrics list — codify in `local-only-metrics.md`.
- **§6.3 #12** Skill C corpus retention — legal.
- **§6.3 #13** Skill C CI runtime cost — engineering.
- **§6.3 #14** WAPE/Coverage thresholds per service/pattern — post-GA.
- **§6.3 #15** Algorithm versioning + historical re-attribution — post-GA.
- **§6.3 #17** Phase 3 customer-onboarding-gate UX — v2.
- **§6.4 #23** Idempotency-key derivation policy — platform-level.

These seven items are the natural HLD agenda. The rest are either resolved, deferred, or out of scope.

---

## Dogfood-surfaced gaps (post-v1, no requirements-doc §)

| Gap | Status | Path |
|-----|--------|------|
| **Worker / consumer / scheduler scan coverage** | **OPEN — design ready.** The discovery + instrument pipeline is HTTP-request-shaped at all 5 layers (detect / cost-match / refund-test / attribute / emit); non-HTTP emission sites (queue workers, stream consumers like moo-meter's Kafka sink, cron, CLI batch) are invisible. Surfaced 2026-05-28 by a moo-meter dogfood run. | See `worker-coverage-design.md` (cost-call-anchored discovery + execution-context classification; phased task list W0-W7). |
