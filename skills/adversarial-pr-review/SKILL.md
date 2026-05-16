---
name: adversarial-pr-review
description: MUST USE this skill — not a one-shot review — whenever the task involves quality-gating an open PR before merge. Adversarial review-fix loop that dispatches a hostile review via superpowers:requesting-code-review, verifies each finding against the actual code, fixes confirmed bugs minimally on the PR's own branch, sibling-searches for latent variants, and iterates until the review reports no real bugs. MUST fire on "adversarial review", "hostile review", "review until clean", "review and fix PR #X", "make sure PR #X is ready", "what else could break in PR #X", "are there hidden bugs in PR #X", "is PR #X actually merge-ready", "ship-block PR #X", or any framing that asks for latent-bug hunting. IDEMPOTENT — if a loop is already in progress for the same PR (audit trail spec or PR comment exists from this session), continue the existing loop instead of starting a new one; never run two parallel loops on the same PR. DOES NOT apply to ordinary CI debugging where the failing test already points at the bug — that's debugging, not adversarial review. Stack-agnostic — discovers languages, frameworks, test runners, migrations, codegen, config style, and auth surface from the repo itself. Never merges without explicit permission.
intent: |
  This skill is the difference between "tests pass on a PR" and "this PR is actually safe to merge." Tests prove the code does what its test cases say; adversarial review proves there is no obvious bug the test suite doesn't yet have a case for.

  It is stack-agnostic by design. Before reviewing anything it reads CLAUDE.md / AGENTS.md, top-level manifests, CI config, and conventions docs to derive the codebase profile (languages, test runners, migration tool, codegen, config style, auth surface, deployment shape). That profile drives the reviewer brief, the verification commands, and the risk categories — so the same skill works on a Go monorepo, a Python service, a TypeScript app, or a polyglot codebase without being recompiled for each.

  It enforces hard contracts: never merge without explicit permission, fix on the PR's own branch (not main), verify each reviewer finding before fixing (the reviewer hallucinates), keep per-PR loops independent, and produce an audit trail the human can inspect.

  It is NOT a general-purpose CI debugger. If the user is fixing actively-failing tests pointed at by CI, that's debugging — use the appropriate language-specific skill or just fix the bug. Use adversarial review when the question is "what bugs exist that we don't know about yet."
---

# Adversarial PR Review

The adversarial review loop is the difference between "tests pass on a PR" and "the PR is actually ready to merge." Tests prove compilation and basic correctness. Adversarial review proves robustness against the bugs the test suite doesn't yet have a case for.

This skill orchestrates that loop in any codebase. It does not replace `superpowers:requesting-code-review` — it stands on top of it, adding stack discovery, finding-verification, sibling search, and fix-iterate logic around each review pass.

## MUST-USE rule

<EXTREMELY-IMPORTANT>

If the task involves quality-gating an open PR before merge — and the boundary in "When this does NOT apply" doesn't exclude it — **you MUST invoke this skill.** Not a one-shot review, not a manual diff scan, not "I'll just look at the diff myself." This skill exists because manual reviews miss the bugs the test suite hasn't yet found a case for; bypassing it because the task "feels small" is exactly when latent bugs ship.

Apply this rule whenever any of the trigger framings below is plausible — even at 5% confidence. A wasted invocation costs one Phase 1 pass; a missed invocation costs a production bug.

**IDEMPOTENCY — do NOT re-fire if the loop is already running.** Before starting Phase 0, check:

- Is there already an audit trail (full spec doc OR PR comment) for this PR/batch from the current session?
- Are you already inside one of Phase 1-5 for this PR?

If yes to either, **continue the existing loop** — don't open a new spec, don't restart Phase 0, don't dispatch a second reviewer concurrently. The loop is one-at-a-time per PR. New round = Phase 5 → Phase 2 re-entry, not a fresh invocation.

| Rationalization | Reality |
|---|---|
| "The PR is tiny, manual scan is fine" | Phase 1.5 (stack discovery) is the only thing scaled to "tiny PR" — the rest of the loop adapts. Use compressed Phase 0; don't skip. |
| "I already eyeballed the diff" | Eyeballing isn't the verify-fix-sibling-search-harden cycle. Use this skill. |
| "The user just asked me to look quickly" | "Take a look" → use `superpowers:requesting-code-review` (one-shot). "Make sure it's ready" → use this skill (loop). Read the actual intent. |
| "I'll start without the spec, write it later" | Phase 0 is the audit trail. Without it the user can't verify your loop ran. Compressed format is the floor, not "skipped." |
| "The loop is already running; let me fire a second pass to be thorough" | Idempotency forbids it. One loop per PR. Re-enter the existing loop at Phase 5. |

</EXTREMELY-IMPORTANT>

## When this applies

Fire on any variant of:

- "review PR #X adversarially / hostilely"
- "review PRs #A, #B, #C until clean"
- "find bugs in this PR and fix them"
- "run a review-fix loop on this PR"
- "make sure PR #X is ready before merge"
- "ship-block PR #X on review"
- "what else could break in PR #X" / "are there hidden bugs in PR #X"
- "is PR #X actually merge-ready" / "I want to be sure before I merge PR #X"
- Any "quality gate this PR" framing where the user wants confidence beyond CI-green before merging

Use it for one PR or a list. The same loop applies to each PR independently (but each PR runs ONE loop at a time — see idempotency).

## When this does NOT apply

Adversarial review is heavyweight — Phase 0 spec, multi-round loops, sibling search. Don't fire it for tasks that aren't actually about hunting unknown bugs:

| Task | Use instead |
|---|---|
| "Fix the failing tests on PR #X" / "CI is red on PR #X, fix it" | Ordinary debugging — read the failure, fix the bug, push. The failing test already told you where the bug is. |
| "Take a look at PR #X" / "what do you think of this PR?" | `superpowers:requesting-code-review` directly — one-shot review, no loop. |
| "Reword the PR description" / "add a test for X" | Whatever skill matches the actual change. |
| "Rebase PR #X onto main" / "resolve merge conflicts" | Git operation, not a review. |

The boundary: **active bugs surfaced by CI / errors → debugging. Latent bugs the test suite hasn't reported → adversarial review.** The PR #395 SDK codegen failures were debugging (CI told you exactly which tests broke). Asking "what *other* hardcoded dates are time-bombs we haven't hit yet?" is adversarial — sibling search on the active bug.

## The contract — non-negotiable rules

