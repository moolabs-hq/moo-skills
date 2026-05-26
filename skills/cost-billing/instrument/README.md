# /cost-billing-instrument

**Post-discovery codemod. Wires SDK calls into customer code via per-file subagent fan-out.**

Reads the signed chain + inventories + SDK snapshot, builds `tasks.yaml` (one task per file), dispatches each task to a focused subagent. Per-service `moolabs_client.py` helper is generated FIRST; per-callsite inserts route exclusively through that helper. Six framework templates (Python: FastAPI/Django/Flask; TypeScript: Express/NestJS/Next.js).

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"run the codemod"`
- `"instrument this repo"`
- `"wire SDK calls"`
- `"Skill 2"`

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
cp -R instrument ~/.claude/skills/cost-billing-instrument
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
