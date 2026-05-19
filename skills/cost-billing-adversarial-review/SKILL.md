---
name: cost-billing-adversarial-review
description: >-
  Five-phase adversarial review pattern (spec → adversarial pass → fix+verify → robustness sweep → repeat-or-stop) applied as the quality gate for every other Cost+Billing skill. Invoked SIX times per pipeline run — after Skill 1A doc-tree, 1B code-graph, 1C unconfirmed inventory, 1D confirmed inventory, as the holistic gate before Skill 2, and after Skill 2's PR — each with a primary risk class tuned to that stage (hallucinated features, false call edges, hallucinated file:line, refund-test violations, cross-cutting orphans, idempotency-key sloppiness). Cross-model reviewer (codegen and reviewer use different models) with hard 5-round cap and CRITICAL/HIGH/MEDIUM/LOW severity rubric; stops when no CRITICAL or HIGH remains. Produces dated review-spec artifacts as audit trail. Skill R in the suite — applies to Skill A/B/2, NOT to Skill C (Skill C is itself a validator). Triggers on "adversarial review", "Skill R", "review-fix loop", "hostile review", "5-phase review", "block on Skill R gate".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  source: docs/grooming/2026-05-19-cost-billing-discovery-requirements.md §4.5
  related_skills:
    - adversarial-pr-review   # the generic moo-skills adversarial pattern
---

# /cost-billing-adversarial-review — Skill R: The 5-phase adversarial gate

You are a hostile reviewer for the Cost+Billing Discovery & Instrumentation pipeline. Findings are **candidates, not facts** — every finding gets verified before being acted on. You exist because same-model self-review is a known weak spot; you operate as a cross-model reviewer.

## Trigger

```
/cost-billing-adversarial-review --phase <phase-id> --target <artifact>
```

Six valid `--phase` values, mapped to requirements §4.5:

| `--phase` | Triggered after | Target |
|---|---|---|
| `post-skill-1a` | `/cost-billing-discovery` Phase 2 (doc-tree) | `.moolabs/discovery/doc-tree.yaml` |
| `post-skill-1b` | `/cost-billing-discovery` Phase 3 (code-graph) | `.moolabs/discovery/code-graph.yaml` |
| `post-skill-1c` | `/cost-billing-discovery` Phase 5 (unconfirmed inventory) | `.moolabs/inventory/*-inventory.yaml` (pre-review) |
| `post-skill-1d` | After three-role review signoffs | `.moolabs/inventory/*-inventory.yaml` (post-signoff) |
| `holistic-pre-codemod` | After all 1A–1D reviews are clean | `.moolabs/inventory/*` whole |
| `post-codemod` | `/cost-billing-instrument` Phase 3 | the PR(s) emitted by Skill 2 |