1. **Never merge.** The user must explicitly say "merge it" or "go ahead and merge" in the immediate prior turn. Approval to fix is not approval to merge.
2. **Don't revert unrelated changes.** A bug fix is scoped to the specific bug. If the PR adds unrelated work, leave it alone — the user kept it for a reason.
3. **Fix on the PR's own branch.** Each fix lands on the branch the PR was opened against — not main, not a new branch, not a fork.
4. **Push fixes after each commit.** The PR remote branch is the source of truth that the next review round inspects.
5. **Treat review findings as candidates, not facts.** The reviewer is fast and broad, but it can hallucinate APIs, misread context, or be wrong about language semantics. Reproduce or verify each finding before fixing.
6. **Per-PR loops are independent.** Each PR runs its own review-fix loop. If one PR genuinely depends on another, document that explicitly in the spec rather than implicitly mixing them.
7. **Adapt verification to the stack.** Don't run Go test commands in a Node repo. Discover the test runner from the repo (Phase 1.5) and use it.
8. **Always produce an audit trail.** Either the full spec doc (Phase 0 full) or the compressed in-PR summary (Phase 0 compressed). Never zero documentation — the human needs to verify what you did.

## Loop overview

```
For each PR:
  Phase 0:    Create the audit trail (full spec OR compressed summary)
  Phase 1:    Understand the PR
    1a:        Read the diff mechanically
    1b:        Read the changed files in context (not just the diff)
    Phase 1.5: Detect stack and conventions (parallel; feeds 1c-1g)
    1c:        Articulate original intention vs new intention
    1d:        Design concrete success criteria (3-7 observable behaviors)
    1e:        Design codebase-specific challenges (3-5 architecture-specific scenarios)
    1f:        Two-round adversarial self-review of 1c/1d/1e outputs
    1g:        Risk map (informed by 1c-1e + 1f + Phase 1.5)
  Phase 2:    Adversarial review — two passes:
    Pass 1:  Verify the PR-specific contract (criteria + challenges)
    Pass 2:  Apply generic lenses
  Phase 3:    Verify and fix confirmed bugs
  Phase 4:    Robustness sweep (sibling search + defensive hardening)
  Phase 5:    Re-review; repeat 3-4 until all criteria PASS and reviewer is clean
Final:        Report status. Do not merge unless told.
```

## Phase 0: Create the audit trail

**Idempotency check first.** Before creating anything, look for an existing audit trail for this PR (or batch):

```bash
# Full-spec check — any spec in the standard location with this PR's number
ls docs/superpowers/reviews/*pr-<number>* 2>/dev/null
ls docs/superpowers/reviews/*<batch-short-name>* 2>/dev/null

# Compressed check — PR comment from a prior round of this loop
gh pr view <number> --comments --json comments \
  | jq -r '.comments[].body' | grep -l "Adversarial review — round" 2>/dev/null
```

If either turns up a result from the current session (or a recent prior session that this invocation is continuing), **continue updating the existing audit trail** — do NOT create a new spec or a new PR comment. Re-enter the loop at the appropriate phase (typically Phase 5 → Phase 2 for the next round).

Only create a new audit trail when you have confirmed none exists. Pick the format based on scope. Both formats record the same essential information; the difference is where it lives.

### Full spec (multi-PR, multi-round, or anything ≥ 2 rounds expected)

Create `docs/superpowers/reviews/YYYY-MM-DD-<short-name>-pr-review-execution.md`. If the directory doesn't exist, create it. Update after every phase. This is the human-readable audit trail; without it the user has no way to verify what you actually did.

```markdown
# Adversarial PR Review — <short-name>
Date: YYYY-MM-DD
Operator: <agent / model identifier>

## PRs in scope
| PR  | Branch       | Base    | Head SHA  | Status      |
|-----|--------------|---------|-----------|-------------|
| #N  | feat/foo     | main    | abc1234   | in-progress |

## Cross-PR dependencies
None  — OR  — PR #B depends on PR #A because <reason>.

## Codebase profile (filled in Phase 1.5)
- Languages and primary frameworks: …
- Test runners and how to invoke a scoped vs full run: …
- Migration / DDL tool: …
- Codegen tools and their outputs: …
- Config style (env vars / structured config / secrets manager): …
- Auth surface (custom middleware / framework-provided): …
- Deployment shape (single-tenant / multi-tenant / cloud+on-prem split): …
- Conventions documents found and consulted: …

## Per-PR detail

### PR #N — <one-line summary>
- Branch / Base / Head SHA at start
- Summary of changed areas
- Original intention (Phase 1c — what the system did before, and why)
- New intention (Phase 1c — what the system should do after, and why)
- Success criteria (Phase 1d — 3-7 concrete observable behaviors that must hold)
- Codebase-specific challenges (Phase 1e — 3-5 architecture-specific scenarios the PR must survive)
- Phase 1f self-review rounds: round 1 edits + round 2 edits (one line each per category) + suspicions deferred to Phase 2
- Risk map by subsystem (Phase 1g — informed by the now-refined criteria, challenges, and the codebase profile)
- Verification commands used (chosen from what the codebase profile reveals)
- Review rounds: 1, 2, 3 with finding counts (split by Pass 1 / Pass 2)
- Success criteria verification per round: each criterion → PASS | FAIL | not-yet-verified
- Challenge verification per round: each challenge → handled | unhandled | accepted-residue
- Operator spot-check per round: which criterion or challenge you personally verified by reading the code (not just trusting the reviewer), and the file/line you read
- Bugs fixed: description + severity + criterion/challenge it relates to + commit SHA + verification result
- Findings rejected (false positives) with reason
- Defensive hardening applied: list each (where, what, why)
- Remaining risks (accepted non-blocking) with user ack reference
- Status: in-progress | ready-for-human | blocked

## Final summary
- PR #N: <status>, fixes: <commit SHA list>
- Merge status: NOT MERGED — awaiting explicit user permission.
```

### Compressed summary (single PR, single round expected, solo operator)

Skip the standalone doc. Instead post one compact summary as a PR comment (`gh pr comment <N> --body @-`) and reference it in the final report. The format:

```
## Adversarial review — round 1
- Codebase profile: <one line — stack, test runner, migration tool>
- Original → new intention: <one line each — what the system did vs what it should do>
- Success criteria: 1. <…>  2. <…>  3. <…>   (mark each PASS / FAIL after the round)
- Codebase-specific challenges: 1. <…>  2. <…>  3. <…>  (mark each handled / unhandled)
- Phase 1f self-review: r1 edits <intention X / criterion Y added / challenge Z sharpened>; r2 edits <…>
- Risk map: <2-3 bullets, file-specific>
- Review rounds: 1, finding count: N (Pass 1: X, Pass 2: Y)
- Bugs fixed: <SHA short — one-line fix description (cite criterion/challenge if applicable)>
- Findings rejected: <one-line — reason>
- Operator spot-check: <criterion or challenge you personally verified against the code this round>
- Status: ready-for-human | needs-round-2
- Merge status: NOT MERGED.
```

