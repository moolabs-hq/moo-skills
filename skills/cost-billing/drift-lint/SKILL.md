---
name: cost-billing-drift-lint
description: >-
  CI step that flags divergence between customer code and saved Moolabs inventories on every PR — catches new endpoints matching billing-event signals but missing an SDK call, confirmed endpoints renamed/moved/deleted, and existing SDK calls with stale event-type or quantity fields. Runs the same code-graph fusion as cost-billing-discovery (AST + OpenAPI + framework registries + telemetry); emits a severity-graded delta report (block-PR / warn / informational / auto-apply) with one-click 'regenerate inventory entry' suggestions. Matching strategy is workflow_id-based (Doc 3 §3.8) so renames survive without false-positives. Degrades to AST-only when monorepo subset CI lacks full code-graph access. Skill 3 in the Cost+Billing suite — the continuous-correctness mechanism that keeps inventories trustworthy post-codemod. Ships as drop-in GitHub Action / GitLab CI; runs locally too. Triggers on "drift lint", "Skill 3", "check for inventory drift", "CI for SDK coverage".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  source: docs/grooming/2026-05-19-cost-billing-discovery-requirements.md §4.4
---

# /cost-billing-drift-lint — Skill 3: Drift lint (CI)

You are a continuous-correctness watchdog. You diff the customer's current code against their saved Moolabs inventories on every PR, flagging drift before it reaches main.

## Trigger

```
/cost-billing-drift-lint <repo>                          # full re-scan
/cost-billing-drift-lint <repo> --pr <pr-number>         # PR-scoped diff
/cost-billing-drift-lint <repo> --since <commit>         # commit-range diff
/cost-billing-drift-lint <repo> --format github-action   # emit annotations
```

Naturally:

```
Check for inventory drift on this PR
Run Skill 3 on the customer's main branch
Is our SDK coverage still complete after this refactor?
```

## Operating principles (apply EVERY drift-lint pass — especially in CI)

See `cost-billing-shared/operating-principles.md`. Drift-lint runs UNATTENDED (CI), so the rule is **FAIL LOUDLY, never silent-default**:

