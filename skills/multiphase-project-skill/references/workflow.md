# Workflow — End-to-End Walkthrough

This reference shows a complete project lifecycle, with the exact commands at each step.

## Setup

The first time the orchestrator runs in a repository:

```bash
python3 scripts/state_manager.py init "Analytics Dashboard" \
  "Real-time analytics dashboard for the growth team"
```

This creates:

```
.multiphase-project/
└── state.yaml          # project.status: new, phases: []
```

## Stage 1 — Brainstorm

Invoke `/brainstorming` with the user's project description:

```
/brainstorming Real-time analytics dashboard for the growth team — must show
DAU, session length, and funnel conversion per cohort. Internal-only tool.
```

The brainstorming command runs its own Socratic loop with the user. When it returns its synthesis, extract phases.

**Heuristic for phase extraction:**
- A phase is a chunk of work that produces a *demonstrable deliverable* (something a stakeholder can look at and approve).
- Most projects fit in 3-7 phases. Fewer than 3 and you should probably skip the orchestrator; more than 7 and you're decomposing too finely.
- Phases must have a clear order — each one depends on the previous having shipped.

**Example phase plan from the brainstorm above:**

```bash
python3 scripts/state_manager.py add-phase 01-foundation "Foundation" \
  --description "DB schema, event ingestion, CI/CD"
python3 scripts/state_manager.py add-phase 02-metrics "Core metrics" \
  --description "DAU, session length, funnel queries"
python3 scripts/state_manager.py add-phase 03-cohorts "Cohort breakdown" \
  --description "Cohort definition UI and per-cohort queries"
python3 scripts/state_manager.py add-phase 04-ui "Dashboard UI" \
  --description "Charts, filters, drill-down"
python3 scripts/state_manager.py add-phase 05-launch "Launch hardening" \
  --description "Perf, auth, on-call runbook"
```

**Stop here.** Print the plan and ask the user to approve, re-order, or amend. Do not proceed to per-phase work until they say "go".

## Stage 2 — Per-phase loop (one iteration shown)

### 2a. Decompose phase 01-foundation into works

```bash
python3 scripts/state_manager.py add-work 01-foundation w1-schema \
  "Create events, users, sessions, cohorts tables and migrations" \
  --acceptance-criteria "Migrations run clean against a fresh DB; schema reviewed"
python3 scripts/state_manager.py add-work 01-foundation w2-ingest \
  "HTTP endpoint that accepts events and writes to the DB" \
  --acceptance-criteria "POST /events returns 202; events appear in the DB within 1s"
python3 scripts/state_manager.py add-work 01-foundation w3-ci \
  "GitHub Actions workflow: lint, test, build on every PR" \
  --acceptance-criteria "CI fails on lint errors, type errors, and test failures"
python3 scripts/state_manager.py set-phase-status 01-foundation decomposed
```

### 2b. Groom phase — generate the PRD

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation grooming
```

Invoke `/prd` with input that includes the phase and all works:

```
/prd Foundation phase for the Analytics Dashboard. Works:
- w1-schema: Create events, users, sessions, cohorts tables and migrations.
  Acceptance: Migrations run clean against a fresh DB; schema reviewed.
- w2-ingest: HTTP endpoint that accepts events and writes to the DB.
  Acceptance: POST /events returns 202; events appear in the DB within 1s.
- w3-ci: GitHub Actions workflow: lint, test, build on every PR.
  Acceptance: CI fails on lint errors, type errors, and test failures.
```

`/prd` asks clarifying questions and saves to `tasks/prd-foundation.md`. Move it into the phase artifact directory:

```bash
mkdir -p .multiphase-project/phases/01-foundation
mv tasks/prd-foundation.md .multiphase-project/phases/01-foundation/prd.md
python3 scripts/state_manager.py set-artifact 01-foundation prd_path \
  .multiphase-project/phases/01-foundation/prd.md