The compressed format is **terser, not fewer-things-tracked**. Every field from the full spec is still represented, just collapsed to one line where possible. If round 2 happens, upgrade to the full spec — multi-round work needs the longer audit trail.

The contract is: **the human can always reconstruct what you did from text you wrote.** No silent loops.

## Phase 1: Understand the PR

Don't review what you don't understand. A reviewer that hasn't built a mental model of the changed code will only catch surface bugs — typos, missing nil checks, obvious lint issues. The deeper bugs (silent behavioral regressions, intention drift, broken invariants) require understanding what the system *did* before and what it's supposed to *do* after.

Phase 1 has seven sub-steps. Do 1a and 1b first (mechanical diff + reading changed files), then run **Phase 1.5 (Detect stack and conventions)** in parallel before continuing — Phase 1.5's outputs (codebase profile, architectural choices) feed 1c-1g. Then write the creative outputs (1c intentions, 1d criteria, 1e challenges), self-review them in two rounds (1f), and finally build the risk map (1g) from the now-refined inputs. Don't skip ahead to the reviewer.

### 1a. Read the diff mechanically

```bash
gh pr checkout <number>
git fetch origin <base-branch>
git log --oneline origin/<base-branch>..HEAD
git diff origin/<base-branch>...HEAD --stat
git diff origin/<base-branch>...HEAD       # full diff
gh pr view <number> --json title,body,additions,deletions,changedFiles
```

This tells you *what* changed. It does not tell you what the changed code does or means.

### 1b. Read the changed files in context (not just the diff)

For each changed file, read the **whole file** (or the relevant function/module). The diff alone shows you the changed lines, but bugs live in the interaction between changed lines and unchanged surrounding code:

- A new branch added inside an existing function — is it reachable? Does it return the same type the caller expects?
- A removed line — what was that line protecting against? Is the protection now missing?
- A renamed symbol — does every caller use the new name? Did the diff miss any callsites?
- A new parameter — do all callers pass it? What's the default for callers the diff didn't touch?

Also read the **call graph around the changed code** — at least one level out. If the PR changes a function, read the functions that call it and the functions it calls. Bugs introduced by a change often surface one or two hops away.

For larger PRs, prioritize: changed files that touch boundaries (API handlers, public exports, schema, migrations, auth) first; pure-internal changes second.

### 1c. Articulate the original intention and the new intention

This is the step most often skipped, and the one that catches the deepest bugs. For each PR (or for each cohesive change within a multi-purpose PR), write two short statements:

- **Original intention** — what was the system doing here before this PR, and why? What contract did the code hold with its callers and with the rest of the system?
- **New intention** — what should the system be doing here after this PR, and why? What contract does the code now hold?

These are not the same as the PR title. The PR title is what the *author* said they were doing; the intentions are what the *code* was actually doing and is now actually doing. Divergence between the two is itself a finding.

Sources for the original intention: the unchanged code's existing structure, its docstrings, the function names, the tests that exercise it, and any conventions docs that describe the subsystem. Sources for the new intention: the PR description, the commit messages, the new tests added (a new test is a contract assertion), and the changed code itself.

Write both intentions into the audit trail. Example:

> **PR #367 — namespace middleware**
> Original intention: HTTP middleware extracted `OM-Namespace` from the request header on every route. Missing header was treated as "default namespace" silently. Contract: every downstream handler receives `ctx.namespace` populated, never nil.
> New intention: extract `OM-Namespace` only on namespaced routes; for non-namespaced routes (health, metrics, OAuth callback) skip extraction. Missing header on a namespaced route now returns 400. Contract: handlers on non-namespaced routes must not assume `ctx.namespace` is present.

Now the bug categories are sharper:

- *Drift between PR description and the code's behavior* — author says one thing, diff does another.
- *Drift between old and new contract* — a downstream handler that always read `ctx.namespace` will now panic on non-namespaced routes if the new intention isn't fully implemented.
- *Tests that pin the wrong intention* — a test exercising the old contract that wasn't updated.

### 1d. Design concrete success criteria

Before invoking the reviewer, write down **what observable behaviors must hold for this PR to be correct**. These are positive correctness statements, not bug categories. They become the verification targets the reviewer checks against — and the exit-gate for Phase 5 (the loop stops when all success criteria pass, not just when the reviewer goes quiet).

Good success criteria are:

- **Concrete** — phrased as something you can test or observe, not a vague property. *"Customers continue to receive correct invoices"* is too vague. *"Existing invoices created before this PR still render with the same line-item totals"* is testable.
- **Tied to the intention contrast from 1c** — each success criterion follows from either the original contract (must still hold) or the new contract (must now hold).
- **Owned by the PR** — don't add criteria the PR isn't trying to satisfy. ("PR doesn't introduce new memory leaks" is reasonable; "PR fixes the unrelated session-handling bug" is scope creep.)
- **Failable** — there's a specific observation that would tell you the criterion is violated.

Write 3-7 criteria per PR. Fewer than 3 means the PR is either trivial or you haven't thought about it. More than 7 means the PR is doing too many things and should probably be split. Example (continuing PR #367):

```
Success criteria for PR #367:
1. All existing namespaced routes still extract OM-Namespace and pass it to handlers.
2. Health, metrics, and OAuth callback routes do NOT attempt to extract OM-Namespace
   and remain reachable without any auth headers.
3. A namespaced route receiving a request without OM-Namespace returns 400 with a
   structured error body (not a 500 panic).
4. The non-namespaced-route allowlist is explicit (a constant or config), not
   inferred from path prefixes — so the set of "exempt" routes is auditable.
5. Existing tests for namespaced routes continue to pass without modification.
6. New tests cover: (a) a non-namespaced route with no header succeeds, (b) a
   namespaced route with no header returns 400.
```

### 1e. Design codebase-specific challenges

Generic bug categories ("what if there's a nil pointer", "what if the JSON is malformed") catch generic bugs. The bugs that actually escape to production are the ones that hide in the seams of the codebase's specific design — concurrency model, deployment topology, transaction boundaries, eventing patterns, multi-tenancy approach, caching strategy.

Use the architecture you've learned from Phase 1.5 (codebase profile) and Phase 1b (reading the code) to design **concrete adversarial scenarios** — specific, codebase-tailored stress tests that the PR's code must survive. Each challenge is "given this codebase's design choice X, and the PR's change Y, here is a specific scenario that could break, with the observable failure mode."

Write 3-5 challenges per PR. They feed into the reviewer brief in Phase 2 as explicit verification targets.

**How to derive them:** look at each architectural property the codebase has chosen, and ask "what scenario does this property make possible that the PR's code must handle?"

