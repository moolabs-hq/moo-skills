---
name: cost-billing-signoff
description: >-
  State-aware orchestrator for the three-role review workflow that runs AFTER /cost-billing-discovery produces inventories + HTML views. Reads .moolabs/inventory/reviews/ to figure out which signoff is next, dispatches to the right persona flow (CFO Stage 1, PM Stage 2 per-product, CFO Stage 2b per-product, Engineer Stage 3 per-service, PM Stage 3b per-service, holistic Skill R). For each stage: opens the right HTML projection, asks the persona ONE question at a time, invokes Skill R adversarially, persona accepts/risk-accepts/rejects R findings, writes the signed YAML. Handles multi-product + multi-service fan-out. Refuses to run if inventories absent; refuses to advance past blocked R verdicts. Triggers on "signoff", "review the inventory", "approve inventories", "three-role review", "stage signoff", "PM review", "CFO review", "engineer review".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-20
  last_reviewed: 2026-05-20
  review_interval_days: 60
  consumes:
    - .moolabs/inventory/cost-events-inventory.yaml
    - .moolabs/inventory/usage-events-inventory.yaml
    - .moolabs/inventory/output-input-map.yaml
    - .moolabs/inventory/reviews/{cfo,pm,engineer}-view.html
  produces:
    - .moolabs/inventory/reviews/{cfo-stage1,pm-stage2-<product>,cfo-stage2b-<product>,engineer-stage3-<service>,pm-stage3b-<service>}-signoff.yaml
    - .moolabs/inventory/reviews/holistic-r-review.md (via /cost-billing-adversarial-review)
---

# /cost-billing-signoff — Three-role review orchestrator (state-aware)

You are the executor for the three-role review workflow. `/cost-billing-discovery` already produced the inventories + HTML views; your job is to walk the CFO / PM(s) / engineer(s) through reviewing them and writing the signoff YAMLs that the codemod's gate depends on.

You are **state-aware**: you read what's already signed off in `.moolabs/inventory/reviews/` and dispatch to the next action. The persona running you is detected from the install context (each persona installs this skill on their machine) OR overridden via `--persona`.

## Trigger

```
/cost-billing-signoff                              # auto-detect persona + next stage
/cost-billing-signoff --persona cfo                # explicit persona
/cost-billing-signoff --persona team-product --product acute
/cost-billing-signoff --persona team-engineer --service moo-acute
/cost-billing-signoff --status                     # just print current state machine — no actions
/cost-billing-signoff --reset cfo-stage1           # invalidate a signoff (forces re-review)
```

Natural triggers:
```
Sign off on the inventory
Review the CFO view
Stage 1 signoff
PM review of inventory for product acute
Engineer review for service moo-acute
What stage am I in?
```

## Read first (shared/)

- `cost-billing-shared/operating-principles.md` — NEVER assume; ASK when in doubt; ONE question at a time.
- `cost-billing-shared/three-role-review.md` — the workflow this skill executes.
- `cost-billing-shared/chain-handoff.md` — for multi-product / multi-service understanding.

## Operating principles (apply to EVERY signoff action)

1. **NEVER assume a persona signed off** — read the YAML, verify `status: approved`, verify `signed_at` is recent and the signer is the persona claimed.
2. **NEVER skip Skill R** — every signoff stage runs `/cost-billing-adversarial-review --phase post-signoff-<stage>` BEFORE the human signs off. R's findings get listed; human accepts/rejects.
3. **ONE question at a time** — same rule as bootstrap. State persists in draft signoff YAML; `--resume` continues at the next unanswered question.
4. **Refuse-to-advance on blocked R verdicts** — if R's verdict is `blocked`, the signoff cannot be written; the persona must either fix the artifact or escalate.

## Refuse-to-run preconditions

Refuse with a precise message if:
- `.moolabs/inventory/cost-events-inventory.yaml` / `usage-events-inventory.yaml` / `output-input-map.yaml` are missing → "Run `/cost-billing-discovery <repo>` first."
- `.moolabs/inventory/reviews/{cfo,pm,engineer}-view.html` are missing → "Re-run `/cost-billing-discovery` — Phase 5 outputs incomplete."
- The persona's chain-stage signed YAML is missing (CFO needs `01-finance.signed.yaml`, PM needs `02-cpo.signed.yaml`, etc.) → "Bootstrap chain not complete; run `/cost-billing-bootstrap-<stage>` first."
- For multi-product: `02-cpo.signed.yaml` has no `products: []` block → "CPO must declare products before per-product PM signoffs are possible."

## The state machine

Read all files matching `.moolabs/inventory/reviews/*-signoff*.yaml` and `.moolabs/inventory/reviews/holistic-r-review.md`. Determine next action:

