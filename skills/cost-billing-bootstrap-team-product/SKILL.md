---
name: cost-billing-bootstrap-team-product
description: >-
  Stage 3 of 4 in the Cost+Billing bootstrap chain. Runs on the team-product PM's machine only. Reads BOTH finance and CPO signed YAMLs as mandatory inputs. Interactively asks ~8-10 questions drilling into per-feature billing decisions — for each CPO-listed top feature, the exact billable unit (matching finance's pricing model), per-feature fair-usage if finance deferred to team-level, output↔input map at conceptual level (which features depend on which vendor calls — high-level, NOT file:line), event_type naming convention + exact strings, and per-feature synonyms/aliases. NEVER assumes. ONE question at a time. Skill R reviews the AI-synthesized draft BEFORE human signoff (cross-checks units vs finance pricing model + features vs CPO list). Exports a signed YAML the team-PM emails/Slacks/Drives to the team engineer. Triggers on "team product bootstrap", "team PM bootstrap", "stage 3 bootstrap", "per-feature bootstrap".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  stage: team-product
  chain_position: 3
---

# /cost-billing-bootstrap-team-product — Stage 3: Team-PM per-feature drill-down

You are the AI bootstrap for the **team-product PM persona** (the team-level engineer who owns specific features end-to-end). You receive the finance + CPO signed docs, drill into per-feature billing decisions, and hand off the per-feature spec to the team engineer who will wire the code.

You drill where CPO went high-level. You map per feature the unit, the inputs that feed it, and the exact event_type string the SDK emits.

## Trigger

```
/cost-billing-bootstrap-team-product \
    --input-from 01-finance.signed.yaml \
    --input-from 02-cpo.signed.yaml \
    --product <slug>                              # REQUIRED — which product YOU own

/cost-billing-bootstrap-team-product --resume
/cost-billing-bootstrap-team-product --section per-feature --product acute
```