| Codebase design property | Example challenge |
|---|---|
| **Multi-tenant via row-level scoping** | Tenant A and tenant B issue concurrent requests through the new PR's code path. Does the tenant scope leak — does any query the PR added omit the tenant predicate? |
| **Async via event bus → consumer (EventBridge / SQS / Kafka)** | The producer succeeds but the consumer fails after partial processing and retries. Does the PR's handler produce duplicate state? Does it leave the partial state visible to readers? |
| **Saga / compensating transactions** | Step 3 of a 5-step saga fails after this PR's step 2 changes. Do the compensation handlers cover the new state the PR introduced? |
| **Optimistic locking / row versions** | Two requests update the same row through the PR's new write path simultaneously. Does the retry path handle the version-conflict error, or does it surface as a 500? |
| **Cache-aside (cache then DB on miss)** | The PR changes the underlying entity. Is every cache key that could hold this entity invalidated? Is there a TTL gap where readers see stale data? |
| **Cloud + on-prem dual deploy** | The PR uses a feature only available in the cloud build (e.g. a managed identity, a hosted secret store). What does the on-prem build do at runtime — fail at startup, fail at first call, or silently behave wrong? |
| **Read replica with replication lag** | The PR writes, then immediately reads. Does it read from the primary or from a replica? If replica, what's the behavior during lag? |
| **Feature-flag gated rollout** | Two paths exist (flag-on / flag-off). The PR adds code on one path. Does the other path still produce identical output for unaffected users? Does the flag evaluation happen at the right granularity (per-request, per-tenant, per-process)? |
| **At-least-once event delivery** | The same event is delivered twice through the PR's new consumer. Does the consumer's logic produce the same end-state both times (idempotent), or does it double-bill / double-write? |
| **Schema migration applied online** | The PR runs DDL that takes a lock. While the DDL is running, an existing query path is still serving traffic. Does the existing query path break (column missing, wrong type) during the migration window? |
| **Single-writer / leader-elected component** | The PR adds code to the leader path. What happens during a leader handoff — does the new state survive? Does the new follower see the in-flight work? |
| **Long-poll / streaming connection** | The PR changes a response shape. Active streaming connections established before the deploy — do they break, or do they continue with the old shape? |
| **Background reconciler / sweeper** | A reconciler sweeps state every N seconds. The PR changes the data it reconciles. Does the reconciler see the new shape correctly, or does it "fix" the new shape back to the old one? |
| **External webhook delivery** | The PR changes outbound webhook payload. Downstream consumers expect the old shape. Is there a versioning header? Is there a migration path? |

These are starting prompts — the real challenges come from reading *this* codebase. After Phase 1.5 gives you the profile and 1b gives you the code understanding, the question is "for THIS architecture and THIS change, what specific scenario could break?"

Write the challenges into the audit trail. Example:

```
Codebase-specific challenges for PR #367 (namespace middleware):
1. Multi-tenant routing: the namespace from OM-Namespace is used downstream to
   scope DB queries. If the new code path on non-namespaced routes leaves
   ctx.namespace empty, does any downstream handler still try to use it as a
   query scope and accidentally return cross-tenant data?
2. Health check during deploy: the kubelet hits /healthz every 5s. If the new
   allowlist for non-namespaced routes is loaded from config, what happens
   during config reload — is there a window where /healthz becomes namespaced?
3. Pre-existing OAuth callback: the callback route was previously namespaced
   (incorrectly). External identity providers cached the old URL. Does the new
   allowlist preserve the public reachability of the cached URL?
4. Middleware order: the namespace middleware now runs conditionally. Does the
   downstream rate-limit middleware (which reads ctx.namespace) still work for
   non-namespaced routes, or does it crash on empty namespace?
```

These challenges become explicit lenses in the Phase 2 reviewer brief — the reviewer is told to specifically verify each one against the diff.

### 1f. Two-round adversarial self-review of 1c, 1d, 1e

The outputs of 1c (intentions), 1d (success criteria), and 1e (challenges) drive **everything downstream** — Phase 2's reviewer brief, Phase 5's exit gate, even Phase 4a's sibling search. If any of these three are sloppy, the entire loop is misdirected: the reviewer hunts for the wrong bugs, the exit gate validates the wrong contract, sibling search misses the real pattern.

The fix is the same one Phase 5 applies to the main review: **don't trust the first pass; iterate twice.** Self-review your 1c/1d/1e outputs before locking them in and invoking the external reviewer.

**How this differs from Phase 5's operator spot-check:** Phase 1f asks "are these criteria/challenges themselves any good?" (criterion *quality* — are they sharp, complete, codebase-specific). Phase 5's spot-check asks "did the reviewer correctly verify these criteria against the diff?" (reviewer *hallucination* — did it return a false PASS). Same operator, different questions, both required. 1f happens once (two rounds) per PR, before Phase 2. The spot-check happens every round after Phase 2.

**Why exactly two rounds:**

- **Round 1** catches obvious slop — vague phrasing, missing coverage, scope creep, generic-sounding challenges. These are the issues the operator can see on a deliberate re-read.
- **Round 2** catches what the round-1 fixes themselves introduced or missed. The operator is now thinking about the criteria/challenges fresh, with the round-1 edits as anchor — and notices things the first pass was too close to see.
- **Why not three?** Same diminishing-returns logic Phase 5 uses on the main loop. Past round 2, new findings get rare and the marginal review cost dominates. Let Phase 2's external reviewer catch the rest.
- **Why not one?** One round leaves blind spots the operator didn't have distance from. Round 2 is where you'd catch "wait, I said three criteria but they're all positive-path — I missed the error contract entirely."

Stop after round 2 even if you suspect more — log the suspicion in the audit trail and proceed to Phase 2.

#### How to run a round

Treat your own 1c/1d/1e outputs as a finding-source. Walk each item against the checklist below; where a check fails, **edit the original 1c/1d/1e output in place** (don't add "addendum" sections — the audit trail should show the refined version, with the round's edits summarized below).

##### Self-review checklist for intentions (1c)

- [ ] Does the stated **new intention** match what the diff actually does? Re-read both. If the intention says "extract X from header" but the diff also adds logging, retries, or a metric, the intention is incomplete.
- [ ] Does the stated **original intention** match what the code did BEFORE this PR? Check the pre-diff version via `git show <base>:<file>`. If the original is wrong, Phase 2's contract-drift comparison will miss the real change.
- [ ] Is the **contract change explicit**? It should read like "used to do X with property P; now does Y with property Q." If the diff has multiple unrelated changes, write multiple intention pairs — one per cohesive change.
- [ ] Are there **hidden intentions** the PR description didn't mention? Renames, default-value changes, removed conditionals, refactors-in-disguise. Silent contract changes are the most dangerous kind — add them if found.

##### Self-review checklist for success criteria (1d)

