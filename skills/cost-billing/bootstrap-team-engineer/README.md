# /cost-billing-bootstrap-team-engineer

**Stage 4 of 4 (FINAL). Runs on the IC engineer's machine. Consumes Stages 1+2+3.**

Per-service engineer drilldown — repo path, telemetry stack, MCP inventory, SDK key location, attribution sources. Outputs `04-final-<service>.signed.yaml` (one per service). This is the file every downstream skill reads.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"engineer bootstrap"`
- `"team engineer bootstrap"`
- `"stage 4 bootstrap"`
- `"final bootstrap"`

## In the larger suite

This skill is one of 11 in the Cost+Billing suite. See `../README.md` for the overall flow (CFO → CPO → per-product PM → per-service engineer → discovery → instrument), the architecture diagram, and how all 11 fit together.

## Install

Install commands assume you are at the suite root (`skills/cost-billing/` if you
cloned the parent `moo-skills` repo; the folder itself if you have the
`cost-billing/` tree standalone).

Install the whole suite (recommended — this skill depends on `shared/` and
`adversarial-review/`):

```bash
./install.sh
```

Or install just this skill standalone (you must also copy `shared/` — most
cross-skill references break without it):

```bash
cp -R bootstrap-team-engineer ~/.claude/skills/cost-billing-bootstrap-team-engineer
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