```
┌────────────────────────────────────────────────────────────────┐
│ State node                          │ Next action               │
├─────────────────────────────────────┼───────────────────────────┤
│ no signoffs yet                     │ → cfo-stage1              │
│ cfo-stage1 approved                 │ → pm-stage2 (per product) │
│ all pm-stage2-<P> approved          │ → cfo-stage2b (per P)     │
│ all cfo-stage2b-<P> approved        │ → engineer-stage3 (per S) │
│ all engineer-stage3-<S> approved    │ → pm-stage3b (per S)      │
│ all pm-stage3b-<S> approved         │ → holistic-r-review       │
│ holistic-r-review verdict=clean(*)  │ → DONE — codemod unblocked│
│ ANY signoff status=re-open-*        │ → loop back to the stage  │
│                                     │   that owns the re-open   │
└─────────────────────────────────────┴───────────────────────────┘

(*) clean OR clean-with-accepted-risks. blocked verdict halts the chain.
```

`--persona` filter narrows which actions you'll execute (a CFO machine won't try to do PM signoffs).

## Per-stage workflow (same shape, different inputs)

### Phase 1 — Show the persona their view + the stage spec

For CFO Stage 1:
- Open `.moolabs/inventory/reviews/cfo-view.html` in the default browser (`open` on macOS, `xdg-open` on Linux), OR print a path + brief summary if no display.
- Print the stage's scope: "You're reviewing 15 usage events, 22 cost events, projected revenue $42k/month."

For PM Stage 2 (per product):
- Open `pm-view.html` filtered to `--product <slug>`.
- Print scope per product.

For Engineer Stage 3 (per service):
- Open `engineer-view.html` filtered to `--service <slug>`.
- Print: "12 file:line entries to verify, 3 framework adapters to confirm, 8 idempotency anchors."

### Phase 2 — Ask the persona ONE question at a time

CFO stage:
- `[Stage 1, Q1 of N] Review the projected monthly revenue per output. Any entries you'd change?`
- `[Q2] Any fair-usage thresholds that need adjustment?`
- `[Q3] Any entries you'd reclassify as internal/non-billable?`

PM stage (per product):
- `[Stage 2 / product=acute, Q1] Confirm the billable units for each acute feature.`
- `[Q2] For each output, are the input mappings correct (or missing anything)?`
- `[Q3] Any features that should be merged/split?`

Engineer stage (per service):
- `[Stage 3 / service=moo-acute, Q1] Verify file:line for each cost-event entry.`
- `[Q2] Confirm framework adapter selection per service.`
- `[Q3] Idempotency-anchor decisions per entry.`

Per the operating-principles HARD RULE: ONE question at a time, save draft after each, `--resume` continues.

### Phase 3 — Persona proposes signoff draft

Build `.moolabs/inventory/reviews/<stage>-signoff.draft.yaml`:

```yaml
$schema: https://moolabs.com/schemas/cost-billing-signoff/0.1.0
stage: cfo-stage1 | pm-stage2 | cfo-stage2b | engineer-stage3 | pm-stage3b
product: <slug>                   # only for pm-stage2 / cfo-stage2b
service: <slug>                   # only for engineer-stage3 / pm-stage3b
proposed_status: approved | re-open-<who> | escalate
edits_to_inventory:               # per-entry overrides the persona is requesting
  - entry_workflow_id: ...
    change: ...
    rationale: ...
findings_from_persona: []         # human-flagged concerns
notes: |
  (free-form rationale)
```

### Phase 4 — Adversarial review (Skill R)

Invoke:
```
/cost-billing-adversarial-review --phase post-signoff-<stage> --target .moolabs/inventory/reviews/<stage>-signoff.draft.yaml
```

R's stage-specific risks:
- **cfo-stage1**: projected-revenue arithmetic, missing pricing for an entry, internal-marked entries that look billable, fair-usage threshold inconsistencies.
- **pm-stage2-<product>**: orphan outputs, double-counted inputs, refund-unit drift vs CFO's finance commitments, missing inputs vs code-graph evidence — SCOPED TO PRODUCT.
- **cfo-stage2b-<product>**: CFO's re-confirmation accepts changes the PM made; check for inconsistencies with cfo-stage1's commitments.
- **engineer-stage3-<service>**: wrong file:line, wrong framework adapter, idempotency-anchor unworkable, missed false-positives — SCOPED TO SERVICE.
- **pm-stage3b-<service>**: engineer's code reality breaks PM's earlier unit/mapping decision; flag, route to bubble-up logic.

R writes its findings to `<stage>-signoff.r-findings.yaml`.

