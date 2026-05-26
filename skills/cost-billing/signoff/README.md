# /cost-billing-signoff

**State-aware orchestrator for the three-role review workflow.**

Reads `.moolabs/inventory/reviews/` to figure out which signoff is next. Dispatches to the right persona flow, opens the right HTML projection, runs the adversarial review, persona accepts/risk-accepts/rejects, writes the signed YAML. Refuses to advance past blocked verdicts.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"signoff"`
- `"review the inventory"`
- `"approve inventories"`
- `"three-role review"`
- `"stage signoff"`
- `"PM review"`

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
cp -R signoff ~/.claude/skills/cost-billing-signoff
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
