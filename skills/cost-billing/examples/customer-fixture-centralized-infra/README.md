# customer-fixture-centralized-infra

Regression fixture for a prior fix's root cause — the env_loader_scan was
scoped to `services/<svc>/` and never saw centralized infra at the repo
root. moolabs (the canonical example) keeps Terraform in
`infrastructure/terraform/{modules,environments,regional,global,accounts}/`
with NO per-service infra dirs. Every service inherits the shared
modules.

## Shape

```
customer-repo/
├── services/
│   └── moo-arc/
│       ├── app/settings.py             # pydantic-settings BaseSettings
│       ├── app/services/checkout.py
│       ├── .env.example                # service-scope surface
│       ├── Dockerfile                  # service-scope surface
│       └── docker-compose.yml          # service-scope surface
└── infrastructure/                     # CENTRALIZED — repo-scope
    └── terraform/
        ├── modules/
        │   └── secrets/
        │       └── variables.tf        # repo-scope terraform surface
        └── regional/
            └── variables.tf            # repo-scope terraform surface
```

## What the scanner MUST detect

Running `_service_entry` against this fixture for `service_slug=moo-arc`
must return `deployment_surfaces` containing:

| kind | path | scope |
|---|---|---|
| dotenv_example | `.env.example` (service-relative) | service |
| dockerfile | `Dockerfile` (service-relative) | service |
| docker-compose | `docker-compose.yml` (service-relative) | service |
| terraform | `infrastructure/terraform/modules/secrets/variables.tf` | repo |
| terraform | `infrastructure/terraform/regional/variables.tf` | repo |

And `infra_discovery_gap` MUST be `false` (terraform + dockerfile found).

## What the instrument MUST do

For each repo-scope terraform surface: emit `mode=checklist_only`
(NEVER auto-create a `moolabs.tf` in `infrastructure/terraform/modules/
secrets/` — that file is shared by every service and modifying it has
cross-service blast radius).

For each service-scope surface: auto-emit normally (`moolabs.tf`
alongside service-scope `variables.tf`, line-append to
`.env.example`, etc).
