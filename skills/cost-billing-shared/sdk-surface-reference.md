# SDK surface reference — grounded in `moolabs-hq/moolabs-{py,go,ts}` as of 2026-05-18

This is the **single source of truth** for what the customer-facing SDK actually looks like. Every other skill in the suite reads from this file rather than re-deriving it.

> Verified against: `gh api repos/moolabs-hq/moolabs-py/contents` (main branch, updated 2026-05-18T21:51:57Z) and parallel listings for `moolabs-go`, `moolabs-ts`.

---

## Customer-facing namespaces (the only two)

The `Moolabs` client exposes exactly **two** top-level namespaces. The requirements doc's Doc 3 §11 ("`client.meter.events.*` = billing events. `client.cls.*` = account/wallet/lifecycle. No third namespace today") is **confirmed** by the live README.

```python
from moolabs import Moolabs
client = Moolabs(api_key="moo_live_...")

# CLS / billing-side                      Meter / usage-side
client.cls.wallets.create_wallet(...)     client.meter.events.ingest_events([...])
client.cls.grants.list_grants(...)        client.meter.meters.list_meters()
client.cls.ledger.*                       client.meter.entitlements.check_entitlement(...)
client.cls.alerts.*                       client.meter.customers.*
client.cls.auto_topup.*                   client.meter.subscriptions.*
client.cls.rate_cards.*                   client.meter.billing.*
client.cls.rating.*                       client.meter.notifications.*
client.cls.fx_rates.*                     client.meter.apps.*
client.cls.portal.*                       client.meter.portal.*
client.cls.subscriptions.*                client.meter.product_catalog.*
                                          client.meter.subjects.*
```

Routing is internal (`api.moolabs.com` for `cls`, `meter.moolabs.com` for `meter`). The SDK handles it; the codemod does NOT need to wire base URLs per call.

---

## The acute SDK gap (Doc 3 §6.3, requirements §6 #25) — confirmed

**There is no `client.acute.*` namespace today.** Subscription customers (zero usage events) and any customer needing direct cost-event emission for non-AI infra spend cannot call the acute service via the unified SDK.

**However, the underlying API classes are already generated.** All three SDKs ship these acute-side classes alongside cls/meter:

| Generated API class (Python) | Generated API class (Go) | Generated API class (TS) | Purpose |
|---|---|---|---|
| `cost_events_api.py` | `api_cost_events.go` | `api/cost-events-api.ts` | Direct cost-event ingestion |
| `cloud_billing_api.py` | `api_cloud_billing.go` | `api/cloud-billing-api.ts` | Cloud bill imports (Tier 5) |
| `attribution_api.py` | `api_attribution.go` | `api/attribution-api.ts` | Attribution algorithm results |
| `acute_analytics_api.py` | `api_acute_analytics.go` | `api/acute-analytics-api.ts` | Cost analytics queries |
| `acute_integrations_api.py` | `api_acute_integrations.go` | `api/acute-integrations-api.ts` | Cloud-provider integration config |
| `allocation_rules_api.py` | `api_allocation_rules.go` | `api/allocation-rules-api.ts` | Cost allocation rules (4 ACUTE rules) |
| `bom_api.py` | `api_bom.go` | `api/bom-api.ts` | Bill of Materials |
| `budgets_api.py` | `api_budgets.go` | `api/budgets-api.ts` | Budget management |

These exist at the **API layer** but are not wired into `client.cls.*` / `client.meter.*`. The stitcher (`../moolabs/sdks/generator/scripts/stitch-specs.py`) is N-way and currently merges bff/meter/arc; adding acute to the stitcher is mechanical per Doc 3 §6.3 — "stitch acute openapi + add namespace + patch X-API-Key."

### v1 implications

Three patterns the codemod (Skill 2) must choose between, per `requirements §4.3`:

| Pattern | Today (v1, 2026-05-19) | After acute SDK lands |
|---|---|---|
| **Sibling-pair** (one site, both events) | Wire `client.meter.events.ingest_events()` for usage; emit cost as OTel span with Moolabs custom attributes (`moolabs.request.id`, `moolabs.trace.kind=cost`). Tier 2/4 ingest reads the spans. | Same usage call; replace OTel-only cost with `client.acute.events.ingest_events()` for explicit cost emission. |
| **Usage-only** | `client.meter.events.ingest_events()` only. No cost emission. | Same — no change. |
| **Cost-only** (subscription customers, infra hot paths) | **BLOCKED for v1.** Codemod inserts `# TODO: acute SDK not yet exposed; emit OTel span with attribute moolabs.cost.kind=<vendor>` and surfaces in PR. | `client.acute.events.ingest_events()` directly. |

