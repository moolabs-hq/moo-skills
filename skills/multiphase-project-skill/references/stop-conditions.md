# Stop Conditions

Two stages in this orchestrator are open-ended loops. Each has a precise exit condition. Apply them strictly — sloppy stop conditions are how phases get marked done while still broken.

## Stop condition 0 — Phase decompose planning gate

Before invoking `/ralph-loop` for any phase, verify that the phase decompose checklist has been completed (see `references/phase-decompose-checklist.md`). This gate must be satisfied before the phase advances from `decomposed` to `groomed`.

The checklist answers must be recorded in the phase's work notes or PRD preamble. Unmarked items — even if N/A — mean the gate is not satisfied.

**Why this is a stop condition, not a guideline:** The six checklist items correspond to the six CRITICAL and IMPORTANT finding categories that consistently surface during adversarial review. A phase that enters `/ralph-loop` without answering these items will produce the same bugs on every project. The checklist doesn't replace review — it eliminates preventable findings so review time focuses on genuinely hard problems.

**If you cannot answer an item:** that is itself a signal. A work item whose idempotency mechanism you cannot name is underspecified — break it down further before proceeding.

---

## Stop condition A — `/ralph-loop` development

### Mechanism

`/ralph-loop` exits when the inner agent outputs its `--completion-promise` text verbatim. The promise is supplied by the orchestrator at invocation time. Ralph itself enforces "no false promise" rules; the orchestrator's job is to supply a promise that, if true, genuinely means the phase is done.

### Required promise shape

```
PHASE-<phase-id> DEVELOPMENT COMPLETE: every work listed in
.multiphase-project/phases/<phase-id>/prd.md is implemented, all unit
and integration tests pass, git status is clean, and no further changes
are required to satisfy the acceptance criteria.
```

The promise must reference:

1. A unique phase identifier (so Ralph cannot reuse a promise across phases).
2. The PRD path (so Ralph has a concrete checklist).
3. **Tests pass** — explicit, not implied.
4. **git status clean** — no half-finished work, no uncommitted scratch files.
5. **Acceptance criteria satisfied** — the binding standard, not Ralph's self-assessment of "looks right".

### After ralph exits, re-verify before advancing

Ralph's promise enforcement is not infallible. The orchestrator must independently re-verify:

```bash
python3 scripts/state_manager.py status   # every work in phase: status == complete?
git status --porcelain                    # output is empty?
<the project's test command>              # exit code 0?
```

If any check fails, **reject the promise**: do not set the phase to `developed`. Re-invoke `/ralph-loop` with a tightened prompt listing the exact gap:

```
The previous loop exited with the completion promise, but verification
failed. Specifically:
- <list the failing checks>
Continue the implementation. Do not output the completion promise until
all of the above are resolved.
```

### What is NOT a stop condition

- **Iteration count.** Ralph may legitimately need many iterations. Do not cap.
- **Wall-clock time.** Same reason. If the phase is dragging, the right response is to break it into smaller phases, not to time out.
- **"Looks done" from the orchestrator.** Always defer to the three verification checks. Vibes are not a stop condition.

## Stop condition B — `/adversarial-pr-review`

### Mechanism

`/adversarial-pr-review` produces a markdown report with findings, each at a severity level. The orchestrator parses the report and applies the conditions below.

### Conditions for "satisfied"

ALL of these must be true:

1. **Zero CRITICAL findings.** A CRITICAL is something that breaks production, leaks data, corrupts state, or violates a hard requirement (e.g. an architectural rule from CLAUDE.md or rules/).
2. **Zero HIGH findings.** A HIGH is a bug, a security weakness, a contract violation, or a correctness issue that would block merge.
3. **No explicit blocker verdict.** If the reviewer's summary contains phrases like "do not merge", "blocking issue", "requires rework", treat as unsatisfied even if no individual finding is tagged CRITICAL/HIGH.
4. **No unresolved questions the reviewer has flagged as must-answer.** "Why does X do Y?" with no answer in the diff is a blocker; let it ride only if the reviewer explicitly notes it's optional.

MEDIUM and LOW findings do not block phase completion, but should be captured in `state.yaml` under the phase's `notes` for follow-up.

### Loop semantics

If conditions are not satisfied:

1. Take the unmet findings (CRITICAL + HIGH + explicit blockers + must-answer questions).
2. Construct a ralph-loop prompt that lists each one verbatim with its review-report line reference.
3. Use a tightened completion promise that names the review report:
   ```
   REVIEW-<n> ISSUES RESOLVED: every CRITICAL and HIGH finding in
   .multiphase-project/phases/<phase-id>/review-<n>.md has been addressed
   with code changes and tests, git status is clean, and the changes are
   ready for re-review.
   ```
4. Run `/ralph-loop`.
5. When ralph exits, re-invoke `/adversarial-pr-review`. Save as `review-<n+1>.md`.
6. Re-evaluate. Loop.

### What is NOT a stop condition

- **"We've reviewed three times, that's enough."** Iteration count is not a stop condition. If round 3 still has CRITICAL findings, run round 4. If a finding genuinely cannot be resolved within this phase's scope, set the phase to `blocked` via `set-error` and escalate to the user — do not silently drop the finding.
- **"The reviewer is being too strict."** That is for the human to decide, not the orchestrator. If the user explicitly says "this finding is not a blocker, mark it accepted", record their justification in `state.yaml` under the phase's `accepted_findings` array and proceed. Otherwise the finding stands.
- **"The fix is too risky."** Same as above — human decision, recorded with justification, not autonomously overridden.

## How to record findings

When a review completes, write the report to `.multiphase-project/phases/<phase-id>/review-NNN.md` (zero-padded, three digits). Update the phase's `review_path` artifact to point at the *latest* review. Older reviews stay on disk for traceability.

After each loop iteration, append a one-line summary to the phase's `notes`:

```yaml
notes:
  - "review-001: 2 CRITICAL (SQL injection in events handler, missing auth on /admin), 3 HIGH"
  - "review-002: 0 CRITICAL, 1 HIGH (rate limit missing on /events)"
  - "review-003: 0 CRITICAL, 0 HIGH — satisfied"
```

This trail lets a future session (or a human auditor) see how many rounds it took and what was actually fixed.

## Quick reference table

| Stage | Exit signal | Verification before advancing |
|---|---|---|
| `/ralph-loop` development | Promise text output | All works `complete`, git clean, tests pass |
| `/adversarial-pr-review` | 0 CRITICAL + 0 HIGH + no blocker verdict + no unresolved must-answer questions | None — the review itself is the verification |
