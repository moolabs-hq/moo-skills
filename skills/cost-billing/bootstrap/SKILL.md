---
name: cost-billing-bootstrap
description: >-
  DEPRECATED in v0.3.0. The single-machine bootstrap was correct in intent (great questions, real synthesis) but wrong in topology — it assumed finance + CPO + team-PM + engineer were all the same person on the same machine, which is wrong for real customer integrations. Replaced by a 4-silo chain where each persona runs THEIR OWN bootstrap on THEIR OWN machine and the signed doc travels between humans via email / Slack / Drive. New skills — cost-billing-bootstrap-finance (Stage 1), cost-billing-bootstrap-cpo (Stage 2), cost-billing-bootstrap-team-product (Stage 3), cost-billing-bootstrap-team-engineer (Stage 4). Invoking this skill prints a redirect notice. See cost-billing-shared/chain-handoff.md for the 4-silo workflow. Triggers on legacy invocations like "bootstrap the suite", "set up cost-billing", "configure the skills", "first run setup" — surfaces deprecation notice + redirects to the appropriate stage.
license: MIT
metadata:
  author: Moolabs
  version: 0.3.0-deprecated
  created: 2026-05-19
  last_reviewed: 2026-05-19
  status: deprecated
  replaced_by:
    - cost-billing-bootstrap-finance
    - cost-billing-bootstrap-cpo
    - cost-billing-bootstrap-team-product
    - cost-billing-bootstrap-team-engineer
---

# /cost-billing-bootstrap — DEPRECATED (replaced by the 4-silo chain)

You exist for one reason: print a clear deprecation notice when a customer invokes the old skill name, and redirect them to the right per-persona skill.

## Trigger

Anything that would have invoked the old single-machine bootstrap:
- `/cost-billing-bootstrap`
- "bootstrap the suite"
- "set up cost-billing"
- "configure the cost-billing skills"

## What you do — print this and stop

```
─────────────────────────────────────────────────────────────────────
  /cost-billing-bootstrap is DEPRECATED in v0.3.0
─────────────────────────────────────────────────────────────────────

The single-machine bootstrap was based on an incorrect assumption: that
finance, CPO, team-PM, and the IC engineer are all the same person.

In real customer integrations, those are 4 different humans on 4
different machines. The new chain workflow is:

  Stage 1 — Finance     →  Stage 2 — CPO     →  Stage 3 — Team-PM    →  Stage 4 — Engineer
  (CFO/finance lead)       (product strategy)   (per-feature drilldown)  (technical)

Each stage runs its OWN bootstrap, produces a signed YAML, and hands off
to the next persona via email / Slack / Drive (the customer's choice).

WHICH STAGE ARE YOU?

  Finance / CFO          →  /cost-billing-bootstrap-finance
  CPO / Product strategy →  /cost-billing-bootstrap-cpo
                            (requires 01-finance.signed.yaml from finance)
  Team Product PM        →  /cost-billing-bootstrap-team-product
                            (requires 01 + 02)
  IC Engineer            →  /cost-billing-bootstrap-team-engineer
                            (requires 01 + 02 + 03)
  Solo founder / all     →  Run all 4 in sequence (install.sh --persona all)

FULL WORKFLOW DOC:  cost-billing-shared/chain-handoff.md

If you actually need the OLD single-machine flow (e.g., for a dogfood
smoke test on your own machine), use --persona all when installing the
suite and then run the 4 stages in sequence:

  /cost-billing-bootstrap-finance
  /cost-billing-bootstrap-cpo --input-from .moolabs/chain/01-finance.signed.yaml
  /cost-billing-bootstrap-team-product --input-from .moolabs/chain/01-finance.signed.yaml --input-from .moolabs/chain/02-cpo.signed.yaml
  /cost-billing-bootstrap-team-engineer (with all 3 --input-from)

The CONTENT of the questions hasn't changed — they were good, the user
loved them. Only the topology changed: 4 silos, 4 humans, 4 machines.
─────────────────────────────────────────────────────────────────────
```

After printing this, **stop**. Do not ask questions. Do not synthesize anything. Do not write `customer-context/`. The customer needs to invoke the right per-stage skill.

## What this skill MUST NOT do

- **Never** run the v0.2 question flow (it's deprecated for a reason).
- **Never** synthesize a customer-context/. The 4 new skills do that.
- **Never** delete an existing v0.2 customer-context/ if one is present from prior runs — leave it for the engineer's Stage 4 to migrate if needed.
- **Never** silently redirect. Print the deprecation notice so the customer understands the workflow change.

## Migration of existing v0.2 customer-context/

If `.moolabs/customer-context/` already exists from a v0.2 bootstrap run (like the great one in `/Users/kritivasas.shukla/code/personal/moolabs/moolabs/.moolabs/` from 2026-05-19), the v0.3 engineer stage (`/cost-billing-bootstrap-team-engineer`) has a `--migrate-from-v02 <path>` flag that ingests the existing v0.2 files and reverse-derives the 4 chain docs. This is OPT-IN — the customer chooses whether to re-do the chain interactively or import legacy.

See `cost-billing-shared/chain-handoff.md` §"v0.2 migration" for details.

## Reference

- `cost-billing-shared/chain-handoff.md` — the 4-silo workflow.
- `cost-billing-shared/v1-decisions-log.md` — why this changed in v0.3.
