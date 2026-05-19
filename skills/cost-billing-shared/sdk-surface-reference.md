# SDK surface reference — customer-facing Moolabs SDK shape

This is the **single source of truth** for what the customer-facing Moolabs SDK looks like — i.e., what calls the codemod (`/cost-billing-instrument`) is allowed to emit into the customer's code. Every other skill in the suite reads from this file rather than re-deriving it.

> Verified against the published SDKs (`moolabs-hq/moolabs-py`, `moolabs-go`, `moolabs-ts`) as of 2026-05-18. **Customer engineers do not need to know about Moolabs's internal services** — this reference focuses on the public install + import + call shape the customer will see in their own code.

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

## Direct cost-event emission — not yet exposed in the customer SDK

**There is no `client.acute.*` namespace today.** Subscription customers (zero usage events) and any customer needing direct cost-event emission for non-AI infra spend cannot call the cost-event ingest path via the unified SDK.

This is a **Moolabs platform roadmap item, not a customer-visible blocker**. The codemod (`/cost-billing-instrument`) handles it via the OTel-span path described below — the customer never sees the workaround as anything other than a `# TODO` annotation in their PR.

### v1 implications

Three patterns the codemod (Skill 2) must choose between:

| Pattern | Today (v1, 2026-05-19) | After direct cost-event SDK lands |
|---|---|---|
| **Sibling-pair** (one site, both events) | Wire `client.meter.events.ingest_events()` for usage; emit cost as OTel span attributes (`moolabs.request.id`, `moolabs.cost.kind=<vendor>`). The Moolabs platform reads the spans on the backend. | Same usage call; replace OTel-only cost with `client.acute.events.ingest_events()` for explicit cost emission. |
| **Usage-only** | `client.meter.events.ingest_events()` only. No cost emission. | Same — no change. |
| **Cost-only** (subscription customers, infra hot paths) | **BLOCKED for v1.** Codemod inserts `# TODO: direct cost-event SDK not yet exposed; emit OTel span with attribute moolabs.cost.kind=<vendor>` and surfaces in PR. | `client.acute.events.ingest_events()` directly. |

The codemod annotates every cost-only block with `# v1: emitting via OTel until direct cost-event namespace ships` so the customer's PR review can find them later.

---

## Future SDK surface (not in scope for the codemod today)

If Moolabs adds a `client.acute.*` namespace, the codemod templates are structured so swapping pattern 1's OTel emission for the direct call is a one-template change — no per-customer rework needed. Until then, the OTel-span path is the supported v1 mechanism.

---

## SDK is blocking by design (~35ms median round-trip)

Both moolabs-py and moolabs-ts use synchronous transports (`urllib3.PoolManager` in py; fetch in ts). Median round-trip is ~35ms.

**v1 codemod default: Option B — blocking insert + PR documents the latency.** The codemod adds a hot-path comment:

```python
# moolabs SDK blocks (~35ms typical); see PR for latency profile
client.meter.events.ingest_events([...])
```

The decision to swap to background-wrap is per-customer, not per-codemod. See `requirements §10 #4`.

---

## Authentication and routing

- One API key authenticates both namespaces (per README).
- API keys are region-encoded (`sk_use1_*`, `sk_apse1_*`) — the SDK routes regionally on its own. The codemod does NOT prompt for region.
- Base URL override for staging/private deploys is supported (`cls_base_url`, `meter_base_url`). Skill A's discovery surface should ask "is this production?" and if no, surface the base-URL override pattern.

---

## What the codemod must never assume

- **Do NOT assume `client.usage.*` exists.** Some older internal references mention this namespace — it is **stale**. The active customer-facing namespace is `client.meter.events`, per the live SDK README.
- **Do NOT call the underlying `EventsApi`, `MetersApi` directly.** Always go through `client.meter.events.*`. The `*_api.py` modules are internal generated classes; calling them bypasses the namespace routing and breaks when the namespace shape changes.
- **Do NOT assume an async variant.** There is none in v1. Background-wrapping is the caller's responsibility.

---

## Cross-language parity

All three SDKs (`moolabs-py`, `moolabs-go`, `moolabs-ts`) are auto-generated from the same stitched OpenAPI spec and ship the same API classes (verified by listing). Codemod templates in `/cost-billing-instrument` are organized by `{language}-{framework}` (e.g., `python-fastapi`, `typescript-express`, `go-stdlib`), and all three target the same namespace shape.

`moolabs-go`'s naming differs only in case (`api_cost_events.go` vs `cost_events_api.py`) and idiom (`*context.Context` first arg). Otherwise identical surface.

---

## Inputs for downstream skills

| Skill | What this reference gives it |
|---|---|
| `/cost-billing-discovery` | Knows what surface to wire (`client.meter.events.ingest_events`) and what NOT to wire (direct cost-event call absent — emit OTel for cost). |
| `/cost-billing-instrument` | Template selector reads `{language, framework} → template file`; templates reference verified call shapes here. |
| `/cost-billing-drift-lint` | When scanning customer code, looks for `client.meter.events.*` calls (positive match) and flags any direct `EventsApi`/`MetersApi` calls (anti-pattern). |
| `/cost-billing-reconcile` | Reconciles cost spans against cloud-bill imports — until direct cost-event SDK ships, cost emission audit-trail is span-only. |
| `/cost-billing-adversarial-review` | Risk class "wrong namespace" — must flag any insert that uses `client.usage.*` or calls `EventsApi` directly. |
| `/cost-billing-cloud-bill` | No direct dependency — cloud-bill ingestion is configured server-side, not via SDK. |

---

## When to refresh this file

- Moolabs adds `client.acute.*` namespace → update §"direct cost-event emission" + §"future SDK surface"
- SDK README at `github.com/moolabs-hq/moolabs-py` changes the namespace shape → update §"namespaces"

For suite maintainers, the underlying SDK can be inspected via:

```bash
gh api repos/moolabs-hq/moolabs-py/contents/README.md -q .content | base64 -d | head -100
```
