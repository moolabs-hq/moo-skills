---
name: multiphase-project-skill
description: >-
  Use when planning and executing a multi-phase software project end-to-end
  by chaining four existing slash commands — /brainstorming, /prd,
  /ralph-loop, and /adversarial-pr-review — with persistent state between
  phases so work can be paused and resumed across sessions. Activates on
  requests like "plan a multiphase project", "orchestrate phased
  development", "walk this project through brainstorm to PR review", "run
  the full pipeline for X", or any time the user describes a project that
  needs to flow brainstorm -> phase decomposition -> PRD grooming ->
  ralph-loop implementation -> adversarial review. Loops within /ralph-loop
  via its --completion-promise mechanism until each phase's development
  stop-condition is genuinely met, then loops fix -> /adversarial-pr-review
  until zero CRITICAL and zero HIGH findings remain. Never silently
  advances past a phase whose works are incomplete or whose review still
  has unresolved high-severity findings. State lives in
  .multiphase-project/state.yaml at the project root.
license: MIT
metadata:
  author: Moo Labs
  version: 1.0.0
  created: 2026-05-26
  last_reviewed: 2026-05-26
  review_interval_days: 90
  dependencies:
    - name: /brainstorming
      type: slash-command
    - name: /prd
      type: slash-command
    - name: /ralph-loop
      type: slash-command
    - name: /adversarial-pr-review
      type: slash-command
---

# /multiphase-project — Multi-Phase Project Orchestrator

You orchestrate end-to-end execution of a multi-phase software project by chaining four existing slash commands: `/brainstorming`, `/prd`, `/ralph-loop`, and `/adversarial-pr-review`.

Your job is to walk a project from a one-line idea through to merged, adversarially-reviewed code — without losing state between phases, without skipping quality gates, and without stopping until each phase's exit conditions are explicitly met.

## Trigger

User invokes `/multiphase-project` followed by their input:

```
/multiphase-project Build a real-time analytics dashboard for our team
/multiphase-project resume
/multiphase-project status
/multiphase-project replan phase 2 — requirements changed
```

The user can also activate naturally:

```
Plan and execute a multiphase project for X
Walk me through brainstorm -> PRD -> ralph -> review for Y
Orchestrate the full pipeline for Z
Resume my phased project
```

## The contract — non-negotiable rules

1. **Never silently advance.** A phase moves from `developing` -> `developed` only when `/ralph-loop` outputs its completion promise. A phase moves from `reviewing` -> `complete` only when `/adversarial-pr-review` reports zero CRITICAL and zero HIGH unresolved findings.
2. **State is the source of truth.** Every transition writes to `.multiphase-project/state.yaml` via `scripts/state_manager.py`. If state and reality disagree, fix state — do not act from memory.
3. **Resume from disk, never from memory.** On every invocation, first read `state.yaml`. Print the resume point. Continue from the recorded `current_phase_index` and phase status.
4. **Delegate, don't reimplement.** Each stage's reasoning lives in the dedicated command. Invoke `/brainstorming`, `/prd`, `/ralph-loop`, `/adversarial-pr-review` — do not inline their logic.
5. **One artifact directory per phase.** Every output lives under `.multiphase-project/phases/<phase-id>/`. The PRD, the ralph log, the review reports, the works list — all there.
6. **Stop only on the documented exit conditions.** See `references/stop-conditions.md`. Do not invent new ones. Do not skip them because "it looks done".

## Workflow — state machine

```
[Phase 0] init                  -> state.yaml created
[Phase 1] brainstorm            -> phases identified, persisted
[Phase 2] for each phase:
            2a. decompose       -> works enumerated
            2b. groom (/prd)    -> prd.md saved
            2c. develop         -> /ralph-loop with completion promise
            2d. review          -> /adversarial-pr-review (loop until clean)
            2e. complete        -> phase marked done, git commit
[Phase 3] project complete      -> summary printed
```

### Phase 0: Initialization

1. Check for `.multiphase-project/state.yaml` in the project root.
2. **If it exists**: load it via `python3 scripts/state_manager.py status` and print the resume point. Skip to the appropriate phase based on `project.status` and `current_phase_index`.
3. **If it does not exist**: create it.

