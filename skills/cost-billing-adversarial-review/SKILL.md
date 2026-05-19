---
name: cost-billing-adversarial-review
description: >-
  Five-phase adversarial review pattern applied as the hostile gate at every workflow handoff in the Cost+Billing suite. Reviews each role's plan-document (finance / product / engineer generate NO code — just plans) AND the codemod's PR (the only stage that emits new code). Six invocation points: post-discovery, post-cfo-stage1 (pricing + fair-usage plan), post-pm-stage2 (output-input bill-of-materials), post-engineer-stage3 (file:line + adapter + idempotency spec), holistic-pre-codemod, post-codemod (generated PR). Risks tuned per artifact: hallucinated billable features, pricing inconsistency, orphan outputs, refund-unit drift, wrong file:line, idempotency collisions, security footguns. Cross-model reviewer, 5-round cap, CRITICAL/HIGH/MEDIUM/LOW severity stops at no CRITICAL or HIGH remaining. Triggers on "adversarial review", "Skill R", "review the CFO plan", "review the PM plan", "review the engineer spec", "hostile review of codemod PR".
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

## What this skill reviews — and what it does NOT

The Cost+Billing suite has three role-specific generators and one code generator. **Skill R reviews each one with the adversarial mindset the partner loops can't bring.**

**Roles that generate PLANS (no code emitted):**
- ✅ **CFO** writes `reviews/cfo-spec.md` and fills `cfo_metadata` blocks in `usage-events-inventory.yaml` — pricing decisions, fair-usage values, projected revenue. **Document only — no code.** Skill R reviews this plan.
- ✅ **Product Manager** writes `reviews/pm-spec.md` and builds `output-input-map.yaml` — billable-unit selection, bill-of-materials linkage graph, per-edge weights. **Document only — no code.** Skill R reviews this plan.
- ✅ **Engineer** writes `reviews/engineer-spec.md` and verifies `file:line` / framework adapter / idempotency anchor in `cost-events-inventory.yaml` — pointers to **existing customer code**, false-positive rejections, framework adapter overrides. **Spec only — the engineer does NOT write new code at this stage.** Skill R reviews this plan.

**The one component that generates NEW code:**
- ✅ **`/cost-billing-instrument`** (the codemod) reads all three role plans + the inventories and emits a PR that wires SDK calls into the customer's code. **This is the only stage where new code is produced.** Skill R reviews the PR.

The CFO ⇄ PM and Engineer ⇄ PM loops are *partner reviews* — each role evaluates the other through their own lens (CFO sees price-implication; PM sees business-logic; engineer sees code-reality). Skill R is *adversarial* — assumes every claim is wrong until verified against the underlying artifact.

## Trigger

```
/cost-billing-adversarial-review --phase <phase-id>
```

Seven valid `--phase` values, mapped to the workflow stages in `cost-billing-shared/three-role-review.md`:

