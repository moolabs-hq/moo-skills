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

**The SDK handles ALL URL routing internally.** Customer code constructs `Moolabs(api_key=...)` — nothing else. The SDK:

- Defaults to apex `moolabs.com` (production).
- Derives `api.{apex}` / `meter.{apex}` / `arc.{apex}` for the capability backends.
- Looks up regional ingest URLs via `/tenant/config` on first ingest call, caches for process lifetime, falls back to a region-map + `meter.{apex}` chain on failure.

**The codemod does NOT prompt the customer for any URL.** No `base_url`. No `meter_base_url`. No region selection. If a customer needs a non-default apex (dev, staging, self-hosted), they set whatever env var the SDK itself respects and patch the generated `moolabs_client.py` helper post-codemod. We don't capture URL overrides in `04-final.signed.yaml` — that would be a footgun (see §"Known upstream SDK issues" below for what happens when customers pass per-service URLs as `base_url`).

---

## Direct cost-event emission — already exposed on the unified SDK

**The Moolabs SDK is unified — one client, multiple namespaces.** Verified against `moolabs-py@v0.2.0-rc9` (Phase 1.5 snapshot, 2026-05-25): the unified client exposes 11 flat capability namespaces, including BOTH `client.usage` (capability "usage" → EventsApi + MetersApi) AND `client.cost` (capability "cost" → CostEventsApi + SdkIngestApi on the acute backend). There is no separate "acute SDK" and there is no nested `client.cls.*` / `client.meter.events.*` shape — those were drafts from an earlier curation of this doc that did NOT match the shipped SDK.

| Event | Namespace | Method | Verified at v0.2.0-rc9 |
|---|---|---|---|
| Usage | `client.usage` | `ingest_events([...])` | ✓ |
| Cost  | `client.cost`  | `ingest_events_batch([...])`, `ingest_event(...)`, `ingest_sdk_spans(...)`, `submit_adjustment(...)` | ✓ |

### v1 codemod patterns

Three patterns the codemod (Skill 2) chooses between. ALL three route through the per-service helper (`moolabs_client.py`) — call sites never instantiate `Moolabs()` inline or touch SDK namespaces directly. The helper's primary transport per event is gated on the Phase 1.5 snapshot.

| Pattern | Helper call | Helper transport (based on snapshot) |
|---|---|---|
| **Sibling-pair** (one site, both events) | `emit_usage_event_safe(...)` + `emit_cost_event_safe(...)` | Usage: SDK call → log recovery rail. Cost: SDK call when `capabilities.cost_event_direct_emit=true` (today, v0.2.0-rc9: TRUE); OTel-span preferred + log recovery rail otherwise. |
| **Usage-only** (terminal-only event) | `emit_usage_event_safe(...)` | Same usage transport as above. |
| **Cost-only** (subscription customers, infra hot paths) | `emit_cost_event_safe(...)` | Same cost transport as above — the snapshot decides at codemod time whether the SDK branch fires. |

Cost-only inserts are NOT blocked in v1 — they go through the same helper as sibling-pair's cost branch.

---

## Future SDK surface (not in scope for the codemod today)

When the unified SDK adds new capability namespaces (e.g. a future `client.span_ingest.*` for OTLP-format spans), the Phase 1.5 snapshot picks them up automatically. The helper template's `capabilities.cost_event_method_path` resolves at codemod time — no skill update is needed when the SDK adds same-shape endpoints. Call sites stay unchanged.

---

## SDK is blocking by design (~35ms median round-trip)

Both moolabs-py and moolabs-ts use synchronous transports (`urllib3.PoolManager` in py; fetch in ts). Median round-trip is ~35ms.

**v1 codemod default: Option B — blocking insert + PR documents the latency.** The codemod adds a hot-path comment:

```python
# moolabs SDK blocks (~35ms typical); see PR for latency profile
# Helper routes to client.usage.ingest_events() — verified per Phase 1.5 snapshot
emit_usage_event_safe(...)
```

The decision to swap to background-wrap is per-customer, not per-codemod. See `requirements §10 #4`.

---

## Authentication and routing

- One API key authenticates all capability namespaces (per README).
- API keys are region-encoded (`sk_use1_*`, `sk_apse1_*`) — the SDK routes regionally on its own. The codemod does NOT prompt for region.
- The SDK handles URL routing internally (see top of this section). The codemod's helper template constructs `Moolabs(api_key=...)` — NOTHING ELSE. No `base_url`, no per-service URLs, no region. Customers needing non-default routing (dev / staging / self-hosted) configure it via whatever env var the SDK natively respects, or patch the generated helper post-codemod. **The codemod does not capture URLs in customer-context.**

---

## Known upstream SDK issues (open as of 2026-05-25)

These are pre-existing SDK / platform-infra bugs surfaced during a moo-arc end-to-end probe. The codemod does NOT cause them; the codemod ALSO can't fix them (they're upstream). Documented here so customers running on dev / encountering them recognize the cause.

