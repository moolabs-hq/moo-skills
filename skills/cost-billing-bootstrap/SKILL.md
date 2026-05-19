---
name: cost-billing-bootstrap
description: >-
  LLM-driven first-run setup for the Cost+Billing suite — asks the customer about their product (reference doc, pricing page URL, primary repo path, telemetry/observability stack, terminology overrides), then generates customer-context/{product-summary.md, pricing-model.yaml, repo-info.yaml, telemetry-stack.yaml, terminology.yaml}. Every other skill reads from there before running, so discovery / codemod / drift-lint / review speak the customer's own terms rather than generic defaults. The customer's repo is unknown territory; this bootstrap teaches the suite their domain. Runs under whichever LLM the customer is using (Claude / GPT / Gemini — agent-surface-agnostic). Run ONCE per customer integration after install; idempotent re-runs supported via --refresh. Skills refuse to run without customer-context/. Triggers on "bootstrap the suite", "set up cost-billing", "customer context", "ingest my product doc", "first run setup", "configure the cost-billing skills".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
---

# /cost-billing-bootstrap — Generate customer-context references

You are the first-run setup for the Cost+Billing suite. Every other skill in the suite reads `.moolabs/customer-context/` before running. Your job is to fill that directory by asking the customer for their product reference materials and synthesizing them into a small set of YAML/markdown files the skills speak.

## Trigger

```
/cost-billing-bootstrap
/cost-billing-bootstrap --repo /path/to/customer/repo
/cost-billing-bootstrap --refresh    # re-run after product/pricing changes
```

Naturally:

```
Set up cost-billing for this customer
Bootstrap the suite
Ingest my product doc and pricing
First-run setup
```

## Why this skill exists

The other six skills in the suite (`/cost-billing-discovery`, `/cost-billing-instrument`, etc.) are **customer-portable** — they run inside any customer's repo, where everything is unknown. Without a bootstrap, those skills would have to ask the customer five clarifying questions every run, OR fall back to Moolabs-internal terminology that doesn't match the customer's language.

You solve this once: ask the customer for their references, generate `customer-context/`, and let the rest of the suite read from there.

## What you ask the customer (5 questions, in order)

You ask one at a time and wait for an answer before continuing. Use this skill's invocation context (you are running under whatever LLM the customer chose — Claude, GPT-4, whatever the agent surface provides) to do the actual synthesis.

### 1. Product reference document

> "Where can I find your product documentation? Paste a URL, a file path, or paste the doc content directly. If you have multiple docs (developer docs vs. marketing site), give me the one that best describes your product's features and value to end users."

Accept any of:
- URL (`https://docs.example.com/`) — fetch via web fetch tool
- Path (`/path/to/product-docs/`) — recursively read markdown / RST / text
- Pasted content — read inline

Extract:
- Product name + tagline
- Top 5–10 features (the things customers buy)
- End-user terminology (do they say "users" or "agents" or "operators"?)
- Any non-obvious billing concepts ("seats", "credits", "minutes", "renders")

Write to `customer-context/product-summary.md` — a 200–400 line synthesis the other skills load on demand.

### 2. Pricing page

> "What's your pricing page URL? (Optional — skip if you don't have public pricing or it's enterprise-only.)"

If provided, fetch + extract:
- Plans/tiers (Free / Pro / Enterprise / pay-as-you-go)
- Billable units per feature ("per 1k tokens", "per render", "per minute", "per seat")
- Unit prices
- Fair-usage thresholds (free quotas, included credits)
- Add-on pricing

Write to `customer-context/pricing-model.yaml` (schema below).

### 3. Primary repo path

> "Path to the primary repo you want to instrument? (We support monorepo and polyrepo; if you have multiple repos, give me the one that contains the user-facing API or backend.)"

Run a quick repo profile (`cost-billing-discovery/scripts/repo_scan.py` if available, otherwise a simpler inline scan). Extract:
- Repo type (mono/poly/microservices)
- Detected languages + frameworks per service
- Existing instrumentation (OTel / Datadog / Sentry / Helicone / OpenLLMetry)
- Existing Moolabs SDK presence (yes / no / partial)
- Build system (uv, npm, go, etc.)
- Test command (best-effort heuristic from manifest)

Write to `customer-context/repo-info.yaml`.

### 4. Telemetry / observability stack

> "What's your current telemetry stack? OTel? Datadog? Sentry? Multiple? None? (Affects whether the codemod extends existing spans or introduces new ones.)"

If repo scan in step 3 already detected this, confirm with the user. If not, ask.

Write to `customer-context/telemetry-stack.yaml`:

```yaml
primary_tracer: opentelemetry | datadog | sentry | none
secondary: [openllmetry, helicone]
mode: brownfield | greenfield
trace_context_provider: opentelemetry-api | datadog-trace | sentry-sdk | custom
```

### 5. Terminology preferences

> "What do YOU call the things the suite works with? Different customers say 'completions' vs 'generations' vs 'responses'; 'users' vs 'agents' vs 'tenants'. Tell me any term I should use that's different from what I'd default to. (Optional.)"

Write to `customer-context/terminology.yaml`:

```yaml
end_user: "agent"             # default "end-user"
output: "completion"          # default "output"
output_event: "generation_delivered"   # what they call it
unit_token: "credit"          # if they bill in credits not tokens
# ...any other overrides
```

The other skills consult this file when generating user-facing strings. PR descriptions, codemod comments, review surface labels all use the customer's terms.

## What you write

```
.moolabs/customer-context/
├── README.md                  # one-paragraph summary + when to refresh
├── product-summary.md         # 200-400 line product synthesis
├── pricing-model.yaml         # billable units + prices + fair-usage
├── repo-info.yaml             # repo shape + languages + frameworks
├── telemetry-stack.yaml       # tracer + mode (brownfield / greenfield)
└── terminology.yaml           # the customer's words for things
```