| `--phase` | Triggered after | Target | Adversarial risks |
|---|---|---|---|
| `post-discovery` | `/cost-billing-discovery` Phase 5 (initial inventory build, pre-role-stages) | doc-tree + code-graph + draft inventories | Hallucinated features, false call edges, hallucinated `file:line`, double-mapped handlers, refund-test violations, catalog misses missed. |
| `post-cfo-stage1` | CFO generated `cfo-spec.md` + filled `cfo_metadata` | `reviews/cfo-spec.md` + `usage-events-inventory.yaml` (cfo blocks) | Hallucinated billable features, missing fair-usage, pricing inconsistency across similar features, internal-marked entries that should be billable, projected revenue arithmetic. |
| `post-pm-stage2` | PM generated `pm-spec.md` + built `output-input-map.yaml` | `reviews/pm-spec.md` + `output-input-map.yaml` + PM's edits to `refund_unit` | Orphan outputs (no inputs mapped), double-counted inputs (one input feeding two outputs at weight 1.0 each), refund-unit drift between PM's unit and CFO's price model, weight sums outside [0.95, 1.05], missing inputs vs code-graph evidence. |
| `post-engineer-stage3` | Engineer generated `engineer-spec.md` + verified file:line / adapter / idempotency | `reviews/engineer-spec.md` + `cost-events-inventory.yaml` (engineer's edits) | Wrong `file:line` (function moved), wrong framework adapter (e.g., picked fastapi but actually Litestar), idempotency-anchor that won't work at this call site, missed false-positive (retry loops, health checks left in), stale relocations. |
| `holistic-pre-codemod` | All three role-stage Skill R invocations clean + signoffs present | All three artifacts as one cross-cutting whole | Cross-stage drift — refund-unit at output level doesn't match unit at input level; trace-context conflicts across handlers; orphan features only visible cross-stage; cells ③/④ that need re-classification; idempotency-key collisions across outputs. |
| `post-codemod` | `/cost-billing-instrument` Phase 3 | The PR(s) emitted by the codemod | Compilation breaks, wrong adapter chosen, idempotency-key sloppiness, error paths un-instrumented, PII / security footguns, brownfield/greenfield mismatch. |

Plus an optional Skill B invocation (per `gaps-tracker.md` §6.5 #27):

| `--phase post-skill-b` | After Skill B's first-export scan | `.moolabs/cloud-bill/cell-3-findings.yaml` | Misclassified untagged spend, missing Bedrock IAM-principal gate, Azure resource-group-only blind spots. |

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

**Per-phase risk classes + verification commands:**

| Phase | Primary risk class | Specific verifications to run |
|---|---|---|
| `post-discovery` | Hallucinated features, false call edges, hallucinated `file:line`, double-mapped handlers, refund-test violations | For each doc-tree leaf, search source docs (and `customer-context/product-summary.md`) for the claim — catch invented features. Spot-check 10% of code-graph edges via AST counter-claims. For each inventory entry: `head -n<line> <file> \| tail -1` to verify `file:line` matches the named handler. Run refund-test against each terminal-event candidate. |
| `post-cfo-stage1` | Hallucinated billable features, missing fair-usage, pricing inconsistency, projected-revenue arithmetic errors | Cross-check every `cfo_metadata.proposed_billed_unit` against `customer-context/pricing-model.yaml` — does the customer's actual pricing page support this unit? Look for sibling features priced inconsistently (e.g., one per-token, one per-call) without rationale. Verify `projected_monthly_revenue_usd = price × estimated_volume`. Catch entries CFO marked `internal` that have customer-facing handlers in the repo (likely wrongly marked). |
| `post-pm-stage2` | Orphan outputs, double-counted inputs, refund-unit drift vs CFO price, weight-sum errors, missed inputs vs code-graph evidence | For each output: assert at least one input is mapped (no orphans). For each input: sum weights across outputs ≤ 1.0 (no double-counting). Cross-check `refund_unit.unit` (PM's pick) against `cfo_metadata.proposed_billed_unit` (CFO's pick) — flag mismatch. For each edge, run a code-graph proximity check — does the cost-event actually appear in the handler subtree of the usage-event? PM may have missed inputs. |
| `post-engineer-stage3` | Wrong `file:line`, wrong framework adapter, idempotency-anchor unworkable, missed false-positives, stale relocations | For each entry: `head -n<line> <file> \| tail -1` AND grep for the operation in the named handler — both must match. Cross-check `framework` field against `customer-context/repo-info.yaml` (does this service really use that framework?). Verify `idempotency_anchor.path_param` is actually set at this call site (grep upward to the route handler signature). Spot-check rejected entries — are they really false positives, or did engineer over-reject? |
| `holistic-pre-codemod` | Cross-stage drift, trace-context conflicts, orphan features only visible cross-stage, idempotency-key collisions | Compare cost-events + usage-events + output-input-map as one whole. Find inputs feeding two outputs at weight 1.0 (double-count). Flag refund-unit drift between PM mapping and CFO-declared price. Check idempotency-keys don't collide across outputs (`{handler}.{id}.{epoch}` must produce distinct keys per output). Verify cells ③/④ get re-classified after engineer's relocations. |
| `post-codemod` | Compilation breaks, wrong adapter chosen, idempotency-key sloppiness, error paths un-instrumented, security footguns introduced by codemod | Run customer's test suite (read-only — do NOT modify). Per-file: confirm adapter matches `customer-context/repo-info.yaml` framework. Audit each idempotency-key derivation site for variable scoping. Verify error/except paths don't emit usage events. Check inserted span attributes against `customer-context/telemetry-stack.yaml`'s `existing_attributes_prefix` — no collisions. PII-guard regex check on every inserted attribute name + value template. |
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
- **Never** review Moolabs-internal infrastructure outputs (e.g., the attribution-engine reconciliation harness) — those have their own internal validation.
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
