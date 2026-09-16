# 2026-09-16 skip CloudWatch logging PR review execution

> WARNING: Cross-model rule violated: code generation and review both ran on
> GPT-5. Same-model self-review is a known weak spot. Treat this review as less
> independent than a cross-model pass and consider a second-model review before
> merge.
>
> The generic `adversarial-pr-review` skill referenced by the Moolabs overlay
> was unavailable. This review uses the Cost+Billing five-phase specialist gate
> plus the Moolabs-specific overlay as the documented fallback.

## Summary of changes

PR #44 adds an explicit `--skip-logging` degraded mode to the Cost+Billing AWS
installer and its Fargate setup helper. When selected, setup must avoid
CloudWatch Logs APIs and register the task without an ECS `logConfiguration`.
The existing strict CloudWatch logging path must remain the default.

Review target: `2ab0f3ae5d4e9216115a99104f8d836b1f2f187e`

## Risk map

- A flag accepted by `install.sh` but not forwarded through every guided setup
  branch, including dry-run followed by a real run.
- Skip mode still calling CloudWatch Logs or retaining an `awslogs`
  configuration, leaving restricted-IAM customers blocked.
- Default mode silently losing its log-group verification or task logging.
- A task succeeding while the installer prints an unusable log-tail command,
  or failing without explaining that logs are unavailable.
- Generated ECS task-definition JSON becoming invalid in either mode.
- Bash 3.2 incompatibility in the optional-argument array handling.
- A schedule being created after verification failure.
- Documentation implying that skip mode preserves stdout/stderr.
- Cross-repo coordination: not applicable; this is a customer-portable shell
  installer and does not change an API/event/schema consumed by another repo.
- Moolabs routing, middleware, frontend, schema, and event-contract checks: not
  applicable to this diff.

## Verification commands

```bash
gh pr view 44 --repo moolabs-hq/moo-skills --json headRefOid,baseRefOid,mergeable,mergeStateStatus,statusCheckRollup
git diff --check origin/main...HEAD
bash -n skills/cost-billing/shared/install.sh
/bin/bash -n skills/cost-billing/shared/install.sh
bash -n skills/cost-billing/cloud-bill-cli/scripts/aws-fargate-setup.sh
/bin/bash -n skills/cost-billing/cloud-bill-cli/scripts/aws-fargate-setup.sh
bash skills/cost-billing/cloud-bill-cli/tests/test_aws_fargate_setup.sh
/bin/bash skills/cost-billing/cloud-bill-cli/tests/test_aws_fargate_setup.sh
bash skills/cost-billing/shared/scripts/test_aws_session.sh
bash skills/cost-billing/scripts/test-suite.sh
(cd skills/cost-billing/cloud-bill-cli && uv run --extra dev pytest -q)
shellcheck skills/cost-billing/shared/install.sh skills/cost-billing/cloud-bill-cli/scripts/aws-fargate-setup.sh skills/cost-billing/cloud-bill-cli/tests/test_aws_fargate_setup.sh
```

The focused Fargate regression also parses the generated JSON, records every
mock CloudWatch Logs call, and asserts that skip mode makes none while default
mode retains the existing `awslogs` configuration.

## Pre-recorded notes

- The change was requested for a customer environment whose operator cannot
  create or inspect CloudWatch log groups.
- Skip mode is intentionally opt-in. The default must continue to fail loudly
  when required logging cannot be configured.
- Without ECS `logConfiguration`, application stdout/stderr cannot be recovered
  from CloudWatch; ECS stop code, stopped reason, and container exit code remain
  available for verification.
- PR description includes a `No-PRD-needed` rationale for this narrow installer
  compatibility change.

## Review iterations

### Iteration 1

#### Candidate 1: installer flag propagation

- Severity: HIGH if real.
- Verification: inspect `_ORIG_ARGS` recursion and exercise
  `_run_aws_fargate_setup` with a command-capture shim for immediate run,
  dry-run followed by real run, and deferred-command output under Bash 5 and
  Bash 3.2.
