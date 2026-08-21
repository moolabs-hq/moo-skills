# Matrix/hybrid pricing — detecting a variable per-occurrence quantity

> **Naming collision warning:** `three-role-review.md` / `anchor-taxonomy.md` already use
> "hybrid" for a **refund-test timing scenario** ("some real-time, some async — billing
> event fires per addressable output"). This doc's "matrix/hybrid" is a **different axis**
> — not WHEN a unit charges, but **HOW MUCH** it charges per occurrence. A single event
> can be refund-test-hybrid AND pricing-shape-flat, or refund-test-agentic AND
> pricing-shape-variable — the two are orthogonal. Don't conflate them when reading an
> inventory entry.

## What this is

Moolabs' own rate-schedule UI (`PricingDrawer` / matrix pricing, see
`moolabs-hq/moolabs`'s `matrix-pricing.spec.ts`) lets a Moolabs customer's *own*
customers be charged a different price per unit depending on a dimension combination
(e.g. `region` × `model` × `tier`). That's a **pricing-time** concern — it happens on
the Moolabs backend, matching an ingested event's dimension fields (`model`, etc.)
against a rate-schedule row.

This doc is about a **narrower, upstream** problem this suite must not walk past:
before Moolabs can price anything, discovery must correctly capture the **quantity**
(`value`) an ingest event carries. A customer's own codebase frequently ALREADY
computes a variable, per-occurrence quantity — not a flat `1` per event — via a
brownfield metering/credit system that predates any Moolabs integration. If discovery
doesn't detect that and bind `derivation` to it, the codemod ships a `value=1`
(or another flat placeholder) that silently mis-reports every occurrence whose real
cost differs from the placeholder. Moolabs' matrix pricing can't help — a correctly
matched rate row multiplied against a wrong quantity is still a wrong bill.

**This is not hypothetical.** The nRev / `workflow-studio` dogfood run (2026-08-21)
shipped exactly this bug: `BaseNode.execute()`'s usage-event emission hardcoded
`value=1`, while the SAME file's `BaseNode.consume_credits()` — called by 30+ node
subclasses via `get_credit_cost_per_item()` overrides — already proves the real
per-node-run credit cost varies (per-page, per-result, per-item, vendor-priced). The
codemod and its own post-codemod adversarial review both missed it; a human question
about Moolabs' matrix-pricing UI is what surfaced it. This doc + the Phase 4 step it
backs (`discovery/SKILL.md` → "Brownfield variable-quantity detection") exist so the
NEXT customer's variable-cost system is caught at discovery time, not after merge.

## Detection signals (brownfield variable-quantity / matrix-hybrid candidates)

Run these against the target function **and its direct callees** for every
terminal-event candidate, before defaulting `refund_unit` to a flat unit:

1. **Existing metering/credit vocabulary** — function or method names matching
   (case-insensitive): `consume_credit`, `credit_cost`, `cost_per_item`,
   `usage_units`, `metered_amount`, `billable_quantity`, `billing_amount`,
   `record_usage`, `ingest_usage`, `usage_record`. Also vendor-native calls:
   Stripe `usage_records.create(...)` / `meter_events.create(...)`.
2. **Per-subclass / per-config override pattern** — a base class defines a stub
   (commonly returning a constant like `0` or `1`) that MULTIPLE subclasses/configs
   override with distinct, non-constant logic (per-page, per-result, per-item,
   vendor-config-driven). Grep for the base method name across the repo; ≥3 distinct
   non-trivial overrides is a strong signal the base's flat default does NOT reflect
   real-world quantity.
3. **Accumulator / loop-summed counter** — a variable incremented across a loop or
   across multiple calls within the same logical unit-of-work, then read once at the
   terminal event (the shape this suite recommends the codemod itself add — see
   "Recommended fix shape" below).
4. **Existing dimension-keyed cost table** — a lookup structure (dict/config) keyed by
   more than one dimension (e.g. `{provider: {model: price}}`) feeding a per-call cost
   calculation. This is the strongest signal of an eventual Moolabs-side matrix-pricing
   fit (the ingested event should carry the matching dimension, typically `model`, so
   Moolabs' rate engine can price it correctly) — record the dimension name(s) found so
   PM/CFO review can decide whether to also bind the SDK helper's `model=` field.

Any hit → propose one or more `refund_unit.derivation_candidates` entries (never
auto-promote — same discipline as `entity_id_candidate`), set
`refund_unit.pricing_shape: variable`, and record the evidence `file:line`.

No hit → `refund_unit.pricing_shape: flat` (the default; `derivation: 1` or a
genuine constant is correct and expected — most units ARE flat-priced. Don't invent
variability where none exists).

## Recommended fix shape (for the codemod / instrument-stage guidance)

When `pricing_shape: variable` and the confirmed `derivation` references a value that
isn't already available as a simple in-scope variable at the emission site (e.g. it's
the SUM of several calls made earlier in the same unit-of-work, as with
`consume_credits()` being called once per vendor call inside a loop), the codemod
should:

1. Add an instance-level accumulator (e.g. `self._moolabs_<meter>_quantity = 0` in
   `__init__`), initialized once per unit-of-work.
2. Increment it at every call site that contributes to the real quantity — INSIDE the
   existing guard that gates whether that call site's contribution is real (test-mode
   exclusions, zero-cost skips), so the accumulator never diverges from what was
   actually billed by the pre-existing system.
3. Emit the SDK's `value=` from the accumulator at the terminal event, not from the
   raw per-call argument (which is only one contribution, not the total).

This is exactly the shape used to fix the `workflow-studio` `node_run.completed` bug:
`consume_credits()` now accumulates into `self._moolabs_credits_consumed`; the emit
reads that total. See `domains/node_engine/core/nodes/base_node.py` in
`nurturev/workflow_studio` (PR #1239) for the reference implementation.

## Review-blocking condition

`pricing_shape: variable` with a `derivation` that `task_planner.py`'s
`_coerce_derivation` flags `derivation_needs_review: True` (i.e. it fell back to a
placeholder because no valid runtime expression was ever confirmed) is a
REVIEW-BLOCKING finding, not a warning to skim past. See
`instrument/scripts/task_planner.py`'s `pricing_shape`-aware warning and
`adversarial-review/SKILL.md`'s post-discovery / post-codemod risk tables.
