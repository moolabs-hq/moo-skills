# Per-stage canonical question list

The signoff orchestrator asks one question at a time per the operating principles. Here are the canonical questions per stage. The signoff skill READS this file at run-time to know what to ask.

## CFO Stage 1 (`cfo-stage1`)

The CFO is reviewing the unconfirmed inventory after `/cost-billing-discovery`. They have already seen `cfo-view.html`.

| # | Question |
|---|---|
| 1 | "Look at the projected monthly revenue per output. Anything you'd change?" |
| 2 | "Any fair-usage thresholds that need adjustment based on your latest pricing decisions?" |
| 3 | "Any entries currently marked as billable that you'd reclassify as internal/non-billable?" |
| 4 | "Any entries currently marked as internal that you'd reclassify as billable (revenue-relevant)?" |
| 5 | "Any pricing inconsistency between similar features? (E.g., one priced per-token, similar one priced per-call — usually drift.)" |
| 6 | "Any compliance or PII concerns about specific entries that should change handling?" |

## PM Stage 2 per-product (`pm-stage2-<product>`)

PM Alice (acute) reviewing pm-view-acute.html.

| # | Question |
|---|---|
| 1 | "Confirm the billable units for each acute feature. Any wrong?" |
| 2 | "For each output: are the input mappings correct? Anything missing?" |
| 3 | "Any features that should be merged (sub-features of one billable) or split (one feature billing as two distinct units)?" |
| 4 | "For shared-input cost events (e.g., the OpenAI call also used by another product): is the per-edge weight correct?" |
| 5 | "Any per-feature fair-usage decision finance deferred to you?" |
| 6 | "Any synonyms or aliases between docs and code SPECIFIC to acute that CPO didn't capture?" |

## CFO Stage 2b per-product (`cfo-stage2b-<product>`)

CFO re-confirming after PM's per-product spec.

| # | Question |
|---|---|
| 1 | "PM proposed [X] for the [feature] billable unit. Confirm or adjust?" |
| 2 | "PM's per-edge weights for this product imply [revenue distribution]. Does that match your pricing intent?" |
| 3 | "PM flagged [N] cfo_reopen_requests for this product. Address each one." (loop) |
| 4 | "Any pricing change you want to push back into Stage 1 (org-wide)? If yes, re-open Stage 1." |

## Engineer Stage 3 per-service (`engineer-stage3-<service>`)

Engineer Dan reviewing engineer-view-<your-service>.html.

| # | Question |
|---|---|
| 1 | "Verify file:line for each cost-event entry. Any stale or relocated?" |
| 2 | "Confirm framework adapter selection per service. Any wrong (e.g., picked fastapi but actually Litestar)?" |
| 3 | "For each entry, is the idempotency anchor (`{handler}.{path_param}`) actually derivable at the call site? Any unworkable?" |
| 4 | "Any entries that are false positives (test fixtures, retry loops, health checks the discovery filter missed)?" |
| 5 | "For shared call sites (this service AND another service both call this): does the inventory's `service` field correctly attribute it?" |
| 6 | "Any PM unit/mapping decisions that the code REALITY breaks? (E.g., 'PM said per-completion but this handler streams without a single completion event.') If yes, status=re-open-pm." |

## PM Stage 3b per-service (`pm-stage3b-<service>`)

Owning PM(s) re-confirming engineer's per-service spec. For multi-product services, ALL owning PMs see this in sequence.

| # | Question |
|---|---|
| 1 | "Engineer flagged [N] pm_reopen_requests for service `<S>`. Address each." (loop) |
| 2 | "Engineer relocated [M] file:line entries. Confirm the relocations don't break your per-feature mapping intent." |
| 3 | "Engineer rejected [K] entries as false positives. Confirm these aren't billable handlers you wanted instrumented." |
| 4 | "Engineer's adapter decisions per entry — any that affect billing accuracy (e.g., adapter that can't capture per-token data when you priced per-token)?" |
| 5 | "Any finding that requires CFO involvement (Stage 2b re-open)? If yes, status=bubble-up-to-cfo." |

## How the skill uses this

For each stage, the skill loads the questions, then asks them ONE AT A TIME (per operating-principles HARD RULE). After each answer, the skill:

1. Records the answer to `.moolabs/inventory/reviews/<stage>-signoff.draft.yaml > findings_from_persona[]` if the answer indicates a concern.
2. Updates `edits_to_inventory[]` if the answer is a per-entry override.
3. Persists state — `--resume` continues at the next unanswered question.

After all questions, the skill invokes Skill R (Phase 4), surfaces R's findings ONE AT A TIME for human resolution (accept / risk-accept / reject), then writes the signed YAML.

## Customer-specific questions

If `customer-context/terminology.yaml` has overrides, the skill substitutes terms in the questions (e.g., "completion" → "generation" for a customer who uses that term). The canonical list here uses defaults; runtime substitution preserves intent.
