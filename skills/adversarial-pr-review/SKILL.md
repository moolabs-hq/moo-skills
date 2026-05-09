---
name: adversarial-pr-review
description: Run an adversarial review-fix loop on one or more open PRs — read the diff, dispatch a hostile review via superpowers:requesting-code-review, verify each finding, fix confirmed bugs on the PR's own branch, and iterate until the review is clean. Use this whenever the user asks for adversarial / hostile review, "review until clean", "find bugs in PR #X", a review-and-fix workflow, ship-block on review, or making sure a PR is merge-ready. Triggers on phrases like "adversarial review", "hostile review", "review and fix this PR", "make sure PR is ready", "review-fix loop", or any mention of multiple review rounds before merge. Use this — not a one-shot review — when the user names specific PR numbers and asks for review-quality output, even if they don't say the word "adversarial". Never merges without explicit permission. Delegates the actual reviewing to superpowers:requesting-code-review and orchestrates the fix loop on top.
---

# Adversarial PR Review

The adversarial review loop is the difference between "tests pass on a PR" and "the PR is actually ready to merge." Tests prove compilation and basic correctness. Adversarial review proves robustness against the bugs the test suite doesn't yet have a case for.

This skill orchestrates that loop. It does not replace `superpowers:requesting-code-review` — it stands on top of it, adding the verify-fix-iterate workflow around each review pass.

## When this applies

Use this skill when the user asks for any variant of:
- "review PR #X adversarially / hostilely"
- "review PRs #A, #B, #C until clean"
- "find bugs in this PR and fix them"
- "run a review-fix loop on this PR"
- "make sure PR #X is ready before merge"
- "ship-block PR #X on review"

Use it for one PR or a list. The same loop applies to each PR independently.

If the user only asks for a single review pass with no expectation of fixes ("just take a look"), use `superpowers:requesting-code-review` directly instead — this skill is overkill for that.

## The contract — non-negotiable rules

1. **Never merge.** The user must explicitly say "merge it" or "go ahead and merge". Approval to fix is not approval to merge. A `gh pr merge` invocation without an explicit user instruction in the immediate prior turn is forbidden.
2. **Don't revert unrelated changes.** A bug fix is scoped to the specific bug. If the PR also adds unrelated work, leave it alone — the user kept it for a reason.
3. **Fix on the PR's own branch.** Each bug fix lands on the branch the PR was opened against — not master, not a new branch, not a fork.
4. **Push fixes after each commit.** The PR remote branch is the source of truth that the next review round inspects.
5. **Treat review findings as candidates, not facts.** The reviewer subagent is fast and broad, but it can hallucinate APIs, misread context, or be wrong about language semantics. Reproduce or verify each finding before fixing.
6. **Per-PR loops are independent.** Each PR runs its own review-fix loop. If PR #B genuinely depends on PR #A, document that explicitly in the spec rather than implicitly mixing them.

## Loop overview

```
For each PR:
  Phase 1: Understand the PR
  Phase 2: Adversarial review
  Phase 3: Verify and fix confirmed bugs
  Phase 4: Robustness sweep — find sibling bugs
  Phase 5: Repeat 2-4 until clean
Final: Report status. Do not merge unless told.
```

## Phase 0: Create the review execution spec

Before touching any PR, create a spec document at:

```
docs/superpowers/reviews/YYYY-MM-DD-<short-name>-pr-review-execution.md
```

Where `<short-name>` is something like `pr-367-namespace-mw` or `auth-pr-batch`. If `docs/superpowers/reviews/` doesn't exist, create it.

The spec is the human-readable audit trail. Update it after every phase. Without the spec the user has no way to verify what you actually did. The required structure:

```markdown
# Adversarial PR Review — <short-name>
Date: YYYY-MM-DD
Operator: <agent / model identifier>

## PRs in scope
| PR  | Branch                   | Base    | Head SHA  | Status      |
|-----|--------------------------|---------|-----------|-------------|
| #N  | feat/foo                 | master  | abc1234   | in-progress |

## Cross-PR dependencies
None  — OR  — PR #B depends on PR #A because <reason>.

## Per-PR detail

### PR #N — <one-line summary>
- Branch: feat/foo
- Base: master
- Head SHA at start: abc1234
- Summary of changed areas: <list>
- Risk map by subsystem:
    - Subsystem A: <risk and why>
    - Subsystem B: <risk and why>
- Verification commands used:
    - `<cmd>` → <PASS|FAIL|skipped because Y>
- Review rounds:
    - Round 1: <findings count> — <one-line summary of each>
    - Round 2: <findings count> — <one-line summary>
- Bugs fixed:
    - Bug: <description, severity>
      Commit: <SHA> — <message>
      Verification: <command + result>
- Findings rejected (false positives):
    - Finding: <description>
      Reason rejected: <why>
- Remaining risks (accepted non-blocking):
    - <risk + why it's acceptable + user ack reference>
- Status: in-progress | ready-for-human | blocked

## Final summary
- PR #N: <status>, fixes: <commit SHA list>
- Merge status: NOT MERGED — awaiting explicit user permission.
```

