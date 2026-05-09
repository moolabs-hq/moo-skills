# moo-skills

A Claude Code plugin bundling the development skills used at Moolabs — adversarial PR review, the grooming workflow (requirements → HLD → contracts → BE/FE → task breakdown), testing blueprints, feature-flag patterns, and reviewer/sparring-partner/architect personas.

Each guide is packaged as a discoverable skill with YAML frontmatter, so the agent can invoke it via the `Skill` tool when its trigger phrases match.

## Layout

```
.
├── .claude-plugin/
│   └── plugin.json               # plugin manifest
├── skills/
│   ├── dev-workflow-orchestrator/
│   ├── adversarial-pr-review/
│   ├── feature-flags-guide/
│   ├── api-contracts-reference/
│   ├── frontend-unit-testing/    # blueprint + roadmap.md + usage.md
│   ├── backend-unit-testing/
│   ├── backend-api-testing/
│   ├── testing-qa-persona/
│   ├── grooming-requirements/
│   ├── grooming-task-breakdown/
│   ├── grooming-contracts/
│   ├── grooming-be/
│   ├── grooming-fe/
│   ├── grooming-fresh-context-validation/
│   ├── feature-summariser/
│   ├── documentation-unifier/
│   ├── hld-tech-specs-creator/
│   ├── persona-system-design-reviewer/
│   ├── persona-sparring-partner/
│   └── persona-senior-engineer/
└── docs/
    └── branch-86d1fejc4-documentation.md   # historical reference (not a skill)
```

## Skills at a glance

### End-to-end orchestration
- **dev-workflow-orchestrator** — runs the full chain (PRD → tested + reviewed feature) on a single docs.moolabs.com URL, pausing for sign-off after every stage and persisting each deliverable back to Outline. Delegates the substance of each stage to the skills below. Requires the Outline MCP.

### Code review
- **adversarial-pr-review** — multi-round review-fix loop on open PRs with verify-then-fix discipline; never merges without explicit permission.

### Grooming workflow (run roughly in this order)
- **grooming-requirements** — turn a Coda PRD + Figma into an exhaustive product-requirements doc; no code, no timelines.
- **hld-tech-specs-creator** — schema-first HLD against the existing infra (Step Functions, EventBridge+Lambda, Supabase, Redis, EKS); mermaid encouraged.
- **grooming-contracts** — API contracts and schemas only; backward-compat plans and structured error envelopes.
- **grooming-be** — backend LLD (services, validation, error mapping, migrations, observability, feature-flag eval, test plan); no code.
- **grooming-fe** — frontend LLD (component reuse, props, click/hover/empty/error states, Mixpanel events, test plan); no code.
- **grooming-task-breakdown** — split the spec into independently developable tasks with DoD/AC/testing.
- **grooming-fresh-context-validation** — review a spec with no prior knowledge; flag ambiguity/missing-context/assumptions/contradictions.
- **feature-summariser** — write the future-developer architectural summary (HLD/LLD + ADR rationale).
- **documentation-unifier** — cross-doc gap analysis and consistency check across PRD/HLD/grooming docs.

### Testing blueprints
- **frontend-unit-testing** — Vitest + Testing Library; agent-edition completion gates; bundled `roadmap.md` and `usage.md`.
- **backend-unit-testing** — pytest with mocking policy ("if you need >3 mocks, refactor").
- **backend-api-testing** — pytest + httpx / FastAPI TestClient; BDD principles without Behave/Gherkin.
- **testing-qa-persona** — QA-only mode that surfaces failures and fixes broken tests, never edits production code.

### Feature flags
- **feature-flags-guide** — three correct patterns (component swap, prop variation, route-level swap), four anti-patterns, mandatory cleanup window, PR review checklist.

### Reference
- **api-contracts-reference** — `/node/updated_config_and_status` request/response shapes for first-time-open vs field-change.

### Personas
- **persona-system-design-reviewer** — systematic backend design reviews (problem → strategy → implementation → gaps).
- **persona-sparring-partner** — probing question-first peer; not a yes-man, not a naysayer; converges to actionable decisions.
- **persona-senior-engineer** — fast-shipping, simple, extensible Python/FastAPI approaches with documented shortcuts.

## Installing the plugin

This repo doubles as a single-plugin marketplace, so install it through Claude Code's plugin manager — Claude Code only auto-loads plugins that are registered in `installed_plugins.json`, which is populated by `/plugin install`. A bare symlink under `~/.claude*/plugins/` won't be discovered.

```text
/plugin marketplace add /Users/kritivasas.shukla/code/personal/moolabs/moo-skills
/plugin install moo-skills@moo-skills
/reload-plugins
```

(If you previously created a symlink at `~/.claude-moolabs/plugins/moo-skills`, remove it before installing — it isn't tracked by the plugin manager and just clutters the directory.)

After install, the `Skill` tool will list every skill with the `moo-skills:` namespace (e.g. `moo-skills:adversarial-pr-review`, `moo-skills:grooming-be`).

## Adding or editing a skill

1. Create `skills/<kebab-name>/SKILL.md`.
2. Add YAML frontmatter with `name` (matching the directory) and a `description` that includes the trigger phrases users will say.
3. Body = the skill's instructions. Keep them imperative and direct.
4. Supplementary files (roadmaps, usage notes, examples) live alongside `SKILL.md` in the same directory and can be `Read`'d by the skill at runtime.

The description field is the activation criterion — the agent only loads the skill body when this description matches the user's intent. Front-load trigger phrases.
