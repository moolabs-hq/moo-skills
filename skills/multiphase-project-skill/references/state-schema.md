# State Schema

The single source of truth lives at `.multiphase-project/state.yaml`. The orchestrator never writes this file directly — it shells out to `scripts/state_manager.py`, which validates every transition.

## Full schema

```yaml
project:
  name: string                  # required. human-readable project name
  description: string           # optional. one-line summary
  status: string                # required. one of: new, in_progress, complete, aborted
  current_phase_index: int      # required. 0-based index into phases[]
  created_at: ISO-8601 string   # set by `init`
  updated_at: ISO-8601 string   # set by every save
  completed_at: ISO-8601 string # set when project.status becomes 'complete'

phases:                         # ordered list; index = execution order
  - id: string                  # required. kebab-case, unique within project
                                #   convention: NN-name, e.g. 01-foundation
    name: string                # required. human-readable
    description: string         # optional
    status: string              # required. see "Phase status values" below
    works:                      # decomposed tasks within this phase
      - id: string              # required. kebab-case, unique within phase
        description: string     # required
        status: string          # required. one of: pending, in_progress, complete, skipped
        acceptance_criteria: string  # optional
        completed_at: ISO-8601 string  # set by mark-work-done
    artifacts:                  # paths to phase deliverables
      prd_path: string          # path to PRD markdown
      review_path: string       # path to latest review markdown
      ralph_log: string         # path to ralph-loop log (optional)
      # any additional kv pairs the orchestrator wants to record
    error: string               # set when status == blocked; message describing the blocker
    created_at: ISO-8601 string # set by add-phase
    completed_at: ISO-8601 string  # set when status becomes 'complete'
```

## Phase status values

The status sequence encodes the per-phase state machine. The orchestrator transitions a phase through these states in order:

| Status | Meaning | Set by | Next |
|---|---|---|---|
| `pending` | Phase exists but no work has started | `add-phase` | `decomposing` |
| `decomposing` | Currently breaking the phase into works | orchestrator | `decomposed` |
| `decomposed` | Works enumerated, ready for grooming | orchestrator | `grooming` |
| `grooming` | `/prd` is generating the PRD | orchestrator | `groomed` |
| `groomed` | PRD saved as artifact, ready for development | orchestrator | `developing` |
| `developing` | `/ralph-loop` is implementing the works | orchestrator | `developed` |
| `developed` | Ralph hit its completion promise; ready for review | orchestrator | `reviewing` |
| `reviewing` | `/adversarial-pr-review` is evaluating the diff | orchestrator | `complete` (or back to `developing` if findings) |
| `complete` | Review conditions satisfied; ready to advance | orchestrator | (terminal for this phase) |
| `blocked` | A sub-command failed or a human escalation is required | `set-error` | (manual: `clear-error` then re-enter at earlier status) |

`decomposing`, `grooming`, `developing`, `reviewing` are *in-flight* states. They exist so the orchestrator can resume mid-stage: if it sees `grooming`, it knows `/prd` was running and didn't finish, so it should re-invoke `/prd`.

`decomposed`, `groomed`, `developed` are *between* states. They exist so the orchestrator can resume between stages: if it sees `developed`, it knows ralph finished but review hasn't started, so it should run `/adversarial-pr-review`.

## Work status values

| Status | Meaning |
|---|---|
| `pending` | Not yet started |
| `in_progress` | Ralph is actively working on this |
| `complete` | Implementation + tests done; satisfies acceptance criteria |
| `skipped` | Deliberately not implemented; recorded for traceability |

## Project status values

| Status | Meaning |
|---|---|
| `new` | `init` has run; no phases added yet |
| `in_progress` | At least one phase exists and not all are complete |
| `complete` | `current_phase_index >= len(phases)` |
| `aborted` | Manually set when the project is abandoned |

## Example

```yaml
project:
  name: Analytics Dashboard
  description: Real-time analytics dashboard for the growth team
  status: in_progress
  current_phase_index: 2
  created_at: '2026-05-26T13:11:00+00:00'
  updated_at: '2026-05-27T09:42:13+00:00'

phases:
  - id: 01-foundation
    name: Foundation
    description: DB schema, event ingestion, CI/CD
    status: complete
    works:
      - id: w1-schema
        description: Create events, users, sessions, cohorts tables and migrations
        status: complete
        acceptance_criteria: Migrations run clean against a fresh DB
        completed_at: '2026-05-26T18:02:00+00:00'
      - id: w2-ingest
        description: HTTP endpoint that accepts events and writes to the DB
        status: complete
        acceptance_criteria: POST /events returns 202; events appear in DB within 1s
        completed_at: '2026-05-27T02:14:00+00:00'
      - id: w3-ci
        description: GitHub Actions workflow
        status: complete
        acceptance_criteria: CI fails on lint, type errors, test failures
        completed_at: '2026-05-27T05:30:00+00:00'
    artifacts:
      prd_path: .multiphase-project/phases/01-foundation/prd.md
      review_path: .multiphase-project/phases/01-foundation/review-002.md
    created_at: '2026-05-26T13:14:00+00:00'
    completed_at: '2026-05-27T08:00:00+00:00'

  - id: 02-metrics
    name: Core metrics
    description: DAU, session length, funnel queries
    status: complete
    works: []
    artifacts:
      prd_path: .multiphase-project/phases/02-metrics/prd.md
      review_path: .multiphase-project/phases/02-metrics/review-001.md
    created_at: '2026-05-26T13:14:30+00:00'
    completed_at: '2026-05-27T09:30:00+00:00'

  - id: 03-cohorts
    name: Cohort breakdown
    description: Cohort definition UI and per-cohort queries
    status: reviewing
    works:
      - id: w1-cohort-model
        description: Cohort entity, CRUD, validation
        status: complete
      - id: w2-cohort-queries
        description: Per-cohort metric queries
        status: complete
    artifacts:
      prd_path: .multiphase-project/phases/03-cohorts/prd.md
      review_path: .multiphase-project/phases/03-cohorts/review-001.md
    created_at: '2026-05-26T13:15:00+00:00'
```

## Validation rules (enforced by `state_manager.py`)

- `project.status` must be in `{new, in_progress, complete, aborted}`
- `project.current_phase_index` must be a non-negative integer
- `phase.id` must be unique across all phases
- `phase.status` must be in the phase status values above
- `work.id` must be unique within its phase
- `work.status` must be in the work status values above
- `advance-phase` refuses to advance if any work is not `complete` or `skipped` (unless `--force`)
