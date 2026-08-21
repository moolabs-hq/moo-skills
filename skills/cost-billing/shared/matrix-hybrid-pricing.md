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
   a dimension (e.g. `{model: price}`) feeding a per-call cost calculation. This is a
   signal of an eventual Moolabs-side matrix-pricing fit, but see "Quantity vs. price —
   do not bind both" below before proposing anything from it: whether this signal
   means "propose a `derivation_candidate`" or "propose a dimension field, and leave
   `derivation` alone" depends entirely on what the table's OUTPUT represents.

Any hit → propose one or more `refund_unit.derivation_candidates` entries (never
auto-promote — same discipline as `entity_id_candidate`), set
`refund_unit.pricing_shape: variable`, and record the evidence `file:line`.

No hit → `refund_unit.pricing_shape: flat` (the default; `derivation: 1` or a
genuine constant is correct and expected — most units ARE flat-priced. Don't invent
variability where none exists).

## Quantity vs. price — do not bind both (added 2026-08-21, after a near-miss on PR #1239)

Signal 4 above (a dimension-keyed cost table) is dangerous to act on naively, because
"the customer's code already computes a variable number" is true of BOTH of two
completely different things, and they require OPPOSITE handling:

- **A quantity** — a count of billable units (tokens, pages, rows, seconds). Binding
  `derivation` to it is correct and is exactly what this doc otherwise recommends.
- **A price** — a lookup table's OUTPUT already expressed in the customer's own
  billing currency (credits, dollars, cents) for one dimension value (e.g.
  `PRICING_DICT[model] -> credits_per_item`, one row per model). This is the
  customer's OWN rate card, not a quantity. If you bind `derivation` to its output
  AND ALSO propose the table's key (`model`) as a dimension field on the SDK helper
  (`model=`), and Moolabs' rate schedule for that meter ALSO prices by that same
  dimension, the event gets priced TWICE: once by the customer's own table (baked
  into `derivation`'s value) and once more by Moolabs' matrix rate card applied on
  top of it. Silent overcharge, not a crash — nothing about it fails loudly.

**The discriminator:** does the lookup table's output get used as a countable
quantity elsewhere in the same code path (e.g. summed, compared against a usage cap
in raw units), or does it get used as a monetary/credit amount that is itself
DEDUCTED from a balance (e.g. passed straight into a `consume_credits`-shaped
call)? The latter is a price. When it's a price, do NOT default to binding
`derivation` to it — first ask which pricing authority this integration intends:

- **The customer's own rate table stays authoritative for this meter** (Moolabs is
  just recording the resulting spend/credits, not re-pricing it) → bind `derivation`
  to the price-table's output (it correctly IS the variable quantity in the
  customer's own currency), and do NOT also propose the table's dimension key as an
  SDK dimension field — see `pricing_shape: variable` above.
- **Moolabs' matrix rate schedule is meant to REPLACE the customer's own pricing for
  this meter** — the common case when the integration is explicitly a *migration
  off a prior provider that made the customer pre-compute pricing themselves* — set
  `pricing_shape: dimensional` instead: `derivation` stays a flat `1` (or the true
  neutral per-run count), and the table's dimension key becomes `dimension_source`,
  proposed via `dimension_candidates` and confirmed the same way as any other
  candidate field. See "dimensional pricing" below.

Don't guess between these — record the open question in `review_notes` and route it
to the human (customer/reviewer). It is a product decision (is the customer's rate
table being kept as the pricing authority, or replaced by Moolabs'?), not something
discovery or the codemod can infer from code alone. A "we're migrating off provider
X, who made us calculate everything ourselves" statement from the customer is a
strong, explicit signal toward `dimensional`.

### `dimensional` pricing (flat quantity, dimension-driven price)

`pricing_shape: dimensional` is the THIRD case (added 2026-08-21, after PR #1239's
resolution below), distinct from both `flat` (no variability at all) and `variable`
(the per-occurrence COUNT varies). Here the quantity is flat — usually `derivation:
1`, one event per occurrence — but the PRICE varies by a dimension Moolabs' own
matrix rate schedule prices (node type, model, region, tier, ...), not by anything
the customer's code computes. `dimension_source` carries the runtime expression for
that dimension value (the SDK helper's `model=` kwarg — a generic dimension slot,
not restricted to LLM model names).

**Not yet wired into the codemod templates** (the `.j2` templates only interpolate
`entry.refund_unit.derivation`, not `dimension_source` — grep-verified across all
seven `assets/codemod-templates/*.j2` files). Until that lands, `dimension_source`
is applied by the agent by hand at codemod time, the same "aspirational schema,
agent-applied" pattern several other Phase 4/5 fields already use per
`discovery/SKILL.md`'s own Scripts section. Don't claim template automation exists
for this field until the templates are actually updated to read it.

**What actually happened on PR #1239, and how it resolved:** `ask_ai_node.py` /
`summariser_node.py`'s `get_credit_cost_per_item()` returns
`PRICING_DICT[settings["model"]]` — a PRICE (credits per item, keyed by model), not
a quantity. The initial fix bound `derivation` to the accumulated total AND
separately wired `model=` as an SDK dimension (commit `1d2789c7`), reasoning
(wrongly) that `model` was "just descriptive metadata." The customer caught it —
double pricing — and that commit was reverted (`b3bf63ff`).

The actual answer, once asked: this integration is a **migration off a prior
provider that made the customer pre-compute pricing themselves** — the entire
point is to stop doing that. So the resolution (commit `65b94ad5`) is neither
"keep nRev's price" nor "bind derivation to it": it's `pricing_shape: dimensional`
(added to the schema below after this) — `value` is a flat `1` (finance's
`per_node_run` unit is satisfied: one event per run), and `model` carries the node
TYPE (`self.node_details.get("node_type_id")`, e.g. `"ask_ai"`, `"google_search"` —
confirmed generalizable to "model can be anything, not just LLM models"), letting
Moolabs' own rate schedule price per node type. `get_credit_cost_per_item()` /
`consume_credits()` (nRev's own Flexprice system, billing nRev's end-customers) are
untouched — only what's reported to Moolabs changed. Accepted, explicit tradeoff: a
run's Moolabs-reported price no longer varies by LLM model or item/page/row count
within the run, only by node type.

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
reads that total, with NO dimension field alongside it (see "Quantity vs. price —
do not bind both" above for why not). See
`domains/node_engine/core/nodes/base_node.py` in `nurturev/workflow_studio`
(PR #1239) for the reference implementation.

## Review-blocking condition

`pricing_shape: variable` with a `derivation` that `task_planner.py`'s
`_coerce_derivation` flags `derivation_needs_review: True` (i.e. it fell back to a
placeholder because no valid runtime expression was ever confirmed) is a
REVIEW-BLOCKING finding, not a warning to skim past. See
`instrument/scripts/task_planner.py`'s `pricing_shape`-aware warning and
`adversarial-review/SKILL.md`'s post-discovery / post-codemod risk tables.