```bash
python3 scripts/state_manager.py init "<project-name>" "<one-line-description>"
```

The init command creates `.multiphase-project/state.yaml` with `project.status: "new"` and an empty `phases:` list.

### Phase 1: Brainstorm and identify phases

Only run this once per project (when `project.status == "new"`).

1. Invoke `/brainstorming` with the user's project description as input. Let the brainstorming command run its full Socratic loop with the user.
2. From the brainstorm output, extract 3-7 discrete project phases. Each phase must have a clear boundary and a verifiable definition of done. Typical phases: foundation, core features, integrations, hardening, launch.
3. For each phase, capture:
   - `id` — kebab-case, prefixed with the index, e.g., `01-foundation`, `02-core-features`
   - `name` — human-readable title
   - `description` — what this phase delivers
   - `success_criteria` — short bullet list of what must be true to call it done
4. Persist each phase:

```bash
python3 scripts/state_manager.py add-phase 01-foundation "Foundation" \
  --description "Database schema, core services, CI/CD"
```

5. Set `project.status: "in_progress"` (the first `add-phase` call does this automatically).
6. Print the phase plan to the user. **Stop and ask for explicit approval** before entering Phase 2. The user may add, remove, reorder, or rename phases.

### Phase 2: Per-phase loop

For each phase, in order from `current_phase_index`:

#### 2a. Decompose into works

1. Break the phase into 3-10 discrete works (tasks). Each work should be implementable in one ralph-loop session and reviewable as one logical unit.
2. For each work:
   - `id` — kebab-case
   - `description` — what gets built
   - `acceptance_criteria` — how we know it's done
3. Persist each work:

```bash
python3 scripts/state_manager.py add-work 01-foundation w1-schema \
  "Create the database schema and migrations"
```

4. Set phase status to `decomposed`:

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation decomposed
```

#### 2b. Groom — generate PRD

1. Set phase status to `grooming`:

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation grooming
```

2. Invoke `/prd` with input that includes the phase name, description, and the full works list with acceptance criteria. `/prd` will ask clarifying questions and write to `tasks/prd-<feature-name>.md`.
3. **Move** the generated PRD into the phase artifact directory and record the path:

```bash
mkdir -p .multiphase-project/phases/01-foundation
mv tasks/prd-foundation.md .multiphase-project/phases/01-foundation/prd.md
python3 scripts/state_manager.py set-artifact 01-foundation prd_path \
  .multiphase-project/phases/01-foundation/prd.md
python3 scripts/state_manager.py set-phase-status 01-foundation groomed
```

#### 2c. Develop — drive `/ralph-loop` to completion

1. Set phase status to `developing`:

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation developing
```

2. Construct the **completion promise**. This is the exact text Ralph must output to exit the loop. Make it specific to this phase — vague promises let Ralph escape early.

   Template:
   ```
   PHASE-<id> DEVELOPMENT COMPLETE: every work in
   .multiphase-project/phases/<id>/prd.md is implemented, all tests pass,
   git status is clean, and no further changes are required to satisfy
   the acceptance criteria.
   ```

3. Construct the **ralph prompt**. It should reference the PRD path and the state file:

   ```
   Implement the works defined in .multiphase-project/phases/01-foundation/prd.md.
   Track per-work progress with:
     python3 scripts/state_manager.py mark-work-done 01-foundation <work-id>
   Run the project's test suite after each work. Only output the completion
   promise when every work is implemented, tested, and committed.
   ```

4. Invoke:

   ```
   /ralph-loop "<the ralph prompt>" --completion-promise "<the promise text>"
   ```

5. When `/ralph-loop` exits (because Ralph output the promise), verify:
   - Every work in the phase has `status: complete` in `state.yaml`.
   - `git status` is clean.
   - Tests pass.
   If any check fails, **do not advance** — re-enter ralph-loop with a tightened prompt listing the specific gap.
6. Set phase status to `developed`:

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation developed
```

#### 2d. Review — adversarial PR review loop

1. Set phase status to `reviewing`:

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation reviewing
```

2. Invoke `/adversarial-pr-review` over the diff for this phase (typically the branch's commits since the last completed phase, or `HEAD~N` where N is the commit count for this phase).
3. Save the review output to `.multiphase-project/phases/01-foundation/review-001.md` and record:

```bash
python3 scripts/state_manager.py set-artifact 01-foundation review_path \
  .multiphase-project/phases/01-foundation/review-001.md
