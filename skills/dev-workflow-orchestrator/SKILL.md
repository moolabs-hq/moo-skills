---
name: dev-workflow-orchestrator
description: Run the Moolabs end-to-end development chain on a single product story — from a PRD on docs.moolabs.com all the way through grooming, implementation handoff, testing, code review, and post-merge documentation. Pauses for explicit human sign-off after every stage; persists every deliverable back to Outline as a child page of the source PRD; maintains a STATE page so a fresh session can resume from where the last one left off. Use when the user gives a docs.moolabs.com URL and asks to "groom this story end-to-end", "automate this from PRD to PR-ready", "run the full pipeline", "walk through the workflow", "take this PRD and run the chain", or names a feature and asks for the full grooming/test/review loop. Use this — not the individual grooming skills — whenever the user wants the whole flow rather than a single stage. Delegates each stage to the existing moo-skills (grooming-requirements, hld-tech-specs-creator, grooming-contracts, grooming-be, grooming-fe, grooming-task-breakdown, frontend-unit-testing, backend-unit-testing, backend-api-testing, testing-qa-persona, adversarial-pr-review, feature-summariser, plus the validator and persona skills) via the Skill tool. Requires the Outline MCP server to be configured and pointed at docs.moolabs.com.
---

# Dev Workflow Orchestrator

The grooming chain works one stage at a time, but a story typically passes through 13+ stages from PRD to merged-and-documented. Without an orchestrator the human (or the agent) has to remember which stage is next, where the previous deliverable lives, and what input the next stage needs. That memory tax is where things slip — a contracts step gets skipped, a fresh-context validation pass never happens, a feature ships without a future-developer doc.

This skill removes the memory tax. It reads a single PRD URL, walks every stage, persists each deliverable back to Outline so the team has one canonical place to find them, and stops after every stage to wait for human sign-off before proceeding.

## The contract — non-negotiable rules

1. **Stop at every gate.** After each stage produces its deliverable, post a short summary to the user and wait. Do not invoke the next stage's skill until the user explicitly says "go" / "approved" / "next" / equivalent. Silence is not approval.
2. **One canonical home per deliverable.** Every stage's output goes to Outline as a child page of the PRD. Local files in the working repo are fine for cross-referencing during implementation, but Outline is the source of truth.
3. **Keep STATE current after every stage.** Update the `STATE` Outline page after each gate so a fresh session can resume. Without state-keeping the orchestrator becomes impossible to interrupt.
4. **Delegate, don't reimplement.** Each stage's reasoning lives in the dedicated skill (`moo-skills:grooming-requirements`, etc). Invoke it via the Skill tool with the right inputs. Do not inline its instructions — that creates two sources of truth that drift.
5. **Don't skip stages.** If the user wants to skip something, they have to say so explicitly and you must record it in STATE under "Skipped stages" with their stated reason.
6. **Never merge or deploy.** The chain ends at "ready-for-human" / "documentation written". Merge and deploy decisions remain with the human, regardless of how clean the loop looked.
7. **Implementation is not a stage.** The orchestrator hands off to the human (or a coding session) after task breakdown, and picks back up when the user returns with a PR/branch ready for testing and review.

## Stage map

```
0. Bootstrap — read the PRD page, create the workspace
   └─ Outline: <feature>/STATE
   └─ Outline: <feature>/00-source-prd-snapshot

1. Requirements                    → moo-skills:grooming-requirements
   └─ Outline: <feature>/01-requirements
   └─ Gate: clarifying questions answered, gaps closed

2. Fresh-context validation        → moo-skills:grooming-fresh-context-validation
   └─ Outline: <feature>/02-requirements-gap-report
   └─ Gate: every flagged gap resolved or explicitly accepted

3. HLD                             → moo-skills:hld-tech-specs-creator
   └─ Outline: <feature>/03-hld
   └─ Gate: schema + flow + recovery story signed off

4. Sparring round                  → moo-skills:persona-sparring-partner
   └─ Outline: append "Sparring outcomes" section to 03-hld
   └─ Gate: edge cases addressed, alternatives considered

5. API contracts                   → moo-skills:grooming-contracts
   └─ Outline: <feature>/05-contracts
   └─ Gate: every endpoint defined with request/response/errors

6. Cross-doc consistency           → moo-skills:documentation-unifier
   └─ Outline: <feature>/06-consistency-report
   └─ Gate: drift between requirements/HLD/contracts resolved

7. Backend LLD                     → moo-skills:grooming-be
   └─ Outline: <feature>/07-backend-plan
   └─ Gate: services, errors, migrations, FF, observability, tests planned

8. Frontend LLD                    → moo-skills:grooming-fe
   └─ Outline: <feature>/08-frontend-plan
   └─ Gate: components, props, states, FF, Mixpanel, tests planned

9. Task breakdown                  → moo-skills:grooming-task-breakdown
   └─ Outline: <feature>/09-tasks
   └─ Gate: each task independently developable with DoD/AC

—— IMPLEMENTATION HANDOFF (humans / coding sessions code) ——

10. Test plan & implementation     → moo-skills:frontend-unit-testing
                                     moo-skills:backend-unit-testing
                                     moo-skills:backend-api-testing
   └─ Tests live in the repo; STATE notes which suites are green
   └─ Gate: target coverage hit, suites green

11. QA pass                        → moo-skills:testing-qa-persona
   └─ Outline: <feature>/11-qa-report
   └─ Gate: failures triaged, real bugs filed back to dev

12. Adversarial review             → moo-skills:adversarial-pr-review
   └─ Outline: <feature>/12-review-execution-spec
   └─ Gate: PR marked ready-for-human; merge decision belongs to user

13. Future-developer summary       → moo-skills:feature-summariser
   └─ Outline: <feature>/13-architecture-summary
   └─ Gate: doc readable cold by someone who's never seen the feature
```

