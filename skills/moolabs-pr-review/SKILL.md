---
name: moolabs-pr-review
description: Moolabs-specific overlay on top of moo-skills:adversarial-pr-review. Run the generic adversarial loop FIRST; this skill adds the company-specific checks the generic detection won't surface — internal middleware behavior (OM-Namespace, customer-key path, OpenMeter sink single-node-vs-cluster), cross-skill checks (feature-flags-guide if FF code changed; grooming-contracts if API surface changed; feature-summariser as the post-merge gate), Coda PRD linkage in the PR description, Outline architecture-doc handoff to docs.moolabs.com, and multi-repo coordination across moolabs-app / openmeter / nrev-ui-2. Use this — not the generic skill alone — when reviewing PRs on Moolabs repos. Triggers on "moolabs review", "review this <repo> PR", or any adversarial review request inside the Moolabs codebases. Requires moo-skills:adversarial-pr-review and references the existing Moolabs grooming/testing/feature-flag/feature-summariser skills.
---

# Moolabs PR Review Overlay

This skill is a checklist that sits on top of the generic adversarial loop. It does not replace `moo-skills:adversarial-pr-review` — it adds Moolabs-only items the generic detection step won't surface, and it routes to other moo-skills when their domain shows up in the diff.

## When this applies

The user is asking for an adversarial review on a PR in a Moolabs-owned repo:
- `moolabs-app` — BFF / API layer (Python)
- `openmeter` — metering / billing pipeline (Go)
- `nrev-ui-2` — frontend (TypeScript / Next.js)
- Any other repo under `moolabs-hq/`

If the PR is in a non-Moolabs repo, use `moo-skills:adversarial-pr-review` directly without this overlay.

## Procedure

1. **Run `moo-skills:adversarial-pr-review` first.** That skill creates the spec, detects the stack, and runs the loop. Don't reimplement any of it here.
2. **Before the first review round**, add the items below to the spec's "Risk map" section so the reviewer briefing in Phase 2 includes them.
3. **Verify each Moolabs-specific item before declaring the PR ready-for-human.** Add a "Moolabs-specific verifications" subsection to the spec's per-PR detail.

## Moolabs-specific risk items

### Cross-repo coordination

The generic detection sees one repo at a time; cross-repo drift is a Moolabs problem and the most common source of post-merge bugs:

- If the PR touches API contracts in `openmeter` or `moolabs-app`, find the matching consumer in `nrev-ui-2` and confirm it isn't broken by the change. Search by endpoint path or response field name; don't assume the FE doesn't touch this surface.
- If the PR adds or renames an event/topic/queue, search the other repos for consumers before the rename ships.
- Note in the spec under "Cross-repo coordination": "Coordinated PR in `<other-repo>` #`<n>`" or "Confirmed no consumer in `nrev-ui-2` (reason: …)" or "Follow-up filed: …".

### Internal middleware and routing behavior

The generic loop won't know these without being told:

- **OM-Namespace middleware** sits in front of `/v1/*` routes in OpenMeter. It must not 4xx on routes that should be tenant-agnostic (`/healthz`, `/livez`, `/readyz`, `/metrics`, anything under `/_internal/*`). If the PR changes the route table or middleware ordering, verify this explicitly — past incidents trace back to this.
- **Customer-key auth path**: when a request authenticates via customer key instead of namespace header, the namespace must still be derived for downstream services. Verify both auth paths produce the same downstream context (same `tenant_id`, same `meter_slug` resolution).
- **OpenMeter sink — single-node vs cluster table writes**: if the PR touches sink code, both `EventsTableName` and `DistributedTableName` paths must be correct. Cross-reference `phase3_schema_test.go` patterns; substring-based assertions on table names need paired negative assertions (`NotContains` for the cluster variant when checking the single-node variant, and vice versa).
- **`ingested_at` Kafka header parsing**: if the PR touches sink message parsing, verify the `RawEvent.IngestedAt` is sourced from the header when present, not silently falling back to `time.Now()`.

### Cross-skill checks (route to other moo-skills if applicable)

