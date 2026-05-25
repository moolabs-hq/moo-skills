# SDK surface reference — customer-facing Moolabs SDK shape

This is the **single source of truth** for what the customer-facing Moolabs SDK looks like — i.e., what calls the codemod (`/cost-billing-instrument`) is allowed to emit into the customer's code. Every other skill in the suite reads from this file rather than re-deriving it.

> Verified against the published SDKs (`moolabs-hq/moolabs-py`, `moolabs-go`, `moolabs-ts`) as of 2026-05-18 (call shape) and 2026-05-25 (install paths). **Customer engineers do not need to know about Moolabs's internal services** — this reference focuses on the public install + import + call shape the customer will see in their own code.

## Install — IMPORTANT

**SDKs are NOT currently on public package registries.** As of 2026-05-25:

| Registry | `moolabs-py` | `moolabs-ts` | `moolabs-go` |
|---|---|---|---|
| PyPI (`pip install moolabs`) | ❌ 404 | n/a | n/a |
| npm (`npm install moolabs` or `@moolabs/sdk`) | n/a | ❌ 404 | n/a |
| Go module proxy (`go get moolabs.com/sdk`) | n/a | n/a | ❌ wrong path |

**Install via GitHub directly.** The default install — what the codemod's pre-merge note recommends and what `cost-billing-bootstrap-team-engineer` Q16 defaults to — resolves the latest GitHub release tag dynamically:

### Tag-selection contract (CRITICAL)

The default "latest tag" pipeline MUST filter to **stable releases only**. `sort -V | tail -1` alone is buggy — `sort -V` places `v1.0.0-rc1` AFTER `v1.0.0`, so the naïve pipeline would pick the release candidate over the stable release. Every install command below uses a strict `vX.Y.Z` regex (anchored with `$`) to reject any prerelease suffix (`-rc1`, `-beta`, `-alpha`, etc.). Customers wanting prereleases use Q16 strategy `pinned` instead.

### Python

```bash
LATEST=$(git ls-remote --tags https://github.com/moolabs-hq/moolabs-py.git \
  | grep -v '\^{}' \
  | awk -F'refs/tags/' '{print $2}' \
  | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
  | sort -V | tail -1)
[ -n "$LATEST" ] || { echo "no stable tag found"; exit 1; }
pip install -U "git+https://github.com/moolabs-hq/moolabs-py.git@$LATEST"
```

Import: `from moolabs import Moolabs` (verified against local moolabs-py).

### TypeScript

```bash
LATEST=$(git ls-remote --tags https://github.com/moolabs-hq/moolabs-ts.git \
  | grep -v '\^{}' \
  | awk -F'refs/tags/' '{print $2}' \
  | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
  | sort -V | tail -1)
[ -n "$LATEST" ] || { echo "no stable tag found"; exit 1; }
npm install -E "moolabs-hq/moolabs-ts#$LATEST"
```

Import: `import { Moolabs } from 'moolabs';` (the npm `github:org/repo#tag` syntax installs the package under whatever name its `package.json > name` declares — typically `moolabs`).

### Go (⚠️ requires workaround until upstream go.mod is fixed)

The `moolabs-go` repo's `go.mod` currently declares module path `github.com/moolabs/moolabs-go` but the repo lives at `github.com/moolabs-hq/moolabs-go`. A bare `go get github.com/moolabs-hq/moolabs-go@latest` **fails today** with:

```
go: github.com/moolabs-hq/moolabs-go@vX.Y.Z: parsing go.mod:
        module declares its path as: github.com/moolabs/moolabs-go
                but was required as: github.com/moolabs-hq/moolabs-go
```

**Canonical workaround** (the codemod emits this verbatim in its PR pre-merge note when Go is in the touched languages):

```go
// go.mod
require github.com/moolabs/moolabs-go vX.Y.Z   // use the latest stable tag

replace github.com/moolabs/moolabs-go => github.com/moolabs-hq/moolabs-go vX.Y.Z
```

```bash
# 1. Resolve latest stable tag (same filter discipline as Python/TS)
LATEST=$(git ls-remote --tags https://github.com/moolabs-hq/moolabs-go.git \
  | grep -v '\^{}' \
  | awk -F'refs/tags/' '{print $2}' \
  | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
  | sort -V | tail -1)
[ -n "$LATEST" ] || { echo "no stable tag found"; exit 1; }

# 2. Add the require + replace lines to go.mod, then:
go mod tidy
```

Import (after replace directive): `import "github.com/moolabs/moolabs-go"` — note the customer's import path matches the (wrong) module path, not the repo URL.

Track upstream fix at: https://github.com/moolabs-hq/moolabs-go/blob/main/go.mod — once the module path matches the repo path, the codemod will switch back to the simple `go get github.com/moolabs-hq/moolabs-go@latest` form transparently.

### Overrides

`cost-billing-bootstrap-team-engineer` Q16 captures per-language overrides for customers who need:

- **Pinned version** (instead of latest tag) — for reproducibility / compliance
- **Private mirror** — for air-gapped or VPN-only customers
- **GitHub auth env var** — for private forks (`GH_TOKEN`, `GITHUB_TOKEN`)
- **Custom command** — for teams with internal build wrappers / pre-vendored copies

These flow into `04-final.signed.yaml > integration.sdk_package_install` and the codemod reads them when emitting the PR's pre-merge note.

### Roadmap note

Moolabs platform team plans to publish to public registries (PyPI / npm / Go vanity URL) post-GA. This reference will be updated when that happens; codemod will switch to standard `pip install moolabs` / `npm install moolabs` paths transparently — customer-context's `sdk_package_install` block remains the override surface.

---

## Customer-facing namespaces — 11 flat capabilities (verified)

