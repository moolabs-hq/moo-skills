# customer-fixture-env-routing

End-to-end fixture for the env-routing + slugs migration (spec
`2026-06-06-cost-billing-env-routing-and-slugs-design.md`).

## What's here

- `customer-repo/` — a minimal customer codebase shape:
  - `app/settings.py` — pydantic-settings `BaseSettings` with
    `moolabs_api_key: SecretStr` (the pattern env_loader_scan
    recognizes as `python-pydantic-settings-v2`)
  - `app/services/checkout.py` — emit callsite for
    `checkout.recommendation.delivered`
  - `app/services/seat_assignment.py` — emit callsite for
    `seat.assigned`
  - `.env.example`, `infra/terraform/variables.tf` — deployment
    surfaces the codemod expects to find

- `inventories/` — pre-computed Phase A outputs that would result
  from running discovery against the customer-repo above. Hand-curated
  so the e2e test doesn't need to actually run discovery (which would
  require library installs).

- `customer-context/` — the `04-final.signed.yaml` that Phase 2's
  task_planner expects.

## How it's used

`instrument/scripts/test_e2e_phase_d.py` runs `task_planner.py` against
this fixture and asserts the resulting `tasks.yaml` contains correctly-
shaped `env_wire_tasks:` and `slugs_emit_tasks:` blocks. This catches
CLI-integration regressions that unit tests miss.

## Regenerating

If the discovery layer changes, regenerate inventories by running
Phase A's scripts against `customer-repo/`:

```bash
python skills/cost-billing/discovery/scripts/env_loader_scan.py \
    --service-root customer-repo \
    --output inventories/env-routing-inventory.yaml

python skills/cost-billing/discovery/scripts/slug_inventory.py \
    --cost-events inventories/cost-events-inventory.yaml \
    --usage-events inventories/usage-events-inventory.yaml \
    --output inventories/slug-inventory.yaml
```

Hand-curate `attribution-bindings.yaml`, `cost-events-inventory.yaml`,
`usage-events-inventory.yaml`, and `04-final.signed.yaml` to match.