If the diff includes specific patterns, also consult these moo-skills before signing off:

| Diff includes | Consult | Specific check |
|---|---|---|
| `utils/feature-flags.ts`, `NEXT_PUBLIC_*` env vars, tenant-scoped flag helpers, `FEATURE_FLAG.*` references | `moo-skills:feature-flags-guide` | Flag at highest branch point, ≤3 callsites, all in containers; no flags in props/hooks/business logic; no compound flag conditions; cleanup window documented |
| New endpoints, changed response shapes, schema migrations, `Record<string, …>` payload shapes | `moo-skills:grooming-contracts` | Backward-compat plan exists; structured-error envelopes (400 with actionable message vs 500 generic); behaviour-driven endpoint count |
| ent schema, atlas migrations, ClickHouse DDL, alembic | `moo-skills:grooming-be` | `NOT NULL` columns have backfill default; rollback path; index strategy for the access pattern; idempotency on retried requests |
| New React component / page / hook | `moo-skills:grooming-fe` | Empty/error/loading states; default sort; pagination; Mixpanel events; component reuse vs new component decision documented |
| Tests added/changed in `nrev-ui-2` | `moo-skills:frontend-unit-testing` | Vitest patterns; testing pure logic over JSX; full-suite gate before declaring done |
| Tests added/changed in `moolabs-app` | `moo-skills:backend-unit-testing` + `moo-skills:backend-api-testing` | pytest scoping; >3 mocks → refactor; FastAPI TestClient for integration |

If a check fails, surface it as a finding in the same review-fix loop — it's a Moolabs-defined contract violation even if the generic reviewer didn't flag it.

### Documentation linkage

- The PR description should reference a Coda PRD or ClickUp task (or be flagged "no-PRD-needed" with a one-liner reason). Missing PRD linkage on a meaningful change is itself a finding.
- **Post-merge architecture summary** belongs at `docs.moolabs.com/<feature-slug>/` per `moo-skills:feature-summariser`. If the PR ships a meaningful new behavior, the final report should remind the user to run `feature-summariser` (or `moo-skills:dev-workflow-orchestrator` for the whole post-merge handoff including the Outline write).

### Test commands — canonical Moolabs invocations

The generic skill's Phase 1.5 should discover these from `Makefile` / `package.json` / `pyproject.toml`. If it doesn't, paste from this list into the spec's "Verification commands":

| Repo | Scoped run | Package run | Full run |
|---|---|---|---|
| `openmeter` (Go) | `go test -tags=dynamic ./<pkg> -run <TestName>` | `go test -tags=dynamic ./<pkg>` | `go test -tags=dynamic ./...` |
| `moolabs-app` (Python) | `uv run pytest <path> -x -k <name>` | `uv run pytest <path> -x` | `uv run pytest -x` |
| `nrev-ui-2` (TS) | `vitest run <path>` or `npm run test:unit:changed` | (path-filtered vitest) | `npm run test:unit` |

The Go `-tags=dynamic` flag is mandatory for sink/connector tests — without it the dynamic ClickHouse client is excluded and tests silently skip relevant code paths.

## Final report addition

When the loop ends, append to the generic skill's final report:

```
### Moolabs-specific verifications
- Cross-repo consumers checked: <list of repos / "n/a">
- Cross-skill checks applied: <feature-flags-guide / grooming-contracts / grooming-be / etc. — or "none">
- Coda PRD linked from PR: yes / no (reason)
- Post-merge feature-summariser needed: yes (suggest dev-workflow-orchestrator) / no (reason)
- Outline doc handoff: <link or "n/a">
```

The merge decision still belongs to the user — same rule as the generic skill, no exceptions.

## When to bypass this overlay

- Documentation-only PRs in a Moolabs repo — use `moo-skills:adversarial-pr-review` alone.
- Renovate / dependabot dependency-bump PRs — the cross-skill checklist is mostly noise; rely on the generic loop and CI.
- Hot-fix PRs already pre-discussed where the user has explicitly waived the overlay checklist.
