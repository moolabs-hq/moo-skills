# /cost-billing-bootstrap-finance

**Stage 1 of 4 in the bootstrap chain. Runs on the CFO's machine.**

Interactively walks the CFO through pricing model, billable units (with classification — customer-facing vs vendor-COGS vs sibling-pair), fair-usage thresholds, compliance regimes, PII blocklists, regions, and multi-tenant shape. Adversarial-reviews the draft before signoff. Outputs `01-finance.signed.yaml`.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"finance bootstrap"`
- `"CFO bootstrap"`
- `"cost-billing finance stage"`
- `"stage 1 bootstrap"`
- `"pricing model questionnaire"`

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
cp -R bootstrap-finance ~/.claude/skills/cost-billing-bootstrap-finance
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