```

4. **Evaluate** the review against the conditions in `references/stop-conditions.md`. Conditions are satisfied when:
   - Zero CRITICAL findings.
   - Zero HIGH findings.
   - No explicit blocker / "do not merge" verdict from the reviewer.
5. **If conditions are NOT satisfied:**
   - Re-enter `/ralph-loop` with a prompt that lists the unmet findings verbatim and a tightened completion promise: `REVIEW-<n> ISSUES RESOLVED: every CRITICAL and HIGH finding in review-<n>.md has been addressed with code changes and tests`.
   - When ralph exits, re-run `/adversarial-pr-review`. Save to `review-002.md`. Re-evaluate.
   - Loop indefinitely until conditions are satisfied. **Never exit this loop on iteration count alone** — only on satisfied conditions.
6. When conditions are satisfied, set phase status to `complete`:

```bash
python3 scripts/state_manager.py set-phase-status 01-foundation complete
```

#### 2e. Advance to the next phase

```bash
python3 scripts/state_manager.py advance-phase
```

This refuses to advance if any work in the current phase is still pending. Use `--force` only after manually marking skipped works.

Commit the phase's artifacts to git:

```
git add .multiphase-project/phases/01-foundation
git commit -m "chore(01-foundation): record phase artifacts and review"
```

### Phase 3: Project completion

When `advance-phase` reports `now at index N/N`, the project is complete. The state manager automatically sets `project.status: "complete"`.

Print a summary to the user:
- Project name
- Phases completed, each with the path to its PRD and final review
- Total review rounds across all phases
- Git ref range covered

## Resume semantics

On any invocation that is not the first, read `state.yaml` first via `status`. The resume point is the tuple `(current_phase_index, phases[current_phase_index].status)`:

| Phase status | Resume action |
|---|---|
| `pending` / `decomposing` | Re-run 2a (decomposition is idempotent — `add-work` errors on duplicate ids) |
| `decomposed` / `grooming` | Re-invoke `/prd` (2b) |
| `groomed` / `developing` | Re-invoke `/ralph-loop` with the same completion promise (2c) |
| `developed` / `reviewing` | Re-invoke `/adversarial-pr-review`, then evaluate (2d) |
| `complete` | Advance and start next phase (2e -> next 2a) |
| `blocked` | Print the blocker, ask the user how to proceed |

## Error handling

- **Sub-command failure**: capture the failure in `state.yaml` under the phase's `error:` field, set status to `blocked`, ask the user how to proceed (retry / skip / abort). Do not catch and continue silently.
- **Test failure during ralph-loop**: do not exit the loop — Ralph itself should keep iterating until tests pass. If you receive a completion-promise output despite failing tests, that is a bug in Ralph's promise enforcement: reject the promise, re-enter the loop with explicit "tests must pass" wording.
- **Reviewer cannot evaluate diff**: typically means the diff is too large or spans unrelated changes. Break the phase into smaller phases via `add-phase` and re-plan.

## References

| File | When to consult |
|---|---|
| `references/workflow.md` | First-time setup, full example transcript, command flags |
| `references/state-schema.md` | The exact shape of `state.yaml` and all valid status values |
| `references/stop-conditions.md` | How to evaluate ralph completion and review conditions |
| `assets/state-template.yaml` | Empty starter, copied by `init` |
| `scripts/state_manager.py` | State CRUD CLI (run with `--help`) |

## Helper script CLI summary

```bash
python3 scripts/state_manager.py init "<name>" "<description>"
python3 scripts/state_manager.py status [--json]
python3 scripts/state_manager.py add-phase <id> <name> [--description ...]
python3 scripts/state_manager.py add-work <phase-id> <work-id> "<description>"
python3 scripts/state_manager.py set-phase-status <phase-id> <status>
python3 scripts/state_manager.py mark-work-done <phase-id> <work-id>
python3 scripts/state_manager.py set-artifact <phase-id> <key> <path>
python3 scripts/state_manager.py advance-phase [--force]
```