- [ ] Each criterion: **concrete, observable, failable, intention-tied?** Rewrite any vague ones. "X works correctly" is not a criterion; "the response shape matches the OpenAPI spec for tenant-scoped routes" is.
- [ ] **Coverage of both contracts**: at least one criterion confirms "old contract still holds for unchanged callers" AND at least one confirms "new contract behaves as specified"? Missing either side is a common pattern.
- [ ] **Boundary coverage**: at least one criterion covers each of (error path / missing input / empty collection / boundary value / concurrent access) that's relevant to this PR? Add for any relevant gap.
- [ ] **Test-gap honesty**: for each criterion, does a test today prove it? If not — is one planned in this PR, or are you accepting it as not-testable? Mark explicitly; "not-testable" should be rare and justified.
- [ ] **Scope discipline**: any criterion exceeds the PR's mandate? Strip it — those are follow-ups, not gates.
- [ ] **Volume sanity**: 3-7 criteria. Fewer than 3 → you've missed something; more than 7 → the PR is doing too many things, consider proposing a split.

##### Self-review checklist for challenges (1e)

- [ ] Each challenge: **tied to a specific architectural property of THIS codebase**, named explicitly? "What if input is null" is generic; "given the multi-tenant row-level scoping, does the new write path's `INSERT ... ON CONFLICT` clause still scope by tenant_id" is codebase-specific.
- [ ] Each challenge: **includes the observable failure mode**, not just "what if X"? "What if the consumer fails after partial processing" is incomplete; "...does the partial state stay visible to readers via the read replica until the retry succeeds?" is complete.
- [ ] **Coverage breadth**: do challenges cover at least three of (concurrency, deployment topology, data flow, error paths, lifecycle/upgrade)? If all challenges are about one architectural axis, you're missing seams.
- [ ] **Substitution test**: would any challenge make equal sense in a totally different codebase? If yes, sharpen it with a property name from THIS codebase's design.
- [ ] **Volume sanity**: 3-5 challenges. 1-2 → you haven't engaged with the architecture; 6+ → you're likely re-stating generic lenses (Phase 2 Pass 2's job, not this one's).

#### Record the self-review

After each round, append a one-line note to the audit trail. Show what changed; don't re-paste the full criteria/challenges (the now-refined version IS the criteria/challenges — the round notes are the diff).

```
## Phase 1f self-review — round 1
- Intentions: 2 edits (added rate-limit reset to new intention; corrected
  pre-diff behavior of OAuth callback)
- Success criteria: 1 added (boundary case for missing OM-Namespace on
  non-namespaced route), 1 rewritten for testability
- Challenges: 2 sharpened (multi-tenant scoping, schema-migration window)
- Re-read of diff after edits: confirmed alignment.

## Phase 1f self-review — round 2
- Intentions: no edits.
- Success criteria: 1 added (response-shape stability for streaming
  connections — caught by re-reading 1e's streaming challenge)
- Challenges: 1 added (background reconciler — discovered while checking
  the new criterion)
- Re-read of diff after edits: confirmed alignment.
- Suspicions deferred to Phase 2: <any further nagging concerns — log them
  for the external reviewer to validate>
```

Two outcomes, two follow-ups:

- **Round 2 found zero edits across all three categories.** Proceed to Phase 2 as normal — your 1c/1d/1e set is stable.
- **Round 2 found substantial new items** (more than 1 edit in any category). Proceed to Phase 2 anyway (no round 3), AND record this in two places:
  - audit trail: log the round-2 finding count
  - Phase 2 reviewer brief: add a one-line note — "Self-review was still finding issues at round 2 — apply extra scrutiny on Pass 1." This tells the external reviewer that the PR-specific contract may still have gaps the operator didn't catch, so it should hunt harder for contract drift and criterion violations.

Either way, don't run round 3 — past round 2, new findings get rare and the marginal review cost dominates. Let Phase 2 catch the rest.

### 1g. Risk map

Build the risk map AFTER 1c-1e are self-reviewed (1f) — the risks are mostly "ways success criterion N could be violated" or "scenarios from the challenge list that the diff doesn't handle." Building the risk map from unreviewed inputs amplifies any errors in them, which is why 1g comes last. Write each subsystem and its specific risks into the spec under "Risk map by subsystem." Don't generalize — name the function or file.

## Phase 1.5: Detect stack and conventions

Conceptually this is a sub-step of Phase 1 that runs between 1b and 1c — kept as its own top-level section because it's substantial and reusable across PRs in the same repo. Phase 1.5's outputs (stack, test commands, conventions, architectural choices) feed Phase 1c-1g and Phase 2's reviewer brief. Read these in parallel and pull what you find into the spec's "Codebase profile" section:

### What to look at

| Source | What it tells you |
|---|---|
| `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursor/rules/`, `.windsurf/rules/`, `CONTRIBUTING.md` | Team-codified conventions, style rules, "don't do X" warnings |
| `README.md` (sections on testing, building, running) | The intended developer loop |
| Top-level manifests: `go.mod`, `package.json`, `pyproject.toml`, `Cargo.toml`, `pom.xml`, `build.gradle*`, `Gemfile`, `composer.json`, `mix.exs` | Languages and major frameworks |
| Build/run scripts: `Makefile`, `justfile`, `Taskfile.yml`, `package.json` scripts, `scripts/`, `bin/` | The exact commands the team uses |
| CI: `.github/workflows/`, `.gitlab-ci.yml`, `circle.yml`, `.azure-pipelines.yml`, `buildkite.yml` | The "ground truth" for what must pass — and the CI environment's quirks (caches, dep installation paths, env vars set only in CI) |
| Test runner config: `vitest.config.*`, `jest.config.*`, `pytest.ini`, `pyproject.toml [tool.pytest]`, `tox.ini`, `karma.conf.*`, `phpunit.xml`, `Cargo.toml [profile.test]` | How to invoke a scoped vs full test |
| Migration tooling: `migrations/`, `db/migrate/`, `alembic.ini`, `atlas.hcl`, `flyway.conf`, `prisma/schema.prisma`, `ent/schema/`, `liquibase.xml` | DDL drift / backfill risks. Also: any migration **preflight scripts** that branch on heuristics — those silently skip DDL when the heuristic is wrong (anti-pattern from real incidents). |
| Codegen artifacts in the diff: `*_gen.go`, `wire_gen.go`, `*.gen.ts`, `openapi.yaml` + generated clients, protobuf-generated files, GraphQL codegen output | Files that must be regenerated, not edited |
| Config style: `viper`, `koanf`, `cobra`, `pydantic-settings`, `dotenv`, `helm/values*.yaml`, `terraform/*.tfvars`, `12-factor` env-var-only | Where defaults live and which kind of default is dangerous |
| Auth surface: middleware files, `auth/`, `security/`, `*Guard*`, `*Authentication*`, `oauth*` files | What the auth flow looks like and where order matters |
| Health/observability: `/healthz`, `/livez`, `/readyz`, structured logging setup, metrics endpoint | What "still working" means at runtime |

