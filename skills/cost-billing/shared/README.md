# `shared/` — library, not a slash-invocable skill

This directory is **not a skill**. It has no `SKILL.md`, no slash command, and
nothing happens if you type `/cost-billing-shared`. Despite that, `install.sh`
copies it to `~/.claude/skills/cost-billing-shared/` so the other 10 skills can
reach it via consistent post-install paths.

## What lives here

| File | Used by |
|---|---|
| `install.sh` | The actual install workhorse (suite-root `install.sh` is a thin wrapper that delegates here). |
| `operating-principles.md` | Hard rules every skill in the suite follows (NEVER assume, ASK if in doubt, ONE question at a time, save state). Referenced by every SKILL.md. |
| `chain-handoff.md` | The 4-silo bootstrap workflow + multi-product / multi-service fan-out diagram. Referenced by every `bootstrap-*` skill. |
| `three-role-review.md` | The CFO ⇄ PM ⇄ Engineer review cycle the `signoff` orchestrator implements. |
| `sdk-surface-reference.md` | Human-readable summary of the Moolabs SDK shape. The Phase 1.5 snapshot (`instrument/scripts/sdk_snapshot.py`) is the runtime source of truth; this doc is the hint for human reviewers. Also documents known upstream SDK issues. |
| `v1-decisions-log.md` | Audit trail of v1 default decisions, each with rationale + "revisit at" trigger. |
| `gaps-tracker.md` | Open gaps from the requirements doc + status of each. |
| `anchor-taxonomy.md` | Vocabulary — cost vs usage event, refund test, attribution keys, cells ③/④, etc. |
| `desktop-app-guide.md` | How to use the .zip outputs of `install.sh --package` with Claude Desktop. |
| `assets/mcp-catalog.json` | Catalog of MCPs `install.sh --mcp <name>` knows how to configure. |

## Why it lives in `skills/` despite not being a skill

The Claude Code platform doesn't have a first-class concept of "shared library
within a skill suite." If `shared/` lived outside `skills/`, the other skills'
references to `cost-billing-shared/X.md` would break after install (the files
wouldn't exist at the expected post-install path). Putting `shared/` inside
`skills/` and installing it alongside the others as `cost-billing-shared/`
makes cross-references stable.

The platform will list `cost-billing-shared` in its skill list. That's
cosmetic — there's no slash command, so it can't be invoked. We accept that
trade-off rather than fragment the suite across multiple directories.

## See also

For the full architecture and "how the 10 skills fit together", see the
suite-root `../README.md`.