**This section was wrong in the 2026-05-19 draft of this doc.** The earlier claim was a `client.cls.*` + `client.meter.*` split; the actual SDK ships **11 flat capability namespaces** directly on the `Moolabs` client. Each capability dispatches dynamically via `_Namespace.__getattr__` to one or more backing API classes routed by `_dx_routing.CAPABILITY_MAP`.

The codemod must NOT read this section as authoritative at runtime — Phase 1.5 (`scripts/sdk_snapshot.py`) introspects the actual SDK at the pinned version and writes `.moolabs/customer-context/sdk-surface-snapshot.yaml`. This section exists as a human-readable summary verified against `moolabs-py@v0.2.0-rc9` (2026-05-25).

```python
from moolabs import Moolabs
client = Moolabs(api_key="moo_live_...")

# The 11 capability namespaces (flat on `client`):
client.usage          # event ingest + meter querying       → EventsApi, MetersApi (meter backend)
client.cost           # cost-event ingest                   → CostEventsApi, SdkIngestApi (acute backend)
client.customers      # customers + subjects                → CustomersApi, SubjectsApi (meter)
client.catalog        # plans, features, addons, rate cards → ProductCatalogApi (meter) + RateCardsApi (bff)
client.subscriptions  # subscriptions + addons              → MeterSubscriptionsApi (meter)
client.entitlements   # access checks + grants              → EntitlementsApi (meter)
client.wallets        # prepaid-credit wallets              → WalletsApi (bff)
client.credits        # grants, ledger, auto-topup          → GrantsApi, LedgerApi, AutoTopupApi (bff)
client.billing        # invoicing + rating + FX             → MeterBillingApi (meter) + RatingApi, FxRatesApi (bff)
client.collections    # AR / dunning / arc resources        → 17 Arc API classes
client.notifications  # channels + rules + alerts           → NotificationsApi (meter) + AlertsApi (bff)
```

**The two emission entry points** the codemod cares about:

| Event | Capability | Method | Verified at v0.2.0-rc9 |
|---|---|---|---|
| Usage | `client.usage` | `ingest_events([...])` | ✓ |
| Cost  | `client.cost`  | `ingest_events_batch([...])`, `ingest_event(...)`, `ingest_sdk_spans(...)`, `submit_adjustment(...)` | ✓ |

Routing is internal (`api.moolabs.com`, `meter.moolabs.com`, `acute.moolabs.com` per capability). The SDK derives subdomains from `base_url`; the codemod does NOT wire base URLs per call.

---

## Direct cost-event emission — not yet exposed in the unified SDK

**The Moolabs SDK is unified — one client, multiple namespaces.** Today the client exposes `client.cls.*` (billing/wallets) and `client.meter.*` (usage events). A customer-facing cost-event endpoint does NOT yet exist on the same client (final method path TBD — likely something like `client.meter.cost.ingest_events()` or a new sibling namespace on the same client; the platform team owns the decision). **There is no separate "acute SDK"** — when the cost-event endpoint ships, it lands on the existing unified `Moolabs` client.

This is a **Moolabs platform roadmap item, not a customer-visible blocker**. Until that endpoint ships, the codemod (`/cost-billing-instrument`) emits cost via OTel span attributes (preferred) + a structured-log recovery rail (when no recording span exists) per the dual-transport contract in `cost-billing-instrument/SKILL.md` Phase 2. The customer never sees the workaround as anything other than a `# TODO` annotation in their PR.

### v1 implications

Three patterns the codemod (Skill 2) must choose between:

| Pattern | Today (v1, 2026-05-19) | After unified SDK's cost-event endpoint ships |
|---|---|---|
| **Sibling-pair** (one site, both events) | Usage via `client.meter.events.ingest_events()`; cost via dual transport — OTel span attributes preferred, structured log fallback (per `emit_cost_event_safe()` helper). | Same usage call; cost-event helper swaps primary transport from OTel-span to direct SDK call on the SAME `Moolabs` client. Log recovery rail stays. |
| **Usage-only** | `client.meter.events.ingest_events()` only. No cost emission. | Same — no change. |
| **Cost-only** (subscription customers, infra hot paths) | **BLOCKED for v1.** Codemod inserts `# TODO: cost-event endpoint not yet exposed on unified SDK; emitting via OTel span + log fallback until it ships` and surfaces in PR. | Direct call on the unified client (exact method TBD by platform team). |

The codemod annotates every cost-only block with `# v1: emitting via OTel + log fallback until unified SDK's cost-event endpoint ships` so the customer's PR review can find them later.

---

## Future SDK surface (not in scope for the codemod today)

When the unified SDK adds its cost-event endpoint, the `/cost-billing-instrument` helper templates change in ONE place — `emit_cost_event_safe()`'s primary-transport branch swaps from OTel-span-write to the new SDK method on the same `Moolabs` client. Call sites do not change. The log recovery rail stays as defense in depth (and matches the recovery rail for usage events).

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
| `/cost-billing-adversarial-review` | Risk class "wrong namespace" — must flag any insert that uses `client.usage.*` or calls `EventsApi` directly. |
| `/cost-billing-cloud-bill` | No direct dependency — cloud-bill ingestion is configured server-side, not via SDK. |

---

## When to refresh this file

- Unified Moolabs SDK adds a cost-event endpoint (e.g. `client.meter.cost.*`) → update §"direct cost-event emission" + §"future SDK surface"
- SDK README at `github.com/moolabs-hq/moolabs-py` changes the namespace shape → update §"namespaces"

For suite maintainers, the underlying SDK can be inspected via:

```bash
gh api repos/moolabs-hq/moolabs-py/contents/README.md -q .content | base64 -d | head -100
```