The codemod must annotate every cost-only inserted block with `# v1: emitting via OTel until client.acute namespace ships (Doc 3 §6.3)` so review/audit can find them later.

---

## The four-namespace future

If the SDK team adds `client.acute.*` (likely path, per stitcher trajectory), it will expose at minimum:

```python
client.acute.events.ingest_events([...])      # direct cost-event emission
client.acute.cloud_billing.import_cur(...)    # programmatic cloud-bill ingestion
client.acute.attribution.query(...)           # algorithm result queries
client.acute.bom.get(...)                     # output-input bill-of-materials
```

The `/cost-billing-instrument` codemod is structured so swapping pattern 1's OTel emission for `client.acute.events.ingest_events()` is a one-template change — no per-customer rework needed.

---

## SDK is blocking by design (Doc 3 §6.1, requirements §10 #4) — confirmed

Both moolabs-py and moolabs-ts use synchronous transports (`urllib3.PoolManager` in py; fetch in ts). Median round-trip is ~35ms (cited in requirements §11).

**v1 codemod default: Option B — blocking insert + PR documents the latency.** The codemod adds a hot-path comment:

```python
# moolabs SDK blocks (~35ms typical); see PR for latency profile
client.meter.events.ingest_events([...])
```

The decision to swap to background-wrap is per-customer, not per-codemod. See `requirements §10 #4`.

---

## Authentication and routing

- One API key authenticates both namespaces (per README).
- API keys are region-encoded (`sk_use1_*`, `sk_apse1_*` per requirements §11) — the SDK routes regionally on its own. The codemod does NOT prompt for region.
- Base URL override for staging/private deploys is supported (`cls_base_url`, `meter_base_url`). Skill A's discovery surface should ask "is this production?" and if no, surface the base-URL override pattern.

---

## What the codemod must never assume

- **Do NOT assume `client.usage.*` exists.** The local `../moolabs/sdks/generator/configs/moolabs-python.yaml:18` comment mentions `client.usage` — this is **stale**. The active customer-facing namespace is `client.meter.events`, per the live README.
- **Do NOT call the underlying `EventsApi`, `MetersApi` directly.** Always go through `client.meter.events.*`. The `*_api.py` modules are internal generated classes; calling them bypasses the namespace routing and breaks when the namespace shape changes.
- **Do NOT assume an async variant.** Per Doc 3 §6.1 there is none. Background-wrapping is the caller's responsibility.

---

## Cross-language parity

All three SDKs (`moolabs-py`, `moolabs-go`, `moolabs-ts`) are auto-generated from the same stitched OpenAPI spec and ship the same API classes (verified by listing). Codemod templates in `/cost-billing-instrument` are organized by `{language}-{framework}` (e.g., `python-fastapi`, `typescript-express`, `go-stdlib`), and all three target the same namespace shape.

`moolabs-go`'s naming differs only in case (`api_cost_events.go` vs `cost_events_api.py`) and idiom (`*context.Context` first arg). Otherwise identical surface.

---

## Inputs for downstream skills

| Skill | What this reference gives it |
|---|---|
| `/cost-billing-discovery` | Knows what surface to wire (`client.meter.events.ingest_events`) and what NOT to wire (acute SDK absent — emit OTel for cost). |
| `/cost-billing-instrument` | Template selector reads `{language, framework} → template file`; templates reference verified call shapes here. |
| `/cost-billing-drift-lint` | When scanning customer code, looks for `client.meter.events.*` calls (positive match) and flags any direct `EventsApi`/`MetersApi` calls (anti-pattern). |
| `/cost-billing-reconcile` | Reconciles cost spans (Tier 2/4 OTel) against Tier 5 cloud-bill imports — until acute SDK ships, cost emission audit-trail is span-only. |
| `/cost-billing-adversarial-review` | Risk class "wrong namespace" — must flag any insert that uses `client.usage.*` or calls `EventsApi` directly. |
| `/cost-billing-cloud-bill` | No direct dependency — cloud-bill ingestion is configured server-side via ACUTE integrations, not via SDK. |

---

## When to refresh this file

- SDK team adds `client.acute.*` → update §"acute gap" + §"four-namespace future"
- Stitcher adds a new service → update generated-API-class table
- README at `github.com/moolabs-hq/moolabs-py` changes the namespace shape → update §"namespaces"

Verification command:

```bash
gh api repos/moolabs-hq/moolabs-py/contents/README.md -q .content | base64 -d | head -100
gh api repos/moolabs-hq/moolabs-py/contents/moolabs/api -q '.[].name' | sort
```