## Phase 1: Understand the PR

Don't review what you don't understand. For each PR:

```bash
gh pr checkout <number>
git fetch origin <base-branch>
git log --oneline origin/<base-branch>..HEAD
git diff origin/<base-branch>...HEAD --stat
git diff origin/<base-branch>...HEAD       # full diff
gh pr view <number> --json title,body,additions,deletions,changedFiles
```

Build the risk map. The categories below are not a complete list — they're the ones that have produced the most surprising bugs in past loops:

- **Migrations / schema changes** — DDL, ent schema edits, atlas diffs. High blast radius on a populated DB. `NOT NULL` on existing rows, dropped columns, type narrowing.
- **Runtime config changes** — viper defaults, env vars, secrets, feature flags. A safe-looking non-empty default that activates new code paths is a footgun.
- **Routes / API changes** — new endpoints, changed auth, removed routes, changed response shapes. Customers may depend on the old shape.
- **Build / dependency changes** — `go.mod`, `package.json`, `requirements.txt`, `Dockerfile`. Pinning issues, transitive vulns, lockfile drift, version skew between dev / CI / prod.
- **Generated code** — `wire_gen.go`, `ent/db/`, openapi clients, codegen output. Re-running the generator with a different version produces different output and breaks builds. Verify the diff matches what the generator says it should produce.
- **Test coverage** — what's tested vs only structural? A PR adding a flag-gated code path with only `flag=false` tests is suspect — there's no proof the `true` branch actually works.
- **Concurrency / shared state** — middleware ordering, goroutine spawns, mutex changes, channel semantics.
- **Cross-deployment behavior** — code that branches on cloud vs on-prem, on-flag-on vs flag-off, single-node vs cluster.
- **Auth surface** — middleware ordering relative to body parsing, header extraction, token validation. A pre-auth middleware that 400s on missing OM-Namespace breaks /healthz too.

Write each subsystem and its specific risks into the spec under "Risk map by subsystem". Don't generalize — name the function or file.

## Phase 2: Adversarial review

Invoke the `superpowers:requesting-code-review` skill. Brief the reviewer adversarially — make it look for bugs, not style:

```
Adversarially review the diff between <BASE_SHA> and <HEAD_SHA> on branch
<branch>. Context: <one-paragraph what the PR does and why>.

Focus on:
1. Correctness bugs — wrong logic, off-by-one, race conditions, nil deref
2. Crash / panic paths — unchecked errors, nil dereferences, type assertions
3. Migration / schema issues — destructive DDL, missing rollback, NOT NULL on
   existing rows, atlas/ent mismatch
4. Dependency / runtime failures — broken imports, version skew, generated
   code out of sync with the spec
5. Broken routes — missing auth, wrong middleware order, openapi mismatch
   with handler signatures
6. Bad assumptions — feature-flag re-evaluation, TOCTOU, "this can't happen"
7. Security footguns — secrets in logs, SSRF, SQL injection, missing CSRF,
   permissive CORS, hardcoded credentials, plaintext tokens
8. Missing test coverage — code paths only existing on the `true` branch of a
   flag with no test, error returns never asserted, integration paths only
   covered by mocked unit tests

Report each finding as: severity (CRITICAL / IMPORTANT / MINOR / NIT),
file:line, what the bug is, why it's wrong, and a suggested minimal fix.
Skip style nits and pure formatting.
```

Save the reviewer's output. Update the spec with the round number and the finding count.

## Phase 3: Verify and fix confirmed bugs

For each reviewer finding, work through this sequence — not the next bug until this one is resolved one way or the other:

### 3a. Reproduce or verify

