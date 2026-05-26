# /cost-billing-discovery

**Post-chain. Scans the customer's repo to identify cost/usage emission sites.**

Phase 1.5 snapshots the actual Moolabs SDK; Phase 1.6 walks the developer through confirming attribution-source bindings; Phase 4 emits the three reviewable inventories (cost-events, usage-events, output-input map). All driven by the signed chain handoff YAMLs.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"discover ingest events"`
- `"find where to emit cost/usage events"`
- `"scan repo for Moolabs instrumentation"`
- `"build chargeability map"`
- `"Skill A"`

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
cp -R discovery ~/.claude/skills/cost-billing-discovery
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
