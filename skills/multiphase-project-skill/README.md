# multiphase-project-skill

An agent skill that walks a software project from a one-line idea all the way
through to merged, adversarially-reviewed code — without losing state between
phases and without skipping quality gates.

It does this by chaining four existing slash commands you already have
installed:

- `/brainstorming` — identify project phases
- `/prd` — groom each phase into a Product Requirements Document
- `/ralph-loop` — drive implementation until the phase's completion promise is met
- `/adversarial-pr-review` — review the diff until zero CRITICAL and zero HIGH findings remain

Between every stage it persists state to `.multiphase-project/state.yaml` in
your project root, so any session can resume exactly where the previous one
stopped.

## Why this exists

Multi-phase projects fall apart at the seams. A human (or an agent) starts the
work, gets interrupted, comes back a week later, can't remember which phase is
current, re-runs steps that already ran, skips a review because "I think I
already did that one." This skill removes that memory tax. State lives on
disk; resume is one command; quality gates are enforced by code, not by
discipline.

## Install

```bash
# Auto-detect your platform (Claude Code, Cursor, Copilot, etc.)
./install.sh

# Or pick a specific target
./install.sh --platform claude
./install.sh --platform claude-moolabs
./install.sh --platform cursor

# Install everywhere it can detect
./install.sh --all

# See what it would do without doing it
./install.sh --dry-run
```

### `CLAUDE_CONFIG_DIR`

The `claude` platform honors `$CLAUDE_CONFIG_DIR` — the same env var Claude
Code itself uses to relocate its config directory. If set, `--platform claude`
installs into `$CLAUDE_CONFIG_DIR/skills/`. If unset, it falls back to
`~/.claude/skills/`. The `claude-moolabs` platform always means the literal
path `~/.claude-moolabs/skills/`; pass it explicitly when you want that
target regardless of env.

If you run Claude Code with a custom config dir (e.g.
`CLAUDE_CONFIG_DIR=~/.claude-moolabs claude`), make sure the same env var is
set when you run `install.sh` — otherwise the skill lands in a location your
session never reads.

### Manual install

Drop the whole `multiphase-project-skill/` directory into your platform's
skills path:

| Platform | Destination |
|---|---|
| Claude Code (user) | `${CLAUDE_CONFIG_DIR:-~/.claude}/skills/multiphase-project-skill/` |
| Claude Code (Moo) | `~/.claude-moolabs/skills/multiphase-project-skill/` |
| Cursor (project) | `.cursor/rules/multiphase-project-skill/` |
| GitHub Copilot | `.github/skills/multiphase-project-skill/` |
| Universal | `~/.agents/skills/multiphase-project-skill/` |

Or `git clone` it directly into one of those paths.

## Use

Open a new session in your project and type:

```
/multiphase-project Build a real-time analytics dashboard for the growth team
```

The skill will:

1. Run `/brainstorming` with you to identify phases.
2. Pause for you to approve / amend the phase plan.
3. For each phase:
   a. Decompose into discrete works.
   b. Invoke `/prd` to groom the phase.
   c. Invoke `/ralph-loop` with a phase-specific completion promise.
   d. Invoke `/adversarial-pr-review`, loop fix-and-review until zero CRITICAL and zero HIGH findings.
   e. Mark the phase complete and advance.

To resume:

```
/multiphase-project resume
```

To check status:

```
/multiphase-project status
```

## What's in the box

```
multiphase-project-skill/
├── SKILL.md                          # the always-loaded entry point
├── README.md                         # this file
├── LICENSE                           # MIT
├── install.sh                        # cross-platform installer
├── scripts/
│   └── state_manager.py              # state.yaml CRUD + validation
├── references/
│   ├── workflow.md                   # end-to-end walkthrough
│   ├── state-schema.md               # full state.yaml schema + status values
│   └── stop-conditions.md            # how to evaluate ralph + review exits
└── assets/
    └── state-template.yaml           # empty starter for state.yaml
```

## Dependencies

This skill is a thin orchestrator. It does not implement brainstorming, PRD
generation, ralph looping, or PR review — it delegates to existing slash
commands. You must have these installed:

| Command | Where it typically lives |
|---|---|
| `/brainstorming` | superpowers plugin |
| `/prd` | ralph-skills plugin (or any PRD-generating skill) |
| `/ralph-loop` | ralph-loop / ralph-wiggum plugin |
| `/adversarial-pr-review` | moo-skills (`skills/adversarial-pr-review/`) or equivalent |

If your environment names them differently, edit the `## Workflow` section in
`SKILL.md` to match — the orchestrator's logic is platform-agnostic, only the
command names are.

Python ≥ 3.9 with `PyYAML` is required for `state_manager.py`. Install with:

```bash
pip install pyyaml
```

## License

MIT. See `LICENSE`.