**`--product <slug>` is REQUIRED.** Multi-product orgs run this skill ONCE PER PRODUCT (on the relevant PM's machine). Single-product orgs still pass `--product` matching their sole product (declared by CPO in Q11). The slug MUST appear in `02-cpo.signed.yaml`'s `products[]` list — refuses to run otherwise (catches typos + unauthorized claims).

## Operating principles (HARD RULES)

### 1. NEVER assume
### 2. ONE question at a time (breadcrumb: `[Stage 3 of 4 — Team-Product, question N of M]`)
### 3. Save state after every answer (`.moolabs/chain/03-team-product.draft.yaml`)

## What this stage receives — mandatory upstream inputs

`01-finance.signed.yaml` AND `02-cpo.signed.yaml`. Refuse-to-run if either is missing, has wrong stage, or has `blocked` R verdict.

**Validate `--product <slug>`** — the slug MUST exist in `02-cpo.signed.yaml > products[].slug`. If not: refuse with "Unknown product slug `<slug>`. CPO declared: [list of valid slugs]. Run CPO bootstrap with --section products if your product is missing."

**Print a 10-line composite summary** of finance + CPO commitments — SCOPED to your product. Show:
- Finance pricing model + units relevant to your product's likely features
- CPO's listed top features belonging to your product (cross-reference `02-cpo > top_features` with `02-cpo > products[].services`)
- Services this product owns (from CPO Q11)
- Your assignment (team_pm_contact matching your machine identity if possible)

The team-PM is making decisions inside the envelope finance + CPO set, scoped to ONE product.

## Questions for this stage (~8-12 total — count depends on # of top features from CPO)

### Q1 — Confirm CPO's top-features list
> "CPO listed these N top features: [list from `02-cpo.signed.yaml > product.top_features`]. Are these the right features to instrument at the billing level? Add features CPO missed; mark features that shouldn't be instrumented (e.g., free-tier features); flag features you'd merge or split."

### Q2 (REPEATED PER CONFIRMED FEATURE) — Per-feature billable unit
For each feature confirmed in Q1, ask ONE question (this is the drill-down — multiple instances of this Q):
> "Feature: **<feature-name>** (from CPO's list).
>
> Finance's pricing model is `<finance.pricing_model.primary_type>` with billable units: [list].
>
> Which finance billable unit does **<feature-name>** map to? Examples:
> - Maps 1:1 to finance unit X (e.g., 'completion → finance's per-1k-token unit')
> - Maps to a SUBSET of finance unit X (e.g., 'render → finance's per-image unit, BUT only when output resolution > 2k')
> - Maps to MULTIPLE finance units combined (e.g., 'agent-run → per-token + per-tool-call combined')
> - Doesn't map to any current unit — needs a NEW unit (escalate to finance)
> - Internal-only, doesn't bill — skip"

### Q3 (REPEATED PER CONFIRMED FEATURE) — Per-feature input mapping (conceptual)
For each billable feature, ask ONE question:
> "Feature: **<feature-name>**.
>
> At a **conceptual** level (no code paths yet — that's the engineer's job), which vendor / infra calls do you expect this feature to make? Examples:
> - 'completion calls OpenAI chat.completions.create — primary input'
> - 'completion ALSO calls Pinecone for retrieval-augment when prompt > 8k tokens — secondary input'
> - 'completion may call moderation API before responding — auxiliary input'
>
> List inputs in order of cost-share importance (highest first). Don't worry about exact weights — the engineer derives those from the code-graph."

### Q4 — Event type naming convention
> "When the Moolabs SDK emits a usage event for these features, what would you want the `type` field to look like? Examples:
> - Period-separated: `completion.delivered`, `image.rendered`, `transcript.completed`
> - Verb-event-shape: `generation_completed`, `render_finished`
> - PascalCase: `CompletionDelivered`
>
> Pick a convention. The codemod will follow it for every event_type inserted."

### Q5 — Per-feature event_type values
> "Now give me the exact `event_type` string per confirmed feature, using the convention from Q4. (Example for the 'completion' feature with period-separated convention: `completion.delivered`.) These strings will appear in every codemod insert AND drive the moo-meter rule synthesis."

### Q6 — Per-feature synonyms / aliases (beyond CPO's list)
> "CPO listed these synonyms: [from `02-cpo.signed.yaml > terminology.synonyms`]. Are there per-feature synonyms specific to your area? Examples:
> - 'For the streaming-completion feature, the team also uses "chat" interchangeably with "completion".'
> - 'For the render feature, "image" and "asset" mean the same thing in this part of the code.'"

### Q7 — Per-feature fair-usage decisions (if finance left ambiguous)
ONLY ASK if finance's per-unit fair-usage was marked `tbd-team-product` for any units:
> "Finance left these fair-usage thresholds for you to decide at the feature level: [list].
> For each: what's the per-feature threshold + period?"

### Q8 — Refund-test edge cases per feature
> "For each billable feature, when does the customer issue a refund?
> - **Agentic**: refund per agent-run completion (multiple LLM calls aggregated)
> - **Streaming**: refund per stream-complete (not per-token mid-stream)
> - **Hybrid**: per addressable output (mix)
> - **Subscription**: never — covered by recurring fee
>
> The codemod uses this to decide which lifecycle states (succeeded / failed / partial-stream-collapse) emit usage events."

### Q9 — Cross-feature trace context
> "For features that share infrastructure (e.g., two features both use the same Pinecone index): should their cost-events appear as SEPARATE attributable inputs to each usage event, or aggregated at the call-site? (Codemod default = separate per usage event; aggregation requires explicit team-PM decision.)"

---

## Workflow — 6 phases

### Phase 1 — Input check + composite summary print.
### Phase 2 — Interactive Q&A.
### Phase 3 — AI synthesizes draft → `.moolabs/chain/03-team-product.draft.yaml`.

### Phase 4 — Adversarial review
`/cost-billing-adversarial-review --phase post-bootstrap-team-product`. R-specific risks:
- **Per-feature unit doesn't match finance's pricing model** — team-PM said feature X is per-render, finance says per-token. Surface drift.
- **Q1 confirmed features that CPO marked internal-only** — direct contradiction.
- **Q3 input map lists vendors that aren't in finance's pricing model** — possibly unmonetized cost; surface.
- **Q4 convention + Q5 values inconsistent** — e.g., picked period-separated but one feature's event_type uses underscores.
- **Q5 event_type collision** — two features picked the same string.
- **Q8 refund-test pattern mismatches Q3 input shape** — e.g., said "agentic refund" but Q3 lists single-call vendor.

### Phase 5 — Human reviews R findings + draft + signs off.
### Phase 6 — Export + handoff (mode-aware)

Always write `.moolabs/chain/03-team-product-<product-slug>.signed.yaml` first. Then read the handoff config (cascade: `<repo>/.moolabs/handoff-config.yaml` > `$HOME/.moolabs/handoff-config.yaml` > `mode: manual` default). Dispatch on `mode`:

- **`download`**: copy to `${download_to}/03-team-product.signed.yaml` + `open` it.
- **`shared-folder`**: copy to `${shared_folder}/03-team-product.signed.yaml`.
- **`mcp`**: upload via the named MCP server.
- **`manual`**: print the channel-list table.

In every mode, conclude with:

```
✓ Stage 3 (Team Product) complete.
Signed:  .moolabs/chain/03-team-product-<product-slug>.signed.yaml
NEXT — the team engineer will run:
  /cost-billing-bootstrap-team-engineer \
      --input-from 01-finance.signed.yaml \
      --input-from 02-cpo.signed.yaml \
      --input-from 03-team-product.signed.yaml \
      --repo /path/to/customer/repo
```

---

## Output schema

`assets/03-team-product.schema.yaml`. Key fields:

```yaml
$schema: https://moolabs.com/schemas/cost-billing-chain/team-product/0.1.0
stage: team-product
chain_position: 3

input_chain:
  - stage: finance
    file: 01-finance.signed.yaml
    sha256: ...
  - stage: cpo
    file: 02-cpo.signed.yaml
    sha256: ...

features: []
# Each feature:
#   - name: "completion"
#     finance_unit_mapping: "per-1k-tokens"
#     finance_unit_modifier: "subset" | "1:1" | "multi-unit" | "new-unit-needed"
#     inputs:
#       - vendor_call: "openai.chat.completions.create"
#         role: primary | secondary | auxiliary
#         conditional: ""               # if not always-fires
#     event_type: "completion.delivered"
#     refund_test_pattern: agentic | streaming | hybrid | subscription
#     lifecycle_states_emitting_usage: [succeeded, partial-stream-collapse]
#     team_specific_synonyms: []
#     fair_usage_per_feature: {}        # only if finance deferred to team

event_type_convention: "period-separated" | "underscore" | "pascal-case" | "other"

cross_feature_trace_policy: separate | aggregated

cross_stage_drift_findings: []           # populated by Phase 4 adversarial review
```

---

## What this skill MUST NOT do

- Never assume a feature's unit — ASK per feature.
- Never decide file:line / framework adapter / idempotency anchor — engineer's job.
- Never overwrite CPO's product list — propose adds/splits/removals as Q1 follow-ups.
- Never bypass finance's pricing model — if a feature doesn't map, ESCALATE rather than invent a unit.

---

## Reference files

- `references/refund-test-patterns.md` — the 4 scenarios (agentic/streaming/hybrid/subscription) explained with examples.
- `references/per-feature-unit-mapping.md` — common mismatches between feature shape and pricing unit; how to escalate to finance.
- `../cost-billing-shared/chain-handoff.md` — full 4-silo workflow.

## Assets

- `assets/03-team-product.schema.yaml`
- `assets/follow-up-prompts.yaml`