- Verdict: not a functional bug. Command-capture verification passed for the
  immediate run, dry-run followed by real run, and deferred-command paths under
  Bash 5 and Bash 3.2. A permanent regression test was added because this was
  previously verified only by inspection.

#### Candidate 2: skip mode still depends on CloudWatch Logs

- Severity: HIGH if real.
- Verification: the focused shell regression makes every `aws logs` call fail
  in skip mode, records whether any call occurred, parses the registered task
  definition as JSON, and rejects `logConfiguration` or `awslogs` content.
- Verdict: not a bug. Skip-mode registration succeeded while the AWS mock was
  configured to fail any Logs call; the marker proved no call occurred, and the
  parsed task definition contained neither `logConfiguration` nor `awslogs`.
  Both successful and non-zero application verification paths omit unusable
  `aws logs tail` guidance.

#### Candidate 3: default mode no longer fails closed

- Severity: HIGH if the runtime behavior regressed; LOW for the confirmed test
  gap.
- Verification: existing coverage only exercised an existing log group. The
  missing fault-injection case was confirmed by inspection.
- Verdict: real LOW regression-coverage gap.

### Fix: preserve the default fail-closed contract in regression coverage

| What was wrong | What changed | What we ran to confirm |
|---|---|---|
| Tests proved default logging only when the log group existed; they did not lock down behavior when Logs APIs are denied. Skip-mode application failures also lacked a direct assertion against unusable log-tail guidance. | Added a denied-CloudWatch scenario that requires task registration to stop, plus a skip-mode non-zero application-exit scenario that permits ECS diagnosis but forbids a log-tail command. | `bash skills/cost-billing/cloud-bill-cli/tests/test_aws_fargate_setup.sh` and `/bin/bash skills/cost-billing/cloud-bill-cli/tests/test_aws_fargate_setup.sh` |

#### Candidate 4: ECS rejects a task definition without log configuration

- Severity: CRITICAL if real because restricted-IAM setup would always fail.
- Verification: parse both generated task-definition variants as JSON and
  inspect the installed Botocore ECS service model.
- Verdict: not a bug. Both variants parse, and Botocore reports no required
  `ContainerDefinition` members; `logConfiguration` is an optional structure.

#### Candidate 5: Bash 3.2 optional-array failure

- Severity: HIGH if real because customer macOS environments use Bash 3.2.
- Verification: syntax checks plus focused Fargate and forwarding regressions
  under `/bin/bash` 3.2.57.
- Verdict: not a bug. All focused checks passed.

Status: one LOW test gap fixed; no functional defect confirmed.

### Iteration 2

The radius-two robustness sweep covered the top-level wrapper, shared installer,
direct Fargate helper, immediate and deferred launch branches, dry-run-to-real
transition, existing and denied log-group paths, successful and failed ECS
verification, and the related README/runbook guidance.

Fresh results:

- Cost+Billing suite: 231 passed, 0 failed; the optional dependency run also
  executed all 33 template/adversarial assertions with 0 failures.
- Cloud-bill CLI pytest suite: 132 passed.
- Focused tests: passed under Bash 5 and macOS Bash 3.2.
- Generated task definitions: valid JSON in both modes.
- `git diff --check`: passed.
- Changed-file ShellCheck: no new warning class from this PR; remaining notices
  are pre-existing installer/helper warnings or indirect test-stub notices.

No CRITICAL, HIGH, or MEDIUM findings remain. The same-model warning at the top
is retained as an accepted process limitation, not a code defect.

Status: clean with accepted cross-model-review limitation.

## Moolabs-specific verifications

- Cross-repo consumers checked: n/a; no shared API, schema, event, or runtime
  service contract changes.
- Cross-skill checks applied: Cost+Billing cloud setup and adversarial-review.
- Coda PRD linked from PR: no; the PR includes a narrow `No-PRD-needed`
  rationale tied to the team standup request.
- Post-merge feature-summariser needed: no; this is a documented installer flag,
  not a new service architecture.
- Outline doc handoff: n/a.

verdict: clean-with-accepted-risks