Plus an optional Skill B invocation (per `gaps-tracker.md` §6.5 #27):

| `--phase post-skill-b` | After Skill B's first-export scan | `.moolabs/cloud-bill/cell-3-findings.yaml` |

Naturally:

```
Run adversarial review on the unconfirmed inventory
Hostile-review this codemod PR
Skill R, post-codemod, on the latest PR
Block the codemod until R passes
```

## Read first (shared/)

- `v1-decisions-log.md` #8 — your cross-model + 5-round cap + severity rubric.
- `gaps-tracker.md` §6.1 §5.1–§5.6 — your operating constraints.

## The 5-phase pattern (mandatory order; never skip)

### Phase 1: Spec the review

Produce `docs/superpowers/reviews/YYYY-MM-DD-<short-name>-pr-review-execution.md` with these sections:

```markdown
# YYYY-MM-DD <short-name> PR review execution

## Summary of changes
What this artifact contains, what changed since the last review.

## Risk map
The primary risk class for this `--phase` (see table below) + any artifact-specific risks.

## Verification commands
The exact shell commands you'll run to validate each candidate finding.
Examples: `grep -rn "openai.chat.completions" <repo>`, `python -m ast <file>`, ...

## Pre-recorded notes
Anything the reviewer already knows about this artifact (e.g., "engineer flagged
3 entries as low-confidence; PM signed off anyway — examine those first").
```

### Phase 2: Adversarial pass (findings = candidates)

Run the per-phase adversarial checks. **Treat every finding as a candidate that requires verification in Phase 3 before being labeled a real bug.**

**Per-phase risk classes (verbatim from requirements §4.5):**

| Phase | Primary risk class | Specific checks |
|---|---|---|
| `post-skill-1a` | Hallucinated features, malformed hierarchy, missed sections | For each doc-tree leaf, search source docs for the claim. Catch invented features. |
| `post-skill-1b` | False call edges, missed handlers, misclassified routes, framework-idiom blind spots | Spot-check 10% of edges in code-graph.yaml; run AST counter-claims; check framework adapter assumptions hold. |
| `post-skill-1c` | Hallucinated `file:line`, double-mapped handlers, low-confidence false positives, missed non-HTTP entrypoints | For each entry, run `<file>:<line>` against actual repo (`head -n<line> <file> | tail -1`); look for double-mappings; spot-check non-HTTP entrypoints (Celery, Lambda, Cron). |
| `post-skill-1d` | Refund-test violations, sibling-feature inconsistencies, stale `file:line` | Run refund-test against each confirmed terminal event; cross-check sibling features have consistent units. |
| `holistic-pre-codemod` | Orphan features, double-counted endpoints, refund-unit drift, cross-feature trace-context conflicts | Compare cost-events + usage-events + output-input-map for orphans; find inputs feeding two outputs at weight 1.0 each; flag refund-unit mismatches between PM mapping and CFO-declared price. |
| `post-codemod` | Compilation breaks, wrong framework adapter, idempotency-key sloppiness, error paths un-instrumented, security footguns | Run customer's test suite (read-only — do NOT modify); spot-check adapter matches detected framework; audit idempotency derivations; verify error paths don't emit usage events; PII guard checks. |
| `post-skill-b` (optional) | Misclassified untagged spend, missing AWS Bedrock IAM-principal gate, Azure resource-group-only blind spots | Spot-check cell ③ findings against actual export data; verify Bedrock gate post-2026-04-08; flag Azure RG-only patterns. |

**Severity rubric (v1, per `v1-decisions-log.md`):**

- **CRITICAL** — data corruption / compilation break / security footgun introduced. Stops the pipeline.
- **HIGH** — wrong attribution / missing emission / wrong framework adapter / refund-test violation.
- **MEDIUM** — low-confidence-accept used where MEDIUM threshold required override; suboptimal idempotency derivation.
- **LOW** — style; documentation incompleteness; informational only.

### Phase 3: Fix confirmed bugs (verification per fix)

For each candidate finding, verify before fixing:

```
verification: <command output>
verdict: real bug | not a bug (rationale) | accepted non-blocking risk (rationale)
```

If `real bug`, apply the fix on the PR's own branch (per moolabs-pr-review pattern). The fix gets its own three-column entry in the review spec:

```markdown
### Fix: <short-name>

| What was wrong | What changed | What we ran to confirm |
|---|---|---|
| Inventory entry api.render.image-rendered cited file:line render.py:47 — actual code at render.py:62 | Updated entry's file:line; bumped workflow_id_history with old:new mapping | `python -m ast services/api/render.py | grep image_rendered` returns line 62 |
```

### Phase 4: Robustness sweep (graph-hop radius = 2, v1 default)

After fixing, search related routes / handlers / service calls / error paths for the same kind of bug. Hop radius 2 means:

- Sibling routes in the same router/file.
- Direct callers of the affected handler.
- Direct callees of the affected handler.
- Sibling files in the same service.

Apply consistency-with-existing-app-patterns check: if the customer already uses `request_id` as their idempotency anchor everywhere else, your fix had better follow that pattern too.

### Phase 5: Repeat OR stop

**Stop criterion (v1):** No CRITICAL or HIGH severity remaining open. Any MEDIUM/LOW items get marked as "accepted non-blocking risks" with rationale and stay in the review spec for audit.

**Repeat criterion:** Any CRITICAL or HIGH finding remains, AND iteration count < 5.

**Hard cap:** 5 iterations. If the loop has not converged after 5 rounds, escalate to human (the integrator). Cite the unresolved findings in the spec.

## Outputs

| File | Used by |
|---|---|
| `docs/superpowers/reviews/YYYY-MM-DD-<short-name>-pr-review-execution.md` | Audit trail (mandatory per requirements §5.3); downstream skill reads the "verified clean" sentinel. |
| Updated artifact (inventory YAML / codemod patch / cell ③ list) | The skill that triggered this review. |
| `.moolabs/reviews/index.yaml` | Cross-invocation history for the customer integration; lists all R invocations with `verdict`, `phase`, `severity_breakdown`. |

## Mandatory output sentinel

Downstream skills look for one of these verdict strings at the bottom of the review spec:

- `verdict: clean` — no real bugs found.
- `verdict: clean-with-accepted-risks` — real bugs found and fixed; any remaining items accepted as non-blocking with rationale.
- `verdict: blocked` — pipeline cannot proceed; lists the unresolved CRITICAL/HIGH items.

`/cost-billing-instrument` refuses to run without the right verdict on the `holistic-pre-codemod` spec.

## Cross-model operating rule (v1 default)

If the codegen for the artifact-under-review was produced by model X, you (the reviewer) must operate as model Y where Y ≠ X. The session's harness should provide a `--reviewer-model <name>` flag; if not, log a WARNING in the spec header:

```markdown
> ⚠️ Cross-model rule violated: codegen and review both ran on <model>.
> Same-model self-review is a known weak spot (Doc 3 §5.1).
> Treat findings as ~30% less reliable; consider re-running with `--reviewer-model`.
```

## Customer IP policy (per `v1-decisions-log.md` §5.6)

Default review-spec location is `docs/superpowers/reviews/<...>` inside the **customer's** repo. For customers with stricter IP policies, the integrator can pass `--review-spec-out=<external-path>` and the spec is written outside the repo. The customer is responsible for archiving externally-written specs.

## What this skill MUST NOT do

- **Never** skip Phase 1. Spec the review before adversarial-passing.
- **Never** label a finding as a real bug without running its verification command.
- **Never** apply a fix without producing the three-column "what was wrong / what changed / what we ran" entry.
- **Never** loop past 5 iterations. Escalate.
- **Never** review Skill C output — Skill C is itself a validation skill (per `gaps-tracker.md` §6.5 #27).
- **Never** write outside the customer's repo unless `--review-spec-out=<path>` is explicitly passed.

## Related skill

The generic `adversarial-pr-review` in moo-skills implements the same 5-phase pattern at a stack-agnostic level. **This skill specializes it** for the Cost+Billing suite — same pattern, per-phase risk classes tuned to each step in the pipeline.

When in doubt, this skill takes precedence for cost-billing artifacts; `adversarial-pr-review` is the fallback for arbitrary PRs.

## Reference files

- `references/5-phase-pattern.md` — full detail per phase with examples.
- `references/invocation-points.md` — the 6 invocations + how to chain them.
- `references/severity-rubric.md` — CRITICAL/HIGH/MEDIUM/LOW with examples.
- `references/review-spec-template.md` — the markdown template.
- `references/cross-model-reviewer.md` — why cross-model + how to verify.

## Scripts

- `scripts/review_driver.py` — runs the 5 phases sequentially; persists spec.
- `scripts/phase1_spec.py` — emits the initial review spec with the right risk map per phase.
- `scripts/phase2_adversarial.py` — runs per-phase checks; outputs candidate findings.
- `scripts/phase3_fix_verify.py` — verification + fix application + three-column entry.
- `scripts/phase4_robustness_sweep.py` — radius-2 sibling search.
- `scripts/phase5_stop_criterion.py` — checks stop criterion; loops or escalates.

## Assets

- `assets/review-spec-template.md` — the markdown skeleton.
- `assets/severity-examples.yaml` — example findings per severity level.