Plus update `.moolabs/customer-context/bootstrap-log.yaml`:

```yaml
generated_at: 2026-05-19T08:42:00Z
generator: cost-billing-bootstrap@0.1.0
llm_used: claude-opus-4-7   # whatever the active agent surface reports
source_artifacts:
  product_doc: https://docs.acme.com/
  pricing_page: https://acme.com/pricing
  repo_root: /Users/x/code/acme-backend
  telemetry_self_reported: opentelemetry+helicone
refresh_recommended_when:
  - product doc changes materially
  - pricing model changes
  - repo undergoes major refactor (new service, framework swap)
```

## Schemas

### `customer-context/pricing-model.yaml`

```yaml
$schema: https://moolabs.com/schemas/customer-context/pricing-model/0.1.0
plans:
  - id: free
    name: "Free"
    monthly_usd: 0
    included_quota:
      - unit: completion
        quantity: 100
        period: month
  - id: pro
    name: "Pro"
    monthly_usd: 29
    included_quota:
      - unit: completion
        quantity: 10000
        period: month
overage_rates:
  - unit: completion
    price_usd: 0.002
    after_quota: pro.completion
notable_terms:
  - "Fair usage applies: 50k completions/month free per user"
  - "GPU-bound features count differently — see pricing notes"
```

### `customer-context/repo-info.yaml`

```yaml
$schema: https://moolabs.com/schemas/customer-context/repo-info/0.1.0
repo_root: /Users/x/code/acme-backend
repo_type: monorepo
services:
  - path: services/api
    languages: [python]
    frameworks: [fastapi]
    existing_instrumentation: [opentelemetry-api]
    existing_moolabs_sdk: null
    build_command: "uv sync"
    test_command: "pytest"
  - path: services/render
    languages: [typescript]
    frameworks: [express]
    existing_instrumentation: [opentelemetry-api, helicone]
    existing_moolabs_sdk: null
    build_command: "npm install"
    test_command: "npm test"
```

### `customer-context/telemetry-stack.yaml`

```yaml
$schema: https://moolabs.com/schemas/customer-context/telemetry-stack/0.1.0
primary_tracer: opentelemetry
secondary: [helicone]
mode: brownfield                  # existing spans present
trace_context_provider: opentelemetry-api
existing_attributes_prefix:
  - "gen_ai.*"
  - "helicone.*"
```

### `customer-context/terminology.yaml`

```yaml
$schema: https://moolabs.com/schemas/customer-context/terminology/0.1.0
overrides:
  end_user: "user"                # customer's word for their customer
  output: "generation"            # what they call a billable output
  output_event: "generation.delivered"  # the event_type they prefer
  unit_token: "credit"            # billable unit if not "token"
  pricing_model_doc_url: "https://acme.com/pricing"
synonyms:
  completion: [generation, response, output]
  render: [image, picture]
```

## Workflow

1. **Pre-check.** If `.moolabs/customer-context/` already exists and `--refresh` not passed, ask the user: "Existing context found, refresh or use as-is?" If they say use-as-is, exit immediately reporting the existing files.

2. **Ask questions 1–5** in order. Show the user a brief reasoning trace as you synthesize each artifact (e.g., "From your pricing page I'm extracting 3 plans: Free, Pro, Enterprise…").

3. **Write artifacts** atomically. Use a temp directory then move; never leave a half-written `customer-context/`.

4. **Validate** — every written file must pass its `$schema` ref. If validation fails, surface the failure and ask the user to clarify.

5. **Report** — print a summary table:

   ```
   customer-context/ generated at .moolabs/customer-context/

     product-summary.md     312 lines   (5 features extracted)
     pricing-model.yaml     2 plans + 1 overage rate
     repo-info.yaml         2 services detected (api, render)
     telemetry-stack.yaml   brownfield (opentelemetry-api + helicone)
     terminology.yaml       4 overrides + 2 synonym groups
     bootstrap-log.yaml     generator + LLM + source artifacts

   Next: run /cost-billing-discovery <repo>
   ```

## What this skill MUST NOT do

- **Never** invent product features, pricing, or terminology. If the user's source is unclear, ask — don't synthesize from nothing.
- **Never** leak customer source content out of the local environment. Bootstrap reads URLs + files locally; the customer-context/ stays in the customer repo.
- **Never** overwrite an existing `customer-context/` without explicit confirmation (or `--refresh`).
- **Never** assume the customer uses Moolabs-internal terms. Always check terminology.yaml first.

## Why "use whatever LLM they're using"

This skill is intentionally written so it runs under any agent surface (Claude Code, Cursor, Copilot, Gemini CLI). The synthesis step ("read this product doc and summarize the features") is just LLM work — no platform-specific tooling required.

When deployed to a customer who runs GPT-4 via Cursor, the bootstrap runs under GPT-4 and produces the same customer-context shape. The downstream skills don't care which LLM generated the context.

## Reference files

- `references/synthesis-prompts.md` — the prompts the bootstrap uses internally for product-doc summarization, pricing extraction, etc.
- `references/refresh-triggers.md` — when to re-run bootstrap (product launches, pricing changes, repo refactors).
- `references/customer-context-readers.md` — for skill authors: how the other 6 skills should read from `customer-context/`.

## Assets

- `assets/customer-context-templates/product-summary.template.md`
- `assets/customer-context-templates/pricing-model.template.yaml`
- `assets/customer-context-templates/repo-info.template.yaml`
- `assets/customer-context-templates/telemetry-stack.template.yaml`
- `assets/customer-context-templates/terminology.template.yaml`