## Stage 0: Bootstrap

When invoked, the user gives a docs.moolabs.com URL (or a feature slug if resuming).

```
1. Resolve the URL via Outline MCP. Fetch the PRD body.
2. Derive a slug:  <feature-slug> = lowercase-kebab(prd-title) (e.g., "customer-portal-redesign").
3. Check Outline for an existing folder named <feature-slug> under the team's grooming workspace.
   - If it exists, read STATE and resume from the first non-done stage. Confirm with user.
   - If it does not, create the folder and the initial STATE page (template below).
4. Snapshot the PRD body to <feature-slug>/00-source-prd-snapshot. The snapshot is read-only — it
   captures the PRD as it was at kickoff so future drift between Outline and the chain's outputs
   is detectable.
5. Post a kickoff message to the user:
   "Starting end-to-end chain on '<title>'. Workspace: docs.moolabs.com/<feature-slug>.
    Stage 1 (Requirements) is next. Reply 'go' when ready."
   Then stop.
```

### STATE page template

```markdown
# STATE — <feature-slug>

Source PRD: <docs.moolabs.com URL>
Started: YYYY-MM-DD by <agent / human identifier>
Current stage: 1 — Requirements
Status: awaiting-go

## Stages
| # | Stage | Status | Outline page | Sign-off by | Notes |
|---|-------|--------|--------------|-------------|-------|
| 0 | Bootstrap | done | 00-source-prd-snapshot | (auto) | |
| 1 | Requirements | pending | — | — | |
| 2 | Fresh-context validation | pending | — | — | |
| 3 | HLD | pending | — | — | |
| ... | ... | ... | ... | ... | ... |

## Skipped stages
None — OR — Stage N skipped on YYYY-MM-DD because <user's stated reason>.

## Open questions
- (questions raised mid-chain that don't block the current stage but need answering before stage X)

## Blockers
- (anything stuck waiting on someone outside the loop — design, legal, data)
```

`Status` values: `pending`, `in-progress`, `awaiting-go`, `done`, `skipped`, `blocked`.

## Per-stage execution pattern

For every stage from 1 onward, follow the same pattern:

```
a. Read STATE. Confirm the current stage matches what the user expects.
b. Set the stage's row to "in-progress" and save STATE.
c. Gather inputs (previous stage's Outline page + any earlier deliverables the
   stage depends on — see "Stage inputs" below).
d. Invoke the stage's skill via the Skill tool, passing:
     - The PRD/feature slug
     - The relevant input doc(s) from Outline
     - "Write the deliverable to Outline page <feature>/<NN-stage-name>"
   The invoked skill is responsible for the stage's substance.
e. When the skill returns its draft, write/update the Outline page and update STATE
   to "awaiting-go".
f. Post to the user:
     "Stage N (<stage name>) draft is at docs.moolabs.com/<feature>/<NN-stage-name>.
      Highlights: <2–3 bullets>. Reply 'go' to advance, 'revise: <feedback>' to iterate
      this stage, or 'skip with reason: <reason>' to skip."
g. Stop. Do not invoke the next stage until the user replies "go".
h. On "go", flip the stage's STATE row to "done" + record sign-off, and proceed to
   the next stage. On "revise", re-invoke the same skill with the user's feedback
   layered onto the inputs. On "skip", record skip + reason in STATE, advance.
```

The pause at step (f) is the single most important rule. It's the orchestrator's reason to exist. Resist the temptation to chain stages — the human's review at each gate is what keeps a 13-stage automation from compounding errors.

## Stage inputs (what each skill receives)

