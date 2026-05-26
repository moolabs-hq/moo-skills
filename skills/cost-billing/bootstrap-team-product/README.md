# /cost-billing-bootstrap-team-product

**Stage 3 of 4. Runs on each product manager's machine. Consumes Stages 1+2.**

Per-product PM drilldown — billable unit per feature, output↔input map at the conceptual level, event_type naming. Outputs `03-team-product-<product>.signed.yaml` (one per product).

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"team product bootstrap"`
- `"team PM bootstrap"`
- `"stage 3 bootstrap"`
- `"per-feature bootstrap"`

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
cp -R bootstrap-team-product ~/.claude/skills/cost-billing-bootstrap-team-product
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