### Risk categories to look for (refine for this codebase)

The reviewer should look for bugs in these categories. Drop any that don't apply to this repo; add any that the conventions docs warn about.

**Standard categories:**

- **Migrations / schema changes** — DDL, NOT NULL on existing rows, dropped columns, type narrowing, missing rollback.
- **Runtime config changes** — defaults that activate new code paths, env-var typos, secrets handling, feature flag wiring.
- **Routes / API changes** — new endpoints, changed auth, removed routes, changed response shapes that customers depend on.
- **Build / dependency changes** — lockfile drift, pinning issues, transitive vulns, version skew between dev / CI / prod.
- **Generated code** — re-running the generator with a different version produces different output and breaks builds. Verify diffs match what the generator would produce.
- **Test coverage** — flag-gated code paths with only `false`-branch tests, error returns never asserted, integration paths only covered by mocked unit tests.
- **Concurrency / shared state** — middleware ordering, goroutine / async-task spawns, mutex / lock acquisition order.
- **Cross-deployment behavior** — code that branches on cloud-vs-on-prem, flag-on-vs-flag-off, single-node-vs-cluster.
- **Auth surface** — middleware ordering relative to body parsing, header extraction, token validation. A pre-auth middleware that 400s on a missing header breaks `/healthz` too.

**Latent-bug categories (the categories that catch what the failing tests don't):**

- **Time-bomb data** — hardcoded dates, hardcoded epochs, hardcoded SHAs / commit hashes, hardcoded version strings, hardcoded year/quarter strings that will silently age out. `grep` for absolute dates in the diff and in adjacent test files. *Real example:* the PTP test suite hardcoded a "future" date that aged into the past, breaking 3 active tests and leaving ~5 latent siblings unflagged until they too aged out. Sibling search across the test directory caught them all at once.
- **CI-vs-local divergence** — the bug only manifests under CI's execution context, not locally:
  - `cache-dependency-glob` paths that don't resolve in the CI workspace
  - dependencies installed locally via the dev environment but missing from CI install steps
  - environment variables set in the dev shell but absent from the CI workflow `env` block
  - working-directory assumptions that differ between dev and CI
  - tools available locally (`make`, `just`, `gh`) that the CI image doesn't have
  - codegen artifacts present locally but the CI doesn't regenerate them
- **Silent-skip patterns** — preflight scripts or branch heuristics that skip work when a sentinel is "already done":
  - migration preflights that stamp version forward and skip DDL
  - cache rebuild scripts that exit-0 on a stale cache hit
  - "if file exists, skip" guards that should be "if file is current, skip"
  - These break silently — there's no error, just incorrect state.
- **Pitfalls the team has documented** — quote them. If `CLAUDE.md` says "never use raw SQL, always use the repository pattern," that's a finding category. Treat documented anti-patterns as automatic findings when you see them in the diff.

### What to derive

From what you find, fill these in concretely (no placeholders) in the spec:

- **Test commands** — the exact invocation for: scoped run on a specific test, package-level run, repo-wide run, with-coverage CI run.
- **Risk categories that are real for this repo** — drop any of the canonical ones that don't apply, and add any project-specific ones the conventions docs warn about.
- **The "ready-to-merge" gate** — is there a coverage threshold? A required CI job? A formatter that must pass? Anything in CI is in scope for "did this PR break it."

If the conventions documents themselves contradict the codebase ("CLAUDE.md says X but the code does Y"), surface that as a finding — it usually means the docs are stale and someone is going to be misled by them.

### Why this matters

A reviewer that doesn't know what `wire_gen.go` is will treat its diff as meaningful and propose nonsense. A reviewer that knows it's generated will check whether the regeneration was triggered correctly. The detection step keeps the loop's signal-to-noise ratio high.

## Phase 2: Adversarial review

Invoke `superpowers:requesting-code-review`. Brief the reviewer adversarially using the codebase profile from Phase 1.5 — make it look for bugs, not style. The brief is a **starting set of lenses**, not a closed list — the reviewer should add categories suggested by the codebase profile.

```
Adversarially review the diff between <BASE_SHA> and <HEAD_SHA> on branch
<branch>. Context: <one-paragraph what the PR does and why>.

Original intention (from Phase 1c — what the code did before):
<paste the original-intention paragraph>

New intention (from Phase 1c — what the code is supposed to do after):
<paste the new-intention paragraph>

Success criteria (from Phase 1d — observable behaviors that must hold):
1. <criterion 1, file/function-specific>
2. <criterion 2>
3. <criterion 3>
...

Codebase-specific challenges (from Phase 1e — scenarios this codebase's design
makes possible that the PR must survive):
1. <challenge 1, with the specific failure mode>
2. <challenge 2>
3. <challenge 3>
...

Codebase profile (from Phase 1.5):
- Stack: <languages, frameworks>
- Migration tool: <name + how DDL changes flow>
- Codegen tools: <list>
- Config style: <env-var / structured / secrets manager>
- Auth surface: <middleware order, header parsing>
- Cross-deployment: <if any>
- Documented anti-patterns from CLAUDE.md / CONTRIBUTING.md: <list>
- CI quirks (env vars only set in CI, install steps that differ from dev): <list>

Operator signal (include ONLY if Phase 1f round 2 still found substantial edits —
otherwise omit):
- "Self-review was still finding issues at round 2 — apply extra scrutiny on
   Pass 1. The PR-specific contract may still have gaps the operator didn't
   catch; hunt aggressively for contract drift and unmet criteria."

Two passes — do both, do not skip pass 2:

Pass 1 — VERIFY THE PR-SPECIFIC CONTRACT.
For each success criterion: does the diff achieve it? Find concrete evidence
in the code, or report it as unmet.
For each codebase-specific challenge: trace through the diff and verify the
PR's code survives the scenario, or report it as unhandled.
Flag any drift between the stated new intention and what the code actually
does — that is itself a finding.

Pass 2 — GENERIC LENSES.
Default lenses (use these AND any others the codebase profile suggests):
1. Correctness bugs — wrong logic, off-by-one, race conditions, nil/null deref
2. Crash / panic paths — unchecked errors, type-cast failures, unwraps on
   optionals
3. Migration / schema issues — destructive DDL, missing rollback, NOT NULL on
   existing rows, drift between migration and ORM
4. Dependency / runtime failures — broken imports, version skew, generated
   code out of sync with its source
5. Broken routes / API behavior — missing auth, wrong middleware order, schema
   mismatch with handler signatures, removed-but-public endpoints
6. Bad assumptions — feature-flag re-evaluation, TOCTOU races, "this can't
   happen", silent fallbacks, preflight scripts that silently skip work
7. Security footguns — secrets in logs, SSRF, SQL/NoSQL injection, missing
   CSRF, permissive CORS, hardcoded credentials, plaintext tokens
8. Missing test coverage — code paths only existing on the `true` branch of a
   flag with no test, error returns never asserted, integration paths only
   covered by mocked unit tests
9. Latent / time-bomb bugs — hardcoded absolute dates, hardcoded SHAs /
   version strings, "if file exists skip" guards on stale state,
   sentinel-based skip patterns
10. CI-vs-local divergence — cache globs, missing CI install steps, env vars
    only set in the dev shell, codegen artifacts not regenerated in CI
11. Documented anti-patterns from above — flag any occurrence in the diff

Report each finding as: severity (CRITICAL / IMPORTANT / MINOR / NIT),
file:line, what the bug is, why it's wrong, and a suggested minimal fix.
For findings from Pass 1, cite which success criterion or challenge the
finding violates. Skip style nits and pure formatting. If the codebase
profile suggests additional risk categories beyond these defaults, apply
them — this list is a floor, not a ceiling.
```

Save the reviewer's output. Update the spec with the round number and the finding count.

## Phase 3: Verify and fix confirmed bugs

For each reviewer finding, work through this sequence — don't move to the next bug until this one is resolved one way or the other:

### 3a. Reproduce or verify

The reviewer can be wrong. Read the file. Is the bug real? Common false positives:

- Reviewer hallucinates an API that doesn't exist in this codebase
- Reviewer misreads the diff (flags the OLD code thinking it's new, or vice versa)
- Reviewer misses context from related files (claims a value is unchecked, but a caller already validated it)
- Reviewer is wrong about language semantics (e.g. "this is a copy" when it's a reference)
- Reviewer claims a test is missing that already exists under a different name

If you can't confirm the bug after reading the relevant code, mark it `unverified` in the spec under "Findings rejected" with a one-line reason and skip it. **Do not fix what you cannot reproduce** — speculative fixes introduce new bugs without removing real ones.

### 3b. Fix minimally

The fix is the smallest change that resolves the specific bug.

- No "while I'm here" cleanup
- No drive-by refactors
- No reorganizing imports unless the bug is import-related

The PR has its own goal; preserve it.

### 3c. Add a test if the bug is testable

A bug that escaped review once will escape it again unless there's a test pinning the fixed behavior. New code paths from the PR especially need at least one test on the `true` branch of any flag they introduce.

If the bug is genuinely not unit-testable (e.g. a config typo that only manifests in deployed values, or a CI-only env divergence), document that in the commit message: "Not unit-testable — manifests only in deployed Helm values" / "Not unit-testable — only fails in CI environment, fixed by CI workflow change."

### 3d. Run targeted verification

Use the test commands you discovered in Phase 1.5 — not generic examples. Capture the exact invocation and result in the spec.

| Scope | Purpose | When to run |
|---|---|---|
| Scoped (one test / one case) | Prove the specific fix works | First, after every fix |
| Package / module level | Catch regressions in the touched area | After each fix, before pushing |
| Repo-wide | Catch cross-package regressions | Once per round, before final push |
| CI-equivalent (if reproducible locally) | Catch CI-only divergence | When the bug class is CI-vs-local — actually invoke the CI command path, not just the local equivalent |

If the test runner differs across the repo (polyglot monorepo), use the runner appropriate to the package the fix touches. If multiple packages are touched, run each.

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

Conventional-commits style. One bug per commit when bugs are unrelated. Multiple commits in the same fix batch are fine when they share a root cause.

### 3f. Update the audit trail

Append to the "Bugs fixed" section (full spec) or extend the PR comment (compressed):

- Bug description
- Severity
- Commit SHA (full or short)
- Verification command + result

## Phase 4: Robustness sweep

A real bug usually has siblings, and a real bug usually points to a missing defensive guard. Run both passes — sibling search is retrospective, defensive hardening is prospective. Together they catch the next round of findings *before* the reviewer does.

### 4a. Sibling search

A real bug usually has siblings somewhere else in the same codebase. After fixing a confirmed finding, ask:

- Where else does this pattern exist? (`grep` for the affected API, the variable name, the middleware function, the unchecked operation, the hardcoded literal)
- If the bug was "this handler crashes on a missing field," do other handlers do the same thing?
- If the bug was "this migration locks the table," do other recent migrations have the same issue?
- If the bug was "this test hardcodes a date that aged out," scan the whole test directory for other absolute dates — they have the same shelf life.
- If the bug was "this code re-evaluates a feature flag," does the rest of the call graph?
- If the bug was "wrong middleware order," do other middleware chains have the same ordering issue?
- If the bug was "this CI step's cache glob doesn't match," do other CI jobs share that pattern?

For each sibling found, decide:

- **Fix in this PR**: same root cause, small enough scope, doesn't expand the PR's mandate. Add to the same fix commit (if logically the same fix) or a sibling commit on the same PR branch.
- **File a follow-up**: different scope or different PR's responsibility. Note in the spec under "Remaining risks" with a one-line description; offer to file an issue if the user has an issue tracker.

**Multi-PR shared context:** if the user gave you a batch of PRs and you confirmed a bug in PR #A, scan PRs #B / #C for the same pattern too — related PRs often share authoring context and the same anti-pattern. Document any cross-PR sibling findings in both PRs' sections of the spec.

### 4b. Defensive hardening

Independent of any specific bug, ask: could a single bad input here crash the whole app? Look for these failure modes around the changed surface area:

| Failure mode | Hardening pattern |
|---|---|
| **Bad upstream response** (third-party API returns 500 / unexpected JSON) | Wrap the call; fall back or degrade gracefully; never let one upstream failure cascade |
| **Unhandled exception** in a request handler | Ensure the framework's error boundary catches it and returns a structured 5xx instead of bringing down the worker |
| **Malformed payload** (parser succeeds but values are nonsensical) | Validate at the boundary; reject early with a 400; don't trust types |
| **Missing optional field** | Treat absence as the documented default; never assume `obj.field.subfield` chains |
| **Route-level failure** (one slow / broken route blocks others) | Per-route timeouts; circuit breakers if a downstream is involved; isolate worker pools where the framework allows |
| **Background job failure** | Retry with backoff; dead-letter queue or audit log so silent failure becomes loud |
| **Resource exhaustion** (memory, file handles, DB connections) | Bounded pools and queues; cancel-on-disconnect; backpressure |
| **Silent skip / preflight bypass** | Make skip-paths log loudly; assert post-conditions; fail-closed when sentinel state is ambiguous |

Apply hardening *consistently with existing app patterns*. If the codebase wraps every handler in `try/catch` and emits structured errors, do that. Don't introduce a new error-handling style mid-PR — that itself is a review-worthy inconsistency.

If the hardening would expand the PR's mandate beyond what the user signed up for, file a follow-up instead. The bar is: the change keeps the PR's existing behavior correct under failure, not "the change adds new resilience capability."

Commit and push hardening fixes to the same PR branch with a clear message: `fix(<area>): handle <specific failure mode>`.

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

Stop when ALL of:

- Every success criterion from Phase 1d has been verified PASS (the reviewer found concrete evidence in the diff, or you confirmed it directly)
- Every codebase-specific challenge from Phase 1e has been verified handled (the diff demonstrably survives the scenario, or the gap is documented as accepted risk with user ack)
- Reviewer reports no real bugs (Pass 1 or Pass 2)
- **Operator spot-check:** at least one success criterion or one challenge per round was verified by you (the operator), reading the relevant code yourself — not just trusting the reviewer's PASS verdict. The reviewer hallucinates on bug findings (Phase 3a) and it hallucinates on criterion verification too. Spot-checking one item per round is the cheapest defense against a reviewer that returns all-PASS while missing the contract drift Phase 1c was added to catch. Record the spot-check (which criterion / challenge, which file/line you read) in the audit trail under "Operator spot-check."

OR when ANY of:

- All remaining findings are documented as accepted non-blocking risks with the user's explicit ack
- Three consecutive rounds with no new actionable findings (diminishing returns — flag the PR as ready and surface the residue to the user)

A reviewer "going quiet" is not the same as "all success criteria met." The reviewer may simply have run out of generic bugs to find — the PR-specific contract from Phase 1 is still the gate. If a success criterion is unmet but the reviewer didn't flag it, that's a Phase 1 → Phase 2 brief failure: the criterion wasn't sharp enough, or wasn't passed clearly to the reviewer. Sharpen and re-run, don't ship.

If you started in compressed-summary mode (Phase 0) and find yourself entering round 2, upgrade to a full spec doc — multi-round work earns the longer audit trail.

## Final report

When the loop ends, post a single concise message to the user:

```
## Review-fix loop complete

### PR status
- PR #367 — ready-for-human (3 rounds, 4 fixes, head SHA: abc123)
- PR #380 — ready-for-human (1 round, 0 fixes)

### Fix commits pushed
- PR #367:
  - <SHA> fix(<area>): <message>
  - <SHA> fix(<area>): <message>
- PR #380: (none)

### Verification commands run
- <commands discovered in Phase 1.5 and used during the loop> → PASS / FAIL with notes

### Audit trail
- PR #367: docs/superpowers/reviews/<filename>.md
- PR #380: PR comment <comment URL> (compressed format)

### Merge status
NOT MERGED. Awaiting explicit "merge" instruction.
```

Even if the user gave merge permission earlier in the session, re-confirm before merging — review can change the picture and merge intent stated several turns ago is not the same as merge intent stated now.

## Common pitfalls

| Pitfall | What to do instead |
|---|---|
| Firing this skill when the user is fixing actively-failing tests | That's debugging. Read the failure, fix the bug. Use this skill for *latent* bugs, not active ones. |
| Skipping this skill on a "small" PR because manual scan feels enough | The MUST-USE rule applies regardless of PR size. Small PRs get compressed Phase 0, not skipped loops. The bugs you'll miss are exactly the small-looking ones. |
| Re-firing this skill mid-loop when the user asks a follow-up question | Idempotency forbids it. If the audit trail exists for this PR/batch, you're already inside the loop — continue at the appropriate phase, don't restart Phase 0. |
| Skipping Phase 1f self-review because intentions/criteria "felt right" first try | The whole point of 1f is that "felt right" is what ships bad criteria. Run two rounds even when the first pass looks clean — the round-2 finding is often the highest-value one. |
| Running more than two rounds of self-review chasing perfection | Past round 2, new findings get rare and the marginal review cost dominates. Log remaining suspicions for Phase 2 and move on. |
| Skipping Phase 1c (intentions) because "the PR description is enough" | The PR title is what the author *said* they did; the original/new intention is what the *code* actually does and is now supposed to do. Skipping this misses intention-drift bugs entirely. |
| Vague success criteria like "PR is correct" or "tests pass" | Criteria must be observable and failable. Rewrite until each one points at a specific behavior you could verify. |
| Treating reviewer "no findings" as success while a criterion is still unverified | The reviewer can be silent and still wrong about your PR-specific contract. The success criteria are the gate, not reviewer silence. |
| Generic challenges ("what if input is null?") instead of codebase-specific ones | The generic lenses are Pass 2's job. Pass 1's challenges must reference *this* codebase's architecture (its eventing model, its tenancy model, its deployment shape) or they add no value. |
| Acting on every reviewer finding without verification | Reproduce or read the relevant file first; mark unverified findings as such in the spec |
| Bundling unrelated cleanup into a fix commit | One bug per commit; cleanup is a separate PR |
| Putting fixes on main or a new branch | Always `git checkout <pr-branch>` before fixing; `git status` to confirm |
| Skipping the audit trail entirely because "I'll remember" | Always document. Compressed PR comment is fine for solo single-round work; never zero. |
| Skipping Phase 1.5 because "the stack is obvious" | Even when the stack is obvious, the conventions docs aren't. Five minutes here saves an hour of irrelevant findings. |
| Trusting green tests today on time-bomb data | A hardcoded "future" date that hasn't aged out yet is a bug, not a passing test. Grep the diff and adjacent test files for absolute dates. |
| Trusting local-green when the CI runs a different path | A bug class that only manifests under CI (cache globs, missing install steps, env-var divergence) won't appear in your local run. When that's the suspected class, run the CI command path, not the local equivalent. |
| Looping forever on minor findings | Three rounds with diminishing returns is the signal to stop. Document the residue and hand back. |
| Re-running review with a stale local HEAD | Always `git fetch origin <branch>` between rounds and use the fetched SHA |
| Treating reviewer output as gospel | False positives are common. Always read the code referenced before fixing. |
| Introducing a new error-handling style mid-PR during Phase 4b | Match the codebase's existing pattern. New patterns are their own PR. |
| `gh pr merge` because the user said "merge" two messages ago about a different PR | Re-confirm merge intent for THIS PR specifically, in the immediate prior turn |

## What "ready for human" means

A PR is ready for human review when:

- The adversarial review found no remaining real bugs (or only documented accepted residue)
- All confirmed bugs have fix commits with verification recorded in the audit trail
- The audit trail (full spec OR compressed PR comment) lists every round, every bug found, every fix, every rejected finding, every accepted residual risk, and the defensive hardening applied
- The CI on the PR branch is green (or the failures are explicitly documented and unrelated to the fix scope)
- The user has been told the PR is ready and the merge decision has been handed back to them

Never make the merge decision yourself.