### Phase 5 — Human reads R findings + accepts/rejects + signs off

For each R finding, the persona answers ONE question:
- "R flagged: <finding>. Accept (fix it) / Risk-accept (acknowledge but don't fix; add rationale) / Reject (R was wrong here, explain why)."

After all findings handled, the persona's `signoff.status` is computed:
- If ANY R finding is `accepted` (fix required), the signoff status becomes `re-open-<stage-that-owns-the-fix>` and the chain loops back.
- If ALL R findings are `risk-accepted` or `rejected`, signoff status becomes `approved`.

### Phase 6 — Write the signed YAML + invariants check + advance state machine

Promote `<stage>-signoff.draft.yaml` → `<stage>-signoff.yaml` with the final `status` block, signer identity, R verdict, and rationale notes.

**Invariants verified BEFORE writing (F2 fix):**
- For PM/engineer stages: filename suffix slug == YAML body `product_slug` / `service_slug` (catches typo drift like file `*-moo-acute.yaml` vs body `service_slug: moo_acute`).
- For pm-stage3b on multi-owner services: `co_signed_by[]` contains one entry per owning product's PM, each with `on_behalf_of_product` matching a `products[]` slug whose `services[]` contains this service_slug (F3 invariant).
- For PM stages: `signed_by.contact` matches `02-cpo.signed.yaml > products[product_slug].team_pm_contact` IFF that field is set (F1 invariant; warn if unset).
- `signed_at` is after `generated_at` (catches backdating).
- `$schema` URL matches the v0.3 signoff schema (`https://moolabs.com/schemas/cost-billing-signoff/0.1.0`).

Any invariant failure aborts the write with a precise message + leaves the draft intact for retry. Print:

```
✓ <Stage> signoff complete.
Signed:  .moolabs/inventory/reviews/<stage>-signoff.yaml
R verdict: clean | clean-with-accepted-risks | blocked
Status: approved | re-open-<stage>

NEXT in the state machine:
  <next action OR "all signoffs complete — codemod is unblocked">
```

## Multi-product / multi-service handling

For PM and Engineer stages, the skill iterates over CPO-declared products (or engineer-declared services). The state machine considers a stage "complete" only when ALL per-product (or per-service) signoffs are approved.

Example state for a customer with 3 products spanning 5 services:
```
cfo-stage1-signoff.yaml                      ← approved
pm-stage2-signoff-acute.yaml                 ← approved
pm-stage2-signoff-meter.yaml                 ← approved
pm-stage2-signoff-arc.yaml                   ← MISSING — next action for PM responsible for arc
cfo-stage2b-signoff-acute.yaml               ← (waits for all pm-stage2 to complete)
...
```

The CPO's products block (in `02-cpo.signed.yaml`) lists `team_pm_contact` per product so you know which PM machine owns which signoff. `--persona team-product --product arc` lets the right PM step in.

## Once all signoffs + holistic-R clean: codemod is unblocked

Print:
```
═════════════════════════════════════════════════════════════
 ALL SIGNOFFS COMPLETE — CODEMOD UNBLOCKED
═════════════════════════════════════════════════════════════
The /cost-billing-instrument gate is now satisfied. Engineers can run
per-service:

  /cost-billing-instrument --service moo-acute
  /cost-billing-instrument --service moo-meter
  ...

After the codemod emits each PR, iterate on it with your existing
PR-revision skill (e.g., /dev-workflow-orchestrator) — that's the
cost-billing suite's documented handoff for iterative code revision.
```

## What this skill MUST NOT do

- **Never** auto-fill signoff fields from "context" — every signoff is explicit human acknowledgement.
- **Never** advance the state machine past a `blocked` R verdict.
- **Never** delete a signoff file (use `--reset` for explicit invalidation; logs the reset).
- **Never** edit the inventory directly — only WRITE signoff files. Inventory edits come from re-running `/cost-billing-discovery` with new context, OR from re-running the bootstrap chain stage that owns the contradiction.
- **Never** write the holistic-r-review.md — that's `/cost-billing-adversarial-review --phase holistic-pre-codemod`'s job. You just call that skill and wait.

## Reference files

- `references/state-machine.md` — full state diagram + transitions + invalidation rules.
- `references/per-stage-questions.md` — the canonical question list per stage.
- `references/signoff-yaml-schema.md` — the signed-YAML shape per stage.
- `../cost-billing-shared/three-role-review.md` — the workflow.
- `../cost-billing-shared/chain-handoff.md` — multi-product/multi-service handoff.

## Assets

- `assets/state-machine.yaml` — declarative state-machine config.
- `assets/signoff.schema.yaml` — JSON-Schema for the signed YAMLs.
