# Phase Decompose Checklist

Run this checklist during step 2a (decompose) **before finalizing the works list**. Each item must be answered with a concrete mechanism or "N/A — not applicable to this phase." Vague answers ("we'll make it idempotent") are not accepted.

Lessons derived from the Dimensioned Usage Rating Milestone A post-mortem, where all six gaps below surfaced as CRITICAL or IMPORTANT findings during adversarial review.

---

## 1. Idempotency mechanisms

For every work item that mentions "idempotent", "deduplication", "replay-safe", or "at-most-once":

- [ ] Name the mechanism: content-addressed PK (uuid5), ON CONFLICT DO NOTHING, CAS, upsert by natural key — pick one explicitly
- [ ] Name the layer: SQL dialect (e.g. `pg_insert().on_conflict_do_nothing()`), ORM-level (e.g. `db.merge()`), or application-level (check-then-insert)
- [ ] Identify the natural key: what fields define uniqueness? Write it down.

**Anti-pattern**: `db.merge(entity_with_new_uuid)` — ORM merge resolves by PK only; a fresh uuid4() always inserts, never updates. This caused AMBIGUOUS_PRICE on every re-sync in Milestone A.

---

## 2. UI-BFF endpoint contract

For any phase that includes both frontend and backend work:

- [ ] List every HTTP endpoint the UI calls in this phase (method + path)
- [ ] Map each button, form submit, or data fetch in the UI spec to a named endpoint in a backend work item
- [ ] Confirm every listed endpoint has an owner work item in THIS phase (not assumed to exist from a prior phase)

**Anti-pattern**: `ApprovalQueue.tsx` called `POST /v1/rate-schedules/{id}/publish` — the endpoint wasn't in any phase's work list and was missing at review time (CRITICAL-2).

---

## 3. Fail-closed state reachability

For any phase introducing a state machine, discriminated union resolver, or explicit fail-closed error states:

- [ ] List every named state/error the resolver can return
- [ ] For each state, write one sentence: "This state is reached when [condition] and [code path]"
- [ ] Verify at least one test case exercises each state's code path

**Anti-pattern**: `DIMENSION_ATTESTATION_MISSING` was a documented fail-closed state but `_predicate_matches` returned `False` instead — the state was unreachable. No test caught it because no test asserted this specific return value.

---

## 4. Cross-layer type consistency

For any phase that introduces a string enum, discriminated union, or coded value that crosses stack layers (Go ↔ Python ↔ TypeScript ↔ DB):

- [ ] Specify the canonical casing and valid values in one place (usually the DB column or the OpenAPI schema)
- [ ] List every layer that references this type and confirm each uses the same casing
- [ ] Add a test or assertion that validates the TS/client type against the DB/server canonical form

**Anti-pattern**: `UnifiedPriceScope` in TypeScript used `'GLOBAL' | 'PLAN' | 'SUBSCRIPTION'` (uppercase); the DB stores `'tenant' | 'plan' | 'subscription'` (lowercase + different name). No test caught the mismatch because tests used Python directly, bypassing the TS layer.

---

## 5. Data field query semantics

For any column added to a data model or schema:

- [ ] For each new column, answer: "How is this used in queries?" — choose one: filtered (WHERE), sorted (ORDER BY), projected (SELECT only), aggregated (GROUP BY / COUNT)
- [ ] If the column is intended as a filter dimension, write the WHERE clause that uses it
- [ ] If the column is projection-only, mark it explicitly — missing filters are a silent correctness gap

**Anti-pattern**: `currency` was added as a column on `unified_price_rows` and as a parameter to `resolve_unified_price()`, but the resolver SQL never filters by it. The column exists; the semantics don't. Deferred to Milestone B as accepted residue.

---

## 6. Test scope per work item

For every work item, before marking it in the works list:

- [ ] List the test cases this work item requires (one line each: "test X asserts Y")
- [ ] Ensure at least one test covers each new endpoint, each new state/error path, and each new branch in business logic
- [ ] For idempotency work items: include a test that calls the operation twice and asserts the result is the same as calling it once

**Anti-pattern**: The publish endpoint tests and ON CONFLICT replay test were only added in Round 2 of adversarial review. They were not in any phase's work list, so they weren't written during development.

---

## Checklist gate

Before setting a phase to `decomposed`, answer these questions in one line each in the phase's work notes or PRD preamble:

| # | Question | Answer |
|---|----------|--------|
| 1 | What idempotency mechanisms are used, and at which layer? | |
| 2 | List of UI-called endpoints and their owner work items | |
| 3 | Reachability table for all fail-closed states | |
| 4 | String enums crossing layers — canonical form and layers verified | |
| 5 | New columns — filter/sort/project semantics | |
| 6 | Test cases per work item | |

Mark each item N/A with a one-line justification if it genuinely doesn't apply to the phase.