| Stage | Reads | Writes |
|---|---|---|
| 1 — Requirements | 00-source-prd-snapshot, Figma links from PRD | 01-requirements |
| 2 — Fresh-context validation | 01-requirements | 02-requirements-gap-report |
| 3 — HLD | 01-requirements (post-gap-resolution) | 03-hld |
| 4 — Sparring | 03-hld | append to 03-hld under "Sparring outcomes" |
| 5 — Contracts | 03-hld | 05-contracts |
| 6 — Cross-doc consistency | 01, 03, 05 together | 06-consistency-report |
| 7 — Backend LLD | 01, 03, 05 (post-consistency) | 07-backend-plan |
| 8 — Frontend LLD | 01, 03, 05, Figma | 08-frontend-plan |
| 9 — Task breakdown | 01, 07, 08 | 09-tasks |
| 10 — Test plans | 07, 08 | repo test files; STATE note |
| 11 — QA pass | repo HEAD; 09-tasks | 11-qa-report |
| 12 — Adversarial review | open PR(s) for the feature | 12-review-execution-spec |
| 13 — Summary | repo HEAD; 03-hld; 07; 08 | 13-architecture-summary |

If a downstream stage finds a contradiction in an upstream doc, surface it back and pause — do not silently re-edit the upstream page without a human "go". This is what stage 6 (cross-doc consistency) is for, but contradictions can surface anywhere.

## Resuming a paused chain

When a user comes back days later and points the orchestrator at the same PRD:

```
1. Look up <feature-slug>/STATE in Outline.
2. Find the first row whose status is not "done" or "skipped".
3. If status is "in-progress", politely ask whether someone took over the work outside
   the loop. If yes, ask them to summarise; if no, restart that stage from scratch.
4. If status is "awaiting-go", remind the user what the deliverable is and ask whether
   to proceed, revise, or skip.
5. If status is "pending", run the per-stage pattern (above) starting at step (a).
```

Do not assume the previous session's reasoning was correct — re-read the upstream pages before invoking the next skill. Outline content can change between sessions when humans edit directly.

## Implementation handoff (between stages 9 and 10)

After stage 9 (task breakdown), the chain pauses indefinitely. The human (or a separate coding session) executes the tasks. When the user returns, they typically say something like "PR is up at #N, run the rest of the chain". At that point:

- Verify the PR exists, fetch its head SHA.
- Note the SHA in STATE under stage 10.
- Run stages 10–13 in sequence with the same per-stage pattern.

If the user comes back without a PR ("the work is on branch X but not a PR yet"), ask whether to skip review (stage 12) until the PR opens, or pause until then. Don't run review on a branch with no PR — `adversarial-pr-review` operates on PR numbers.

## What "ready" means at the end

The chain is complete when:

- STATE has every stage set to `done` (or explicitly `skipped` with a reason)
- Stage 12's spec page lists zero remaining real bugs (or only documented residue)
- Stage 13's summary is in Outline and links from the PRD
- The PRD page has a "Status: shipped (pending merge)" callout linking to the STATE page

The orchestrator does not flip the PRD's own status — that's the PM's call.

## Common pitfalls

| Pitfall | What to do instead |
|---|---|
| Chaining stages without pausing because "the user's been responsive" | The pause is not a courtesy — it's the only error-correction mechanism. Pause every time. |
| Re-running stage 1 from scratch on a "revise" reply | Layer the user's feedback onto the existing 01-requirements page; don't blow it away. |
| Updating Outline pages without bumping STATE | A page edit without a STATE update breaks resume. Update both atomically when possible. |
| Treating Stage 6 (consistency) as optional because the docs "look fine" | Drift is silent until it bites in stage 7 or 8. Always run it. |
| Skipping the sparring round (stage 4) on small features | The skill is cheap and catches edge cases. The cost of running it is far less than the cost of a missed concurrency or recovery gap. |
| Letting stage 10 (tests) run before stage 7/8 are signed off | Tests written against a contradicting plan are wasted work. Don't start tests until BE+FE plans are `done`. |
| Auto-merging after stage 12 returns clean | Stage 12 explicitly hands the merge decision back to the user. The orchestrator never merges. |

## When NOT to use this skill

- Single-stage requests ("just groom the BE for this", "review PR #380") — use the dedicated skill directly.
- Tiny stories that finish in <30 minutes end-to-end. The overhead of 13 sign-offs is worse than the risk of skipping them. Use the skills selectively instead.
- Spikes / experiments where the goal is learning, not shipping. Grooming pretends precision exists; spikes are explicitly precision-free.

## Outline MCP usage

The skill assumes the Outline MCP server is configured and authenticated against docs.moolabs.com. Specifically the chain needs the ability to:

- Search/list documents by path or title
- Fetch a document's body (markdown)
- Create a document under a parent
- Update a document's body
- Append a section to an existing document

Tool names depend on which Outline MCP build is installed — discover them at runtime by listing available `mcp__*outline*__*` tools and pick the closest semantic match for each operation. If a needed operation is not exposed, surface that to the user before starting the chain rather than silently working around it (e.g., writing to a local file instead).

If the MCP is unavailable, do not start the chain — abort and tell the user the dependency is missing.
