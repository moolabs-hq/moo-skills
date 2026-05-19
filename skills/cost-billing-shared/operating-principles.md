# Operating principles — applies to EVERY skill in the Cost+Billing suite

Every skill — chain bootstraps, discovery, codemod, drift-lint, cloud-bill, adversarial-review — operates under the same four principles. The chain bootstraps surface these most visibly (they're question-driven), but downstream skills must honor them too, because the customer's repo is unknown territory and silent defaults compound into wrong instrumentation.

## 1. NEVER assume

Every default is a *proposal*, not a silent fact. When you'd otherwise pick "this is probably the case," instead either:

- **Interactive mode** (human is present): surface the default as a question — "I'm about to default to OpenTelemetry for the tracer. Override?"
- **Unattended / CI mode** (no human): refuse-to-run rather than silent-default. Log the ambiguity clearly. The customer's CI logs will surface the question on the next interactive run.

Customers contradict their "context" all the time. The Moolabs dogfood run produced a 17 KB pricing-model.yaml exactly because bootstrap NEVER assumed: it asked "you said Hybrid; let's confirm what that decomposes into" rather than picking the most common interpretation.

## 2. When in doubt, ASK (interactive); FAIL LOUDLY (unattended)

Doubt is a signal. Three cases:

| Context | Signal | Behavior |
|---|---|---|
| Interactive (chain bootstraps, codemod PR review, three-role review) | Confidence < HIGH on any decision | ONE question to the human; never proceed past doubt. |
| Semi-attended (discovery, cloud-bill — human invoked but stepping away) | Confidence < HIGH | Mark the entry with `confidence: medium` or `low`, surface in the review surface, do NOT proceed past it. Human reviews + decides. |
| Unattended (drift-lint in CI, post-codemod adversarial review on a PR) | Confidence < HIGH | Refuse to merge / fail the build / file a finding. Never silent-default. Better to wake a human than to inject a wrong assumption. |

The cheap thing — silent-default with a `# TODO` comment — is the wrong thing. The customer will not read the TODO. The right thing is to surface the doubt in the channel the customer ACTUALLY watches (CI failure, review surface, blocking finding).

## 3. ONE question at a time (interactive only)

Bootstrap chain stages MUST ask one question, wait for the answer, save state, ask the next. Never dump a category as a bulleted list. Breadcrumb format: `[Category N of M, question K of L]`.

If the customer offers multiple answers in one reply, accept all and skip ahead. Never re-ask answered questions.

This rule applies to **bootstrap and review** skills. Discovery and codemod skills produce artifacts non-interactively from already-answered context, so they're exempt — but when discovery hits ambiguity (catalog miss, refund-test borderline), it should still surface ONE question to the review surface, not dump a list.

## 4. Save state after every decision

After every individual question OR every individual artifact emission, persist to the working draft. If the session crashes mid-skill, the customer can resume with `--resume` and pick up at the next unanswered question / next un-emitted artifact. They never re-do work.

Files: `.moolabs/chain/<NN>-<stage>.draft.yaml` for bootstraps; `.moolabs/inventory/*.draft.yaml` for discovery; `.moolabs/codemod/plan.yaml` for instrument.

---

## What this rules OUT (concrete anti-patterns)

| Anti-pattern | Why it's wrong | What to do instead |
|---|---|---|
| "The customer probably uses OTel — let me skip the tracer question." | OTel is the most common tracer; not the most common one for THIS customer. Customers correcting this assumption silently break the codemod's brownfield/greenfield branch detection. | Ask Q4.1 (primary tracer) explicitly. |
| "Their feature is called `chat.completed`, so `event_type=chat.completed` — confirm done." | Customer might call it `generation.delivered` or `message.completed`. Picking the wrong string means the codemod's emitted events don't match the moo-meter rule. | Ask Q5 (event type naming + per-feature value) explicitly. |
| "The repo has a `pyproject.toml`, so it's Python — proceed." | Repo might be polyglot. Some services Python, some TS. Picking "Python" too early skips the TS adapter selection. | Per-service language detection; ask the engineer to confirm scope per service. |
| "Discovery found 12 cost events; auto-confirm them as the inventory." | Auto-confirm bypasses the three-role review — CFO might reject pricing, PM might say 3 are internal-only. | Always surface as DRAFT inventory; require role signoffs before proceeding. |
| "Drift-lint in CI: this entry was deleted; just remove from inventory." | Maybe the entry was deleted because the feature was renamed and the customer wants the old `workflow_id` preserved across the rename. Silent inventory mutation breaks audit trail. | Refuse-to-merge; surface to the integrator with severity HIGH. |
| "Codemod's PII guard flagged `user.email` — strip it from the span attribute." | The customer might WANT it (with consent + GDPR DSAR support). Don't decide for them. | Surface as a CRITICAL adversarial-review finding pre-merge. |

---

## When this is overridden

Three legitimate cases for `--accept-defaults`:

1. **Smoke test / dogfood** — internal validation runs where speed > correctness. Pass `--accept-defaults` explicitly; output is marked `confidence: dogfood`. No customer ever sees it.
2. **Refresh mode** — re-running an already-completed chain to re-verify a single section. Prior answers ARE defaults; the human re-confirms only what changed.
3. **CI re-runs of attribution_engine.py** (Skill C — internal-only) — operates on accumulated corpus where every input was already-confirmed; defaults are the historic confirmed values.

Outside these three: never `--accept-defaults`. The framework's value IS in the asking.

---

## Verification that you followed these principles

The `bootstrap-log.yaml` for each chain stage MUST log:

- `questions_total` — the count of questions actually asked
- `followups_total` — clarifying follow-ups (each itself ONE question)
- `defaults_accepted_with_proposal` — defaults the human OK'd as the proposal
- `defaults_silent` — should always be `0` (any non-zero is a bug)

The Moolabs dogfood run logged `defaults_silent: 0` across all 7 categories. That's the bar.
