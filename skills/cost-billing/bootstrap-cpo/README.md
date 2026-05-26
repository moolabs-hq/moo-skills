# /cost-billing-bootstrap-cpo

**Stage 2 of 4. Runs on the CPO's machine. Consumes Stage 1 output.**

Asks the CPO about org-level product context, top features, and product structure (multi-product split if any). Outputs `02-cpo.signed.yaml` which lists products and assigns each to a team PM.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"CPO bootstrap"`
- `"product strategy bootstrap"`
- `"stage 2 bootstrap"`
- `"cost-billing CPO stage"`

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
cp -R bootstrap-cpo ~/.claude/skills/cost-billing-bootstrap-cpo
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