1. **NEVER assume** an inventory entry's deletion was intentional. A missing entry could be (a) the feature was deprecated, (b) the engineer renamed but forgot to `workflow_id`-link, or (c) the customer wants the inventory preserved for audit. Always emit a finding; let the integrator decide.
2. **When in doubt, FAIL the PR**. Drift-lint is the LAST line of defense between the customer's main branch and undetected coverage gaps. False-positive PR-block is recoverable (re-run the chain to re-confirm); false-negative is silent revenue leakage that compounds for months.
3. **Severity rubric is strict, not gradient**:
   - **CRITICAL** (block PR) — schema drift on an active billable workflow (`event_type` changed, `unit` changed, `derivation` references a field that no longer exists).
   - **HIGH** (warn loudly) — workflow_id deleted; rename without history link; new endpoint matches billing signal but has no SDK call.
   - **MEDIUM** (annotate) — confidence drift (an entry's confidence dropped from HIGH to MEDIUM since last scan, e.g., due to a refactor that moved the call site).
   - **LOW** (informational) — pure file/line move (workflow_id stable, code moved). Auto-suggested update only.
4. **Override policy via `.moolabs/drift-policy.yaml`** is OK — customer's CI is their domain. But the SUITE defaults must be conservative (block on CRITICAL, warn on HIGH).

## Read first (shared/)

- `anchor-taxonomy.md` — what entries / events / linkage are; workflow_id matching strategy.
- `sdk-surface-reference.md` — what SDK calls to detect (`client.usage.*` / `client.cost.*` positive; raw `EventsApi`/`CostEventsApi` + the dead `client.meter.events.*` / `client.cls.*` shapes are anti-patterns).
- `v1-decisions-log.md` — drift severity rubric (CRITICAL/HIGH/MEDIUM/LOW).

## Workflow — 3 phases

### Phase 1: Load saved inventories (precondition)

The inventories must exist at `.moolabs/inventory/`:
- `cost-events-inventory.yaml`
- `usage-events-inventory.yaml`
- `output-input-map.yaml`

If absent, refuse with: "No inventories found. Run `/cost-billing-discovery` first and commit the inventories."

If present but stale (no `last_synced` timestamp or older than 90 days), warn but continue.

### Phase 2: Re-scan code (same fusion as discovery)

Run `scripts/inventory_load.py` to parse saved inventories. Then run the **same code-graph fusion** Skill A uses:

- AST scan (per-language adapter from `cost-billing-discovery/scripts/catalog_match.py`)
- OpenAPI specs at known paths
- Framework route registries (FastAPI routers, Express routes, etc.)
- OpenTelemetry instrumentation calls (if present)

Output: `.moolabs/drift/current-scan.yaml` — the current snapshot.

**Degraded mode** (monorepo subset CI / sparse checkout): AST-only on changed files. The delta report flags `mode: ast-only` + a note "coverage incomplete; some classes of drift cannot be detected without full repo access."

### Phase 3: Diff + emit delta report

Run `scripts/drift_diff.py` and `scripts/delta_report.py`. Diff strategy is **workflow_id-based** (per Doc 3 §3.8, `v1-decisions-log.md` #19n):

- Each inventory entry has a `workflow_id`. Drift is detected by re-deriving `workflow_id` for current code and matching.
- Stable `workflow_id` + changed `file:line` = **rename** (severity LOW, informational).
- Stable `workflow_id` + missing in current code = **deleted** (severity HIGH, warn).
- New code matching a cost/usage signal + no `workflow_id` match in inventory = **new endpoint** (severity HIGH if billing-event signal; MEDIUM if cost-event signal).
- Existing SDK call with stale event type / quantity field = **schema drift** (severity CRITICAL).

**Delta report shape:**

```yaml
report_version: 0.1.0
mode: full | ast-only
total_drift_items: 7
blocking: 1
warnings: 4
informational: 2
items:
  - kind: new-endpoint-no-sdk-call
    file: services/api/handlers/streaming.py
    line: 84
    signal: "verb pattern '_streamed' + span.kind=server"
    severity: HIGH
    suggested_action: "Run /cost-billing-discovery --refresh services/api"
    suggested_inventory_entry: |
      - workflow_id: api.streaming.stream-complete
        event_type: chat.streamed
        framework: fastapi
        confidence: 0.81
  - kind: renamed-handler
    file: services/api/handlers/render.py
    line: 62
    workflow_id: api.render.image-rendered
    previous_file_line: services/api/handlers/render.py:48
    severity: LOW
    suggested_action: "auto-update file:line in inventory (no review needed)"
  - kind: schema-drift
    file: services/billing/seat_assign.py
    line: 117
    workflow_id: billing.seat-assigned
    drift: "event type was 'seat.assigned'; now emitting 'seat.activated'"
    severity: CRITICAL
    suggested_action: "Block PR; one of {code, inventory} is wrong"
```

**Severity → CI action (v1 default):**

| Severity | CI action |
|---|---|
| CRITICAL | Block PR (exit 1). |
| HIGH | Warn (annotate) but allow merge. |
| MEDIUM | Informational (annotate only). |
| LOW | Auto-suggest inventory update; do not block or warn. |

The customer's repo can override via `.moolabs/drift-policy.yaml`:

```yaml
block_on: [CRITICAL]
warn_on: [HIGH, MEDIUM]
auto_apply: [LOW]
```

## GitHub Action / GitLab CI integration

Ship `assets/github-action.yml`:

```yaml
name: Moolabs Drift Lint
on: pull_request
jobs:
  drift-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      # ⚠️ moolabs-drift-lint is NOT on PyPI as of 2026-05-25 (returns 404).
      # The tool is bundled with the cost-billing skill suite — customer installs it
      # by cloning moo-skills and pointing at the local script. The package name
      # `moolabs-drift-lint` is a placeholder for the post-GA published name.
      # See cost-billing-shared/sdk-surface-reference.md §"Install" for the git-URL
      # pattern customers use for similar Moolabs tools until publishing lands.
      - run: |
          # TODO(post-GA): replace with `pip install moolabs-drift-lint` once published.
          git clone --depth 1 https://github.com/moolabs-hq/moo-skills.git /tmp/moo-skills
          pip install /tmp/moo-skills/skills/cost-billing/drift-lint/scripts
      - run: moolabs-drift-lint --pr ${{ github.event.pull_request.number }} --format github-action
```

The `--format github-action` mode emits inline PR annotations at `file:line`.

## Outputs

| File | Used by |
|---|---|
| `.moolabs/drift/current-scan.yaml` | Skill A re-runs (`--refresh`) can compare. |
| `.moolabs/drift/delta-report.yaml` | CI report; can be archived as PR comment. |
| `.moolabs/drift/delta-report.md` | Human-readable rendering for review. |

## What this skill MUST NOT do

- **Never** auto-modify the inventories. Only suggest. The integrator runs `/cost-billing-discovery --refresh` to apply.
- **Never** block PR on LOW or MEDIUM severity (v1 default; customer can override).
- **Never** scan files outside the repo or send code to remote services. Drift-lint is local-only by design.
- **Never** assume `--ast-only` mode has detected all drift — surface the mode in the report header so reviewers know coverage is partial.

## Reference files

- `references/ci-integration.md` — GitHub Actions, GitLab CI, CircleCI templates.
- `references/delta-severity.md` — full severity rubric + override policy.
- `references/workflow-id-matching.md` — how `workflow_id` is derived per entry kind.
- `references/degraded-ast-only.md` — what's covered and what isn't in AST-only mode.

## Scripts

- `scripts/inventory_load.py` — parse `.moolabs/inventory/*.yaml`.
- `scripts/drift_diff.py` — diff saved inventories vs. current scan via `workflow_id`.
- `scripts/delta_report.py` — emit YAML + markdown reports.
- `scripts/github_action_format.py` — translate delta items to GH annotation syntax.

## Assets

- `assets/github-action.yml` — drop-in CI step.
- `assets/gitlab-ci.yml` — drop-in for GitLab.
- `assets/drift-policy.schema.yaml` — JSON-Schema for `.moolabs/drift-policy.yaml`.