The reviewer can be wrong. Read the file. Is the bug real? Common false positives:
- Reviewer hallucinates an API that doesn't exist in this codebase
- Reviewer misreads the diff (flags the OLD code thinking it's new, or vice versa)
- Reviewer misses context from related files (e.g. claims a value is unchecked, but a caller already validated it)
- Reviewer is wrong about language semantics (e.g. "this is a copy" when it's a pointer in Go)
- Reviewer claims a test is missing that already exists under a different name

If you can't confirm the bug after reading the relevant code, mark it `unverified` in the spec under "Findings rejected" with a one-line reason and skip it. **Do not fix what you cannot reproduce** — speculative fixes introduce new bugs without removing real ones.

### 3b. Fix minimally

The fix should be the smallest change that resolves the specific bug.
- No "while I'm here" cleanup.
- No drive-by refactors.
- No reorganizing imports unless the bug is import-related.

The PR has its own goal; preserve it.

### 3c. Add a test if the bug is testable

A bug that escaped review once will escape it again unless there's a test pinning the fixed behavior. New code paths from the PR especially need at least one test on the `true` branch of any flag they introduce.

If the bug is genuinely not unit-testable (e.g. a config typo that only manifests in deployed YAML), document that in the commit message: "Not unit-testable — manifests only in deployed Helm values."

### 3d. Run targeted verification

Run the smallest test scope that proves the fix. Then expand to the package level to catch regressions.

```bash
# Go
go test -tags=dynamic ./path/to/affected/package -run TestSpecific
go test -tags=dynamic ./path/to/affected/package          # package level

# Python
uv run pytest tests/affected/ -x -k specific_test
uv run pytest tests/affected/ -x                          # directory level

# TypeScript
npm test -- --testPathPattern=affected
```

Capture the exact command and result in the spec.

### 3e. Commit on the PR branch

Always confirm you're on the PR's branch before committing.

```bash
git status                           # confirm branch
git checkout <pr-branch>             # if not already
git add <specific files>             # never `git add -A` for fix commits
git commit -m "fix(<area>): <one-line summary>

<body explaining what was wrong and what changed>
<reference to review round if helpful>
"
git push
```

Conventional-commits style. One bug per commit when bugs are unrelated. Multiple commits in the same fix batch are fine.

### 3f. Update the spec

Append to the "Bugs fixed" section:
- Bug description
- Severity
- Commit SHA (full or short)
- Verification command + result

## Phase 4: Robustness sweep

A real bug usually has siblings. After fixing a confirmed finding, ask:

- Where else in the codebase does this pattern exist? (`grep` for the affected API, the variable name, the middleware function)
- If the bug was "this handler crashes on a missing field," do other handlers do the same thing?
- If the bug was "this migration locks the table," do other recent migrations have the same issue?
- If the bug was "this code re-evaluates a feature flag," does the rest of the call graph?
- If the bug was "wrong middleware order," do other middleware chains have the same ordering issue?

For each sibling found, decide:
- **Fix in this PR**: same root cause, small enough scope, doesn't expand the PR's mandate. Add to the same fix commit (if logically the same fix) or a sibling commit on the same PR branch.
- **File a follow-up**: different scope or different PR's responsibility. Note in the spec under "Remaining risks" with a one-line description; if the user has issue trackers, offer to file an issue.

Keep behavior consistent with the existing app patterns. Don't introduce a new error-handling style mid-PR.

## Phase 5: Loop

After Phase 3+4, the PR branch has new commits. Re-run Phase 2 against the updated head:

```bash
git fetch origin <branch>
NEW_HEAD=$(git rev-parse origin/<branch>)
```

Re-invoke the reviewer with the new SHA. Three outcomes:

| Result | Action |
|---|---|
| New real bugs found | Repeat Phases 3-5 |
| Findings, all unverified or NITs | Document in spec, mark PR as `ready-for-human` |
| No findings | Mark PR as `ready-for-human` |

Stop when ANY of:
- Reviewer reports no real bugs
- All remaining findings are documented as accepted non-blocking risks (with the user's explicit ack)
- Three consecutive rounds with no new actionable findings (diminishing returns — flag the PR as ready and surface the residue to the user)

## Final report

When the loop ends, post a single concise message to the user:

```
## Review-fix loop complete

### PR status
- PR #367 — ready-for-human (3 rounds, 4 fixes, head SHA: abc123)
- PR #380 — ready-for-human (1 round, 0 fixes)

### Fix commits pushed
- PR #367:
  - abc1234 fix(meter): handle nil collector in IngestEvents
  - def5678 fix(meter/auth): preserve namespace on customer-key path
- PR #380: (none)

### Verification commands run
- `go test -tags=dynamic ./openmeter/sink/...` → PASS
- `uv run pytest services/moolabs-app/bff/tests/unit/` → PASS

### Spec
docs/superpowers/reviews/2026-05-09-pr-367-pr-review-execution.md

### Merge status
NOT MERGED. Awaiting explicit "merge" instruction.
```

Even if the user gave merge permission earlier in the session, re-confirm before merging — review can change the picture and merge intent stated several turns ago is not the same as merge intent stated now.

## Common pitfalls

| Pitfall | What to do instead |
|---|---|
| Acting on every reviewer finding without verification | Reproduce or read the relevant file first; mark unverified findings as such in the spec |
| Bundling unrelated cleanup into a fix commit | One bug per commit; cleanup is a separate PR |
| Putting fixes on master or a new branch | Always `git checkout <pr-branch>` before fixing; `git status` to confirm |
| Skipping the spec doc because "I'll remember" | The spec is the human's audit trail. Without it, the user can't verify what you did. |
| Looping forever on minor findings | Three rounds with diminishing returns is the signal to stop. Document the residue and hand back. |
| Re-running review with a stale local HEAD | Always `git fetch origin <branch>` between rounds and use the fetched SHA |
| Treating reviewer output as gospel | False positives are common. Always read the code referenced before fixing. |
| `gh pr merge` because the user said "merge" two messages ago about a different PR | Re-confirm merge intent for THIS PR specifically, in the immediate prior turn |

## What "ready for human" means

A PR is ready for human review when:
- The adversarial review found no remaining real bugs (or only documented accepted residue)
- All confirmed bugs have fix commits with verification recorded in the spec
- The spec doc lists every round, every bug found, every fix, every rejected finding, every accepted residual risk
- The CI on the PR branch is green (or the failures are explicitly documented and unrelated to the fix scope)
- The user has been told the PR is ready and the merge decision has been handed back to them

Never make the merge decision yourself.