| Issue | Symptom | Why it happens | Workaround |
|---|---|---|---|
| **`base_url` mis-use → DNS mangling** | Customer passes `base_url="https://meter.dev.moolabs.com"` (the meter service URL from their .env). SDK then derives `ingest.us.meter.dev.moolabs.com` (extra `meter.` segment), DNS NXDOMAIN. | SDK expects `base_url` to be the APEX hostname (e.g. `dev.moolabs.com`), not a service-specific URL. Customer's .env exposes per-service URLs (`METER_BASE_URL=...`) which look like reasonable defaults but break the apex assumption. | Pass apex only OR don't pass `base_url` at all (use SDK default). The codemod NEVER passes `base_url` — this is documented here so customers don't add it themselves. |
| **URL path doubling** | Final ingest URL ends `/api/v1/events/api/v1/events` (path repeated). | The internal resolver returns `/api/v1/events`, then `EventsApi` re-prefixes it. | Upstream SDK fix needed — track at moolabs-hq/moolabs-py. Not codemod's surface. |
| **Dev DNS topology mismatch** | SDK's ingest resolver expects `ingest.<region>.<apex>` subdomain. Dev only exposes `meter.dev.moolabs.com`. | Platform-infra hasn't deployed the regional ingest topology to dev. | Production environments don't hit this. Dev customers should test in buffer mode (`Moolabs(api_key, buffer=True)`) which returns `{'buffered': True}` instead of attempting the live POST. |

If a customer reports an ingest failure during PR review, check these first before debugging the codemod's emitted code.

---

## What the codemod must never assume

- **Do NOT trust this doc at codemod runtime.** This section is a human-readable summary. The Phase 1.5 snapshot (`scripts/sdk_snapshot.py` → `.moolabs/customer-context/sdk-surface-snapshot.yaml`) is the runtime source of truth, verified against the SDK source at the customer-pinned version. If this doc and the snapshot disagree, the snapshot wins.
- **Do NOT instantiate `Moolabs()` inline at call sites.** Every emission goes through the per-service helper (`moolabs_client.py`) which owns the singleton, secret resolution, and fail-open + never-drop recovery rails. Helper API: `emit_usage_event_safe(...)`, `emit_cost_event_safe(...)`.
- **Do NOT call the underlying `*_Api` classes directly.** They're internal generated classes — call through the unified namespace path (`client.usage.ingest_events`, `client.cost.ingest_events_batch`). Direct `EventsApi.*` / `CostEventsApi.*` calls bypass the namespace router and break on SDK shape changes.
- **Do NOT assume an async variant.** There is none in v1. Background-wrapping is the caller's responsibility.

---

## Cross-language parity

All three SDKs (`moolabs-py`, `moolabs-go`, `moolabs-ts`) are auto-generated from the same stitched OpenAPI spec and ship the same API classes (verified by listing). Codemod templates in `/cost-billing-instrument` are organized by `{language}-{framework}` (e.g., `python-fastapi`, `typescript-express`, `go-stdlib`), and all three target the same namespace shape.

`moolabs-go`'s naming differs only in case (`api_cost_events.go` vs `cost_events_api.py`) and idiom (`*context.Context` first arg). Otherwise identical surface.

---

## Inputs for downstream skills

| Skill | What this reference gives it |
|---|---|
| `/cost-billing-discovery` | Knows what surface to wire — for the FLAT capabilities verified by the Phase 1.5 snapshot (`client.usage.ingest_events` for usage; `client.cost.ingest_events_batch` for cost). |
| `/cost-billing-instrument` | Template selector reads `{language, framework} → template file`; templates render helper calls only. The Phase 1.5 snapshot is the runtime source of truth. |
| `/cost-billing-drift-lint` | Scans customer code for helper-routed calls (`emit_usage_event_safe`, `emit_cost_event_safe`) — positive match. Flags direct `Moolabs(...)` instantiation OR direct `*_Api` calls as anti-patterns. |
| `/cost-billing-adversarial-review` | Risk class "wrong namespace" — must flag any insert that bypasses the helper, instantiates `Moolabs()` inline, or calls `*_Api` classes directly. Namespace shape itself is verified by the Phase 1.5 snapshot, not by this doc. |
| `/cost-billing-cloud-bill` | No direct dependency — cloud-bill ingestion is configured server-side, not via SDK. |

---

## When to refresh this file

- Unified Moolabs SDK adds a cost-event endpoint (e.g. `client.meter.cost.*`) → update §"direct cost-event emission" + §"future SDK surface"
- SDK README at `github.com/moolabs-hq/moolabs-py` changes the namespace shape → update §"namespaces"

For suite maintainers, the underlying SDK can be inspected via:

```bash
gh api repos/moolabs-hq/moolabs-py/contents/README.md -q .content | base64 -d | head -100
```
