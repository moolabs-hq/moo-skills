# /cost-billing-drift-lint

**Continuous CI gate. Flags drift between inventory and emitted code on every PR.**

Watches for: SDK calls that no longer have a matching inventory entry; inventory entries that no longer have a matching SDK call; attribution-binding drift; framework adapter mismatches. Reports inline PR annotations.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"drift lint"`
- `"Skill 3"`
- `"check for inventory drift"`
- `"CI for SDK coverage"`

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
cp -R drift-lint ~/.claude/skills/cost-billing-drift-lint
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
