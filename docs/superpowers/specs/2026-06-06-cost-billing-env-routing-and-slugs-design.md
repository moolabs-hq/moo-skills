# Cost-billing env-routing + event-slug constants — design

**Date**: 2026-06-06
**Status**: Draft — awaiting user review before transitioning to implementation plan
**Authors**: brainstorm session (kritivasas.shukla + Claude Opus 4.7)
**Branch**: `main` (post-PR #2 v0.3 migration)
**Skills touched**: `cost-billing-discovery`, `cost-billing-instrument`, `cost-billing-bootstrap-team-engineer`
**Working title**: cost-billing-env-routing-and-slugs (suite-wide change, not a new top-level skill)

---

## Problem statement

After the v0.3.0-rc1 migration (PR #2), the cost-billing suite produces helper modules and per-callsite inserts that work end-to-end against the unified-ingest SDK. Two gaps remain:

1. **The helper bypasses the customer's existing config layer.** The generated `moolabs_client.{py,ts,go}` resolves `MOOLABS_API_KEY` via direct `os.environ` reads (or strategy-branched fetches against AWS Secrets Manager / Vault / etc. declared in bootstrap Q14). Every real customer already has its own env-loading pattern — pydantic-settings, dotenv, viper, kelseyhightower/envconfig, zod env schemas — and the codemod's emission ignores it. Result: two parallel config patterns in one repo, drift over time, manual post-codemod wiring that customers forget. Production billing silently misfires when the key isn't wired into the same deployment surface (Terraform variable, k8s Secret, docker-compose env block, `.env.example`) the rest of the app uses.

2. **Event slugs are inline string literals scattered across callsites.** Every emission renders `event_type="checkout.recommendation.delivered"`, `meter_slug="checkout.recommendation.delivered"`, `provider="openai"` as literals. Renames require N-file edits; typos silently produce wrong-bucket events; multi-product customers can't see at a glance which slugs belong to which product.

A new skill (suite-wide change spanning three existing skills, not a new top-level skill) closes both gaps with one repo scan: discover the customer's env-routing patterns AND the existing slug usage, then emit appropriately-shaped artifacts as part of the codemod PR.

---

## Decisions resolved in brainstorm

| # | Question | Decision |
|---|---|---|
| 1 | Failure mode driving this | All three modes are the same scan: confirm/override Q14 declaration, integrate into customer's existing config, modify deployment surfaces. Plus a NEW concern: generate an event-slug constants module so callsites reference named constants instead of string literals. |
| 2 | Structural fit | **Extend `cost-billing-discovery` for the scan; extend `cost-billing-instrument` for emission.** No new top-level skill. Matches existing discovery-vs-emission separation. Plus one new question in `bootstrap-team-engineer`. |
| 3 | Slugs categories + origin | **Auto-derived from inventories, per-product split.** Constants module is regenerable; engineer never hand-edits. One module per product (multi-product customers get N modules). Categories: EVENT_TYPE, METER_SLUG, FEATURE_KEY, PROVIDER, SPAN_TYPE. |
| 4 | Unknown env-loader-pattern fallback | **Generate a stub Settings class + ask engineer to merge.** No refuse-to-run. PR comment: "No existing config layer detected; merge this stub into your pattern, or accept as-is." |
| 5 | Monorepo granularity (services × products) | **Engineer declares granularity in a new bootstrap question.** Per-service / repo-wide / hybrid / TBD. Slugs always per-product. |

---

## Architecture

### Discovery side — two new inventory files

Two new scripts in `cost-billing/discovery/scripts/`:

1. **`env_loader_scan.py`** → `.moolabs/customer-context/env-routing-inventory.yaml`
2. **`slug_inventory.py`** → `.moolabs/customer-context/slug-inventory.yaml`

### Bootstrap-team-engineer — one new question

New Q14b (placement near Q14 SDK key location): repo-shape declaration. The answer flows into `04-final.signed.yaml` under `integration.env_loader_granularity` and is read by `env_loader_scan.py`.

### Instrument side — one new phase + new templates

New Phase 1.7 (between attribution-discovery and task_planner), driven by `instrument/scripts/config_wire.py`. Reads both new inventories. Plans:
- Per-service env-wiring (modify customer's existing env-loader OR emit stub Settings class)
- Per-service deployment-stub plan (.env.example entry, Terraform variable + tfvars stub, k8s Secret manifest stub)
- Per-product slugs module

New Jinja templates in `instrument/assets/codemod-templates/`:
- `slugs-python.j2`, `slugs-typescript.j2`, `slugs-go.j2`

Existing helper + callsite templates updated to consume the new artifacts (helper reads via `get_settings()` instead of direct `os.environ`; callsites import slug constants instead of inlining literals).

### Shared assets — one new catalog

New `shared/assets/env-loader-patterns.yaml` — catalog of recognized env-loading patterns per language. Extensible (same model as `discovery/assets/provider-catalog.starter.yaml`).

---

## Discovery side — detailed

### `env_loader_scan.py`

**Inputs**:
- Customer repo root (from `bootstrap-team-engineer` Q1)
- `04-final.signed.yaml > integration.env_loader_granularity` (NEW field; default `TBD` if absent)
- `04-final.signed.yaml > integration.services` (existing — list of services in scope)
- `shared/assets/env-loader-patterns.yaml` (NEW — pattern catalog)

**Pattern catalog** (one-line summary; full catalog in the asset file):

| Language | Pattern | Detection signal | Wire target |
|---|---|---|---|
| Python | pydantic-settings BaseSettings | `class \w+\(BaseSettings\)` + `from pydantic_settings` import | add `moolabs_api_key: SecretStr` field |
| Python | pydantic v1 BaseSettings | `from pydantic import BaseSettings` | add field |
| Python | python-decouple | `from decouple import config` | add `MOOLABS_API_KEY = config(...)` line |
| Python | dotenv + raw os.getenv | `load_dotenv()` + `os.getenv(...)` patterns | add `os.getenv("MOOLABS_API_KEY")` to existing config module |
| TypeScript | zod env schema | `z.object({` in `env.ts`/`config.ts` | add to schema |
| TypeScript | process.env direct | many `process.env.\w+` reads in config module | add to existing config |
| TypeScript | env-var library | `import 'env-var'` | add `.get("MOOLABS_API_KEY")` |
| Go | viper | `viper.SetEnvPrefix` or `viper.AutomaticEnv` | add `viper.BindEnv("MOOLABS_API_KEY")` |
| Go | kelseyhightower/envconfig | struct tags `envconfig:"X"` | add struct field |
| Go | raw os.Getenv | `os.Getenv(...)` reads in config.go | add to existing config struct |

**Deployment-surface scan** (always, independent of language):
- Terraform: any `*.tf` with `variable {}` blocks → flag for variable + tfvars stub
- Kubernetes: `kind: Deployment` with `envFrom: secretRef` → flag for Secret manifest stub
- docker-compose: `environment:` blocks → flag for entry
- `.env.example` / `.env.sample`: detect for line append
- Dockerfile ENV: detect; recommend AGAINST baking the key (CHECKLIST-only emission)

**Granularity behavior** (respects bootstrap declaration):
- `per-service`: scan each service root independently, one inventory entry per service
- `repo-wide`: scan only the shared config package path declared in bootstrap
- `hybrid`: per-service for declared independents, repo-wide for declared shared
- `TBD`: best-effort scan; inventory carries `granularity_source: "default-fallback"` flag for adversarial-review visibility

**Output schema** (`env-routing-inventory.yaml`):

```yaml
generated_at: "2026-06-06T..."
granularity: per-service        # from bootstrap declaration
granularity_source: declared    # declared | default-fallback
services:
  - service_slug: payments-api
    app_config:
      pattern: pydantic-settings  # or "unrecognized"
      file: services/payments-api/app/config.py
      class_name: Settings
      line_to_insert: 47           # last field of the class
      confidence: high             # high | medium | low
      evidence:
        - "from pydantic_settings import BaseSettings at line 3"
      stub_required: false         # true if pattern=unrecognized or confidence=low
    deployment_surfaces:
      - kind: terraform
        path: infra/terraform/payments-api/variables.tf
        insert_kind: variable_block_append
      - kind: k8s
        path: infra/k8s/payments-api/deployment.yaml
        insert_kind: secret_ref_checklist
      - kind: dotenv_example
        path: services/payments-api/.env.example
        insert_kind: line_append
```

### `slug_inventory.py`

**Inputs**:
- `.moolabs/inventory/cost-events-inventory.yaml`
- `.moolabs/inventory/usage-events-inventory.yaml`
- `.moolabs/inventory/output-input-map.yaml`
- `discovery/assets/provider-catalog.starter.yaml` (PROVIDER values)
- CPO-stage signed YAML (product list)
- Future: a canonical `shared/assets/span-type-catalog.yaml` for SPAN_TYPE values (out of scope for v1 — derived from `cost_kind` values found in inventory)

**Naming convention**: `UPPER_SNAKE_CASE` derived as `slug.value.replace(".", "_").replace("-", "_").upper()`.

**Output schema** (`slug-inventory.yaml`):

```yaml
generated_at: "2026-06-06T..."
products:
  - product_slug: billing
    constants:
      EVENT_TYPE:
        - name: CHECKOUT_RECOMMENDATION_DELIVERED
          value: "checkout.recommendation.delivered"
        - name: SEAT_ASSIGNED
          value: "seat.assigned"
      METER_SLUG:
        - name: CHECKOUT_RECOMMENDATION_DELIVERED
          value: "checkout.recommendation.delivered"
      FEATURE_KEY:
        - name: RECOMMENDATION
          value: "recommendation"
        - name: SEAT
          value: "seat"
      PROVIDER:
        - name: OPENAI
          value: "openai"
        - name: ANTHROPIC
          value: "anthropic"
      SPAN_TYPE:
        - name: LLM_TOKENS
          value: "llm-tokens"
        - name: GPU_SECONDS
          value: "gpu-seconds"
```

**Collision handling**:
- Same NAME in same category = CRITICAL error (duplicate `workflow_id` in inventory). Refuse-to-run.
- Same VALUE across different categories (e.g. EVENT_TYPE_X.value == METER_SLUG_X.value) = EXPECTED. Both constants emitted; values are equal.

---

## Bootstrap-team-engineer changes

**One new question** (Q14b — placement near Q14 SDK key location):

> "How is env-loading wired in your repo?
> - **Per-service** — each service has its own config code (e.g. one pydantic-settings class per service)
> - **Repo-wide** — shared config package every service imports from (e.g. `packages/config/`)
> - **Hybrid** — some services share, others have their own. (You'll be asked to name which.)
> - **TBD** — let the scanner detect best-effort"

If `hybrid`: follow-up asking for the list of services that use the shared package.

The answer is written to `04-final.signed.yaml > integration.env_loader_granularity` (plus `integration.shared_config_path` when hybrid/repo-wide).

---

## Instrument side — detailed

### `config_wire.py` (Phase 1.7)

**Inputs**:
- `env-routing-inventory.yaml`
- `slug-inventory.yaml`
- existing inventories (cost-events, usage-events, output-input-map, attribution-bindings)
- `04-final.signed.yaml`

**Outputs** (carried in `tasks.yaml` consumed by Phase 2c):
- One **env-wire task** per service: either MODIFY-IN-PLACE plan (with file:line and the field/expression to insert) OR STUB plan (new file with the minimal Settings class)
- One **deployment-stubs task** per service: list of stub files to emit (Terraform `.tf` file, k8s Secret manifest, `.env.example` append) and CHECKLIST items for files-not-to-modify (existing Deployment yaml, Dockerfile)
- One **slugs-module task** per product: the per-product slugs module rendering plan

### Env-wiring emission

**Recognized pattern (high/medium confidence)** — modify the customer's existing env-loader in place via AST rewrite (Python: `libcst`; TypeScript: `ts-morph`; Go: `goast`). Same agent-driven-today / deterministic-tomorrow split as the rest of the codemod.

Example (pydantic-settings):

```python
class Settings(BaseSettings):
    database_url: str
    redis_url: str
    moolabs_api_key: SecretStr = Field(..., env="MOOLABS_API_KEY")   # ← inserted
```

The helper template's `_resolve_api_key()` is regenerated to read via the customer's settings:

```python
from app.config import get_settings

@lru_cache(maxsize=1)
def _resolve_api_key() -> str:
    return get_settings().moolabs_api_key.get_secret_value()
```

The v0.2-era strategy branches (boto3 / hvac / google.cloud.secretmanager) collapse into the customer's settings class. Customers using Vault for their other secrets already have their Settings class configured to pull from Vault on construction — the helper just reads `settings.moolabs_api_key`.

**Stub-required (unrecognized OR low confidence OR AST-rewrite failure)** — emit a new file `app/services/moolabs_settings.py` with a minimal Settings class for `MOOLABS_API_KEY` only. The helper template renders the same `get_settings()` path, importing from the stub. PR comment surfaces this prominently.

### Deployment-surface stubs (both branches)

- `.env.example`: **always** append a `MOOLABS_API_KEY=` line.
- Terraform: emit a NEW `moolabs.tf` under the service's infra directory with `variable "moolabs_api_key"` + a stub `aws_ssm_parameter` (or equivalent). NEVER modify existing `.tf` files.
- k8s: emit a NEW `secret-moolabs.yaml` with a Secret manifest stub. NEVER modify existing Deployment yamls — emit a CHECKLIST in the PR with the `envFrom` snippet to add.
- docker-compose: append to `environment:` if the service block is detected; else CHECKLIST.
- Dockerfile: never modify. CHECKLIST only if the Dockerfile contains ENV lines that suggest baked-in secrets (security smell, surface via adversarial review).

### Slugs module generation

**Three new Jinja templates** in `instrument/assets/codemod-templates/`:
- `slugs-python.j2` → `app/services/moolabs/slugs_{product_slug}.py`
- `slugs-typescript.j2` → `src/services/moolabs/slugs_{product_slug}.ts`
- `slugs-go.j2` → `internal/moolabsclient/slugs_{product_slug}.go`

**Python module shape** (TypeScript and Go follow the same pattern with idiomatic translations):

```python
"""Moolabs event slugs for product: billing.

DO NOT EDIT — regenerated by /cost-billing-instrument from
cost-billing-discovery's slug-inventory.yaml.
Source: .moolabs/customer-context/slug-inventory.yaml @ {{ generated_at }}
"""

# EVENT_TYPE (per-feature canonical event identifiers)
EVENT_TYPE_CHECKOUT_RECOMMENDATION_DELIVERED = "checkout.recommendation.delivered"
EVENT_TYPE_SEAT_ASSIGNED = "seat.assigned"

# METER_SLUG (per-feature billing routing keys)
METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED = "checkout.recommendation.delivered"

# FEATURE_KEY (per-feature short identifiers)
FEATURE_KEY_RECOMMENDATION = "recommendation"
FEATURE_KEY_SEAT = "seat"

# PROVIDER (recognized vendor identifiers — from provider-catalog)
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"

# SPAN_TYPE (canonical span-kind identifiers)
SPAN_TYPE_LLM_TOKENS = "llm-tokens"
SPAN_TYPE_GPU_SECONDS = "gpu-seconds"
```

### Callsite templates updated to reference slugs

Existing framework callsite templates (`python-fastapi.j2`, `typescript-express.j2`, `python-django.j2`, etc.) get a small update: instead of inlining `event_type="checkout.recommendation.delivered"`, they emit:

```python
from app.services.moolabs.slugs_billing import (
    EVENT_TYPE_CHECKOUT_RECOMMENDATION_DELIVERED,
    METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED,
)

emit_event_safe(
    event_type=EVENT_TYPE_CHECKOUT_RECOMMENDATION_DELIVERED,
    meter_slug=METER_SLUG_CHECKOUT_RECOMMENDATION_DELIVERED,
    ...
)
```

The product-slug is resolved per-task by `task_planner.py` — the task already carries the feature → product mapping from the CPO/team-product bootstrap chain.

### PR structure

The codemod PR gains new commits, ordered for reviewability:

1. `feat(moolabs): add per-product slugs module` (one per product)
2. `feat(moolabs): wire MOOLABS_API_KEY into <service> config` (one per service — modification OR stub)
3. `feat(moolabs): deployment stubs for MOOLABS_API_KEY` (Terraform `.tf`, k8s Secret, `.env.example` — bundled per service)
4. `feat(moolabs): generate per-service emission helper` (existing — now reads from customer's settings)
5. `feat(moolabs): instrument <file>` (existing per-file — now imports from slugs module)

---

## Data flow

```
bootstrap-team-engineer
  └─ NEW Q14b: env-loading granularity
      └─ written to 04-final.signed.yaml > integration.env_loader_granularity

         cost-billing-discovery
         ├─ existing: cost-events-inventory.yaml + usage-events-inventory.yaml
         │            + output-input-map.yaml + attribution-bindings.yaml
         ├─ NEW Phase: env_loader_scan.py
         │     reads: customer repo + integration.env_loader_granularity
         │            + integration.services
         │     uses:  shared/assets/env-loader-patterns.yaml (catalog)
         │     emits: env-routing-inventory.yaml
         └─ NEW Phase: slug_inventory.py
               reads: cost-events-inventory.yaml + usage-events-inventory.yaml
                      + output-input-map.yaml + provider-catalog.starter.yaml
                      + CPO bootstrap's product list
               emits: slug-inventory.yaml

         cost-billing-instrument
         ├─ existing Phase 1.5: sdk_snapshot.py (unchanged)
         ├─ existing Phase 1.6: attribution_discovery.py (unchanged)
         ├─ NEW Phase 1.7: config_wire.py
         │     reads: env-routing-inventory.yaml + slug-inventory.yaml
         │     plans:
         │       - per-service env-wiring (MODIFY-IN-PLACE OR STUB)
         │       - per-service deployment-stubs (.env.example, Terraform, k8s)
         │       - per-product slugs module
         └─ Phase 2c: task_planner.py
               consumes Phase 1.7's plans; carries them as task entries in tasks.yaml
```

---

## Error handling

| Failure | Detection | Behavior | UX |
|---|---|---|---|
| Bootstrap question never answered | `integration.env_loader_granularity` missing | `env_loader_scan.py` defaults to `TBD` (best-effort) | Inventory `granularity_source: "default-fallback"` flag for adversarial-review visibility |
| Scanner detects pattern at low confidence (<0.5) | `confidence` field | Treated as `stub_required: true` → stub branch | PR comment names the low confidence and the partial evidence |
| Multiple conflicting patterns in same service | E.g. both pydantic-settings AND dotenv calls | Pick highest-confidence one, emit CHECKLIST naming the conflict | Adversarial review (Skill R) flags as MEDIUM |
| Bootstrap declared `per-service` but scanner finds shared package | Granularity mismatch | Honor the declaration; emit per-service entries that all point at the shared package's file | AST rewriter de-dups by file:line (idempotent) |
| Slug constant NAME collision in same category | Two features with same workflow_id | `slug_inventory.py` refuses-to-run with CRITICAL: "duplicate slug name X in product Y category Z" | Engineer fixes the source data |
| In-place AST rewrite fails | libcst/ts-morph/goast error on target file | Fall back to stub branch + CHECKLIST with intended diff | Customer sees stub + "we couldn't rewrite your existing file because <reason>" |
| Customer's settings class is in unusual location (e.g. factory pattern, no class) | Scanner returns `pattern: factory-function` → no AST insertion point | Stub branch | Same as low-confidence |
| Helper template references `get_settings()` but app isn't running in context that supports it | Helper's `lru_cache(maxsize=1)` lazy load fails on first call | Failure surfaces via existing env-gated rail (SDK_DEVELOPMENT → raise; prod → log) | Same as existing helper's lazy-resolution UX |

---

## Testing

### Unit-test coverage

In `discovery/scripts/test_*.py` and `instrument/scripts/test_*.py` (matches existing pattern):

- **`test_env_loader_scan.py`**: pattern-recognition fixtures per language × pattern (one minimal repo per pattern in `tests/fixtures/env-loader-shapes/`). Assert:
  - Each recognized pattern produces expected file:line + confidence
  - Unrecognized layouts produce `stub_required: true`
  - Conflicting patterns are detected and the high-confidence one wins
- **`test_slug_inventory.py`**: golden-file tests. Feed a fixture inventory, assert the slug yaml matches a checked-in expected output. Tests for duplicate-name detection.
- **`test_config_wire.py`**:
  - Input: env-routing-inventory + slug-inventory
  - Output: tasks added to tasks.yaml. Assert shape (one env-wire task per service, one slugs task per product, deployment-stub tasks attached)
  - Assert PR-structure ordering survives

### Smoke-test coverage (Phase 7 in `scripts/test-suite.sh`)

- **New helper template renders**: assert `_resolve_api_key()` reads via `get_settings()` (or `process.env` for TS, or viper for Go) instead of direct `os.environ`. Negative-leakage: assert OLD strategy-branched `boto3.client("secretsmanager")` paths are gone (or behind explicit "legacy-direct-resolve" Jinja gate).
- **New slugs templates render**: assert per-product modules contain expected constant names from fixture inventory.
- **New callsite templates render**: assert imports from `slugs_<product>` are present and string-literal event_types are GONE.

### Adversarial-regression assertions (Phase 7 negative-leakage list extended)

- No `os.environ["MOOLABS_API_KEY"]` in rendered helper (post-Phase 1.7) — should go through settings layer.
- No string-literal `"checkout.recommendation.delivered"` in rendered callsite (post-Phase 1.7) — should use slug constant.
- No bare `process.env.MOOLABS_API_KEY` in TS helper.

### End-to-end fixture

New `examples/customer-fixture-env-routing/`:
- Minimal customer-repo fixture with a pydantic-settings Settings class, `.env.example`, `infra/terraform/variables.tf`
- Run full discovery → instrument pipeline against it (via `--dry-run`)
- Assert expected diffs

---

## Out of scope (deferred to v2 or separate skills)

- **Drift-lint integration**: detecting that a customer has hand-edited an auto-generated slugs module (which we said is DO NOT EDIT). Could be added to `cost-billing-drift-lint` as a sibling check. Not in this scope.
- **Span-type catalog**: a canonical `shared/assets/span-type-catalog.yaml` similar to `provider-catalog.starter.yaml`. v1 derives SPAN_TYPE values from `cost_kind` fields found in cost-events-inventory; v2 could add a curated catalog.
- **Slug constants for OTHER value categories**: WORKFLOW_ID, EVENT_TYPE_SUBSCRIPTION, etc. Could expand the categories enum later; v1 covers the five the user named.
- **Cross-product references**: a feature in product A that emits events under product B's slugs. Multi-product customers may need this; v1 assumes 1:1 feature-to-product mapping (which matches the CPO bootstrap's data model today).
- **Customer-extensions block in slugs module**: a hand-curated section the engineer maintains alongside the auto-generated one. We chose Option 3 (auto-derived per-product) explicitly to avoid this complexity.
- **Refactor the existing v0.2-era strategy-branched `_resolve_api_key`**: the AWS-SecretsManager / Vault / GCP branches in the helper template. These collapse naturally when the customer's settings class is used as the resolution path. Cleanup is a follow-up.

---

## Open questions (none blocking)

- **Exact placement of Q14b in bootstrap-team-engineer**: between Q14 and Q15? Or its own section? Implementation plan decides.
- **AST-rewrite library choice for first-implementation pass**: agent-driven today per the codemod's general design. Deterministic AST rewriters (`libcst`, `ts-morph`, `goast`) are the roadmap but not in v1 of this skill.
- **Migration story for existing v0.3 customers**: customers already running on the PR #2 helpers will have a `_resolve_api_key` reading `os.environ` directly. Re-running the codemod after this skill ships should migrate them to the new settings-based path; the smoke's negative-leakage assertion ensures regressions are caught. Implementation plan to confirm.

---

## Implementation phasing (preview — full plan in writing-plans output)

1. **Phase A — Discovery scan + bootstrap question**
   - Add `env_loader_scan.py`, `slug_inventory.py`
   - Add Q14b to bootstrap-team-engineer
   - Add `shared/assets/env-loader-patterns.yaml`
   - Unit tests for both scripts
   - Discovery SKILL.md update
2. **Phase B — Instrument emission (env-wire side)**
   - Add `config_wire.py`
   - Update helper templates (Python/TS/Go) for `get_settings()` path
   - Smoke assertions for new helper shape
   - Instrument SKILL.md update for Phase 1.7
3. **Phase C — Instrument emission (slugs side)**
   - Add `slugs-{python,typescript,go}.j2` templates
   - Update framework callsite templates to import slug constants
   - Smoke assertions for slugs module + callsite imports
4. **Phase D — End-to-end fixture + adversarial-review tuning**
   - `examples/customer-fixture-env-routing/`
   - Adversarial-review skill update for new finding categories
   - Final smoke at 60+N/60+N (count grows by the new assertions)

Each phase ships independently — Phase A alone produces useful inventories; Phase B+C deliver the customer-visible change; Phase D hardens the regression fence.
