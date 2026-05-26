# /cost-billing-adversarial-review

**Hostile review gate. Fires after every chain handoff + after the codemod.**

Cross-model 5-phase pattern (spec → adversarial pass → verify+fix → sibling search → stop). Six invocation points: post-discovery, post-cfo-stage1, post-pm-stage2 (per-product), post-engineer-stage3 (per-service), holistic-pre-codemod, post-codemod. CRITICAL/HIGH/MEDIUM/LOW severity rubric. 5-round cap.

## Trigger

Slash-invoke the skill, optionally with one of these natural phrasings:

- `"adversarial review"`
- `"Skill R"`
- `"review the CFO plan"`
- `"review the PM plan"`
- `"review the engineer spec"`
- `"hostile review of codemod PR"`

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
cp -R adversarial-review ~/.claude/skills/cost-billing-adversarial-review
cp -R shared ~/.claude/skills/cost-billing-shared
```

## License

MIT — see `../LICENSE`.
