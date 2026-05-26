# /cost-billing-cloud-bill

**Customer-cloud-bill plumbing (AWS/GCP/Azure → Moolabs attribution backend).**

Audits cloud-tag propagation. Surfaces untagged spend as cell ③ findings (CFO review surface). Communicates the unavoidable 24–48h floor before first usable data after wiring.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"wire cloud bills"`
- `"set up AWS CUR"`
- `"GCP billing export"`
- `"Azure cost management export"`
- `"Skill B"`
- `"audit tag propagation"`

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
cp -R cloud-bill ~/.claude/skills/cost-billing-cloud-bill
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