python3 scripts/state_manager.py set-phase-status 01-foundation groomed
```

### 2c. Develop — drive `/ralph-loop` to its completion promise

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation developing
```

Construct the completion promise — the exact text Ralph must output to exit. Make it phase-specific and verifiable:

```
PHASE-01-foundation DEVELOPMENT COMPLETE: every work listed in
.multiphase-project/phases/01-foundation/prd.md is implemented, all unit
and integration tests pass, git status is clean, and no further changes
are required to satisfy the acceptance criteria.
```

Invoke ralph-loop:

```
/ralph-loop "Implement the works defined in .multiphase-project/phases/01-foundation/prd.md.
After completing each work, run:
  python3 scripts/state_manager.py mark-work-done 01-foundation <work-id>
Run the test suite after each work. Commit each work as its own commit.
Only output the completion promise when every work is implemented, every
test passes, and git status is clean." --completion-promise "PHASE-01-foundation DEVELOPMENT COMPLETE: every work listed in .multiphase-project/phases/01-foundation/prd.md is implemented, all unit and integration tests pass, git status is clean, and no further changes are required to satisfy the acceptance criteria."
```

When ralph exits, verify:

```bash
python3 scripts/state_manager.py status   # all works should be 'complete'
git status                                # should be clean
<run the test suite>                      # should pass
```

If any of these fail, re-enter `/ralph-loop` with a tightened prompt listing the gap. Do not advance until all three are true.

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation developed
```

### 2d. Review — adversarial review loop

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation reviewing
```

Identify the diff for this phase. If you're on a branch that started before this phase began, use `git log --oneline` to find the commit count for this phase, then point `/adversarial-pr-review` at `HEAD~N..HEAD`.

```
/adversarial-pr-review HEAD~3..HEAD
```

Save the output:

```bash
mkdir -p .multiphase-project/phases/01-foundation
# Write reviewer output to:
#   .multiphase-project/phases/01-foundation/review-001.md
python3 scripts/state_manager.py set-artifact 01-foundation review_path \
  .multiphase-project/phases/01-foundation/review-001.md
```

Evaluate against the conditions in `stop-conditions.md`. If unmet:

1. Construct a ralph prompt listing each unmet CRITICAL and HIGH finding verbatim.
2. Use a tightened completion promise: `REVIEW-001 ISSUES RESOLVED: every CRITICAL and HIGH finding in review-001.md has been addressed with code changes and tests.`
3. Run `/ralph-loop` with that prompt + promise.
4. When ralph exits, re-run `/adversarial-pr-review`. Save as `review-002.md`. Re-evaluate.
5. Loop until conditions are met.

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation complete
```

### 2e. Advance to phase 02-metrics

```bash
python3 scripts/state_manager.py advance-phase
git add .multiphase-project/phases/01-foundation
git commit -m "chore(01-foundation): record phase artifacts"
```

## Resume after interruption

A new session, mid-project:

```bash
python3 scripts/state_manager.py status
```

Output:

```
Project: Analytics Dashboard
Status:  in_progress
Phase:   3/5 (index 2)

  [   complete] 01-foundation            (3/3 works) Foundation
  [   complete] 02-metrics               (4/4 works) Core metrics
  -> [ reviewing] 03-cohorts             (2/2 works) Cohort breakdown
  [    pending] 04-ui                    (0/0 works) Dashboard UI
  [    pending] 05-launch                (0/0 works) Launch hardening
```

Resume action: phase 03-cohorts is in `reviewing` — re-invoke `/adversarial-pr-review`, evaluate, loop or advance.

## Edge cases

- **Phase requires re-planning after partial implementation.** Set the phase status back to `decomposed`, add or remove works, then re-enter at 2b.
- **A work turns out to belong to a later phase.** Mark it `skipped` (in-place edit of `state.yaml`, then re-validate by running `status`), and `add-work` it to the destination phase.
- **The whole project pivots.** `init --force` creates a fresh state file. Save the old `state.yaml` as `state.archive-<date>.yaml` first.
