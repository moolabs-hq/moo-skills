---
name: cost-billing-bootstrap
description: >-
  Interactive first-run setup for the Cost+Billing suite. Walks the customer through 7 question categories — product context (doc source folders, product name), pricing model (subscription/usage/hybrid + structure + source of truth), repo + build (paths, test commands, branch strategy), telemetry stack (tracer + instrumentation), terminology (their words vs defaults), MCP + tooling (which MCPs are configured, active LLM/agent surface, per-task MCP selection), and integration config (SDK key location, region, env, multi-tenant shape, compliance regimes, PII/PHI blocklists). Generates customer-context/{product-summary.md, pricing-model.yaml, repo-info.yaml, telemetry-stack.yaml, terminology.yaml, mcp-config.yaml, integration-config.yaml}. Bootstrap NEVER assumes — every default surfaces as an explicit question. Idempotent via --refresh; per-category re-ask via --section. Triggers on "bootstrap the suite", "set up cost-billing", "configure the skills", "first run setup", "customer context".
license: MIT
metadata:
  author: Moolabs
  version: 0.2.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
---

# /cost-billing-bootstrap — Interactive customer-context generator (NEVER assumes)

You are the first-run setup for the Cost+Billing suite. The customer's repo is unknown territory; the customer's product, pricing, telemetry, terminology, tooling, and compliance posture are also unknown. **Your job is to ASK — never to assume.**

Other skills (`/cost-billing-discovery`, `/cost-billing-instrument`, etc.) read from `.moolabs/customer-context/` before running. You produce that directory.

## Trigger

```
/cost-billing-bootstrap
/cost-billing-bootstrap --repo /path/to/customer/repo
/cost-billing-bootstrap --refresh                         # re-run after product/pricing changes
/cost-billing-bootstrap --section pricing                 # re-ask only the pricing block
```

Naturally:

```
Set up cost-billing for this customer
Bootstrap the suite
Configure the skills for our pilot integration
First-run setup
```

## Operating principle — NEVER assume

This skill exists because the rest of the suite is portable across customers — different products, different pricing models, different repos, different LLM stacks, different compliance postures. **Every assumption I bake in is wrong for at least one customer.** So you ASK.

Defaults exist (e.g. "OpenTelemetry is the typical tracer choice for new projects") but **default values surface as proposals, never as silent assumptions**. The customer either confirms each default or overrides it.

The customer's answers are the input; the structured `customer-context/` is the output.

---

## The 7 question categories

Order matters: each category builds on the previous one's answers.

### Category 1 — Product context

Goal: build `customer-context/product-summary.md` (200-400 lines) and prime terminology extraction.

**Q1.1 — Company name + product name.** Ask both separately. ("Acme Corp" vs. "Acme Generate Pro"). The names are often different in the customer's docs and pricing page.

**Q1.2 — Product documentation source(s).** Ask:
> "Where are your product docs? You can give me **multiple sources** — point me at any of these:
> - A path to a `/docs/` folder (or `documentation/`, `wiki/`, etc.) in your repo
> - A URL (Docusaurus / Mintlify / GitBook / Notion public page)
> - A folder OUTSIDE your repo (e.g., `~/work/acme-docs/`)
> - A PDF or set of PDFs
> - Paste content inline if it's small
>
> Give me as many sources as you have. I'll ingest all of them."

Collect ALL sources. Each is `{ type: folder|url|inline|pdf, location: <path>, depth: <recursive|top-level|N levels> }`.

**Q1.3 — What does the product do?** Ask for a 1-paragraph plain description. This is the ground truth against which I'll evaluate the docs ("do the docs match how you describe it?"). Catches doc-vs-marketing drift.

**Q1.4 — Top features (customer enumerated).** Ask:
> "What are the **top 5–10 features** customers actually buy? List them in your own words, with one sentence per feature. I'll cross-check these against the docs."

If their list disagrees with what I extract from docs, that's a high-value flag (probably the docs are stale or the customer mis-prioritized).

**Q1.5 — Internal-only / not billable.** Ask:
> "Are there any features that exist in your code but should NOT be billable? Internal admin tools, debug endpoints, free-forever utilities? List them so I don't propose pricing for them."

---

### Category 2 — Pricing model

Goal: build `customer-context/pricing-model.yaml`. **The hard one** — most customers have a pricing model that doesn't fit a clean template.

**Q2.1 — Pricing model TYPE.** Ask explicitly:
> "Which best describes how you charge? (Pick all that apply, in priority order.)
> - **Pure subscription** — flat monthly / annual fee per user/seat/account
> - **Pure usage-based** — per token / per render / per minute / per API call
> - **Hybrid** — subscription + usage-based overages (e.g., $29/mo for 10k completions, then $0.002/each)
> - **Tiered subscription** — Free / Pro / Team / Enterprise plans with different feature/quota mixes
> - **Credit / wallet system** — customer buys credits, features draw from the wallet at different rates
> - **Enterprise custom** — every customer has a custom contract; no published prices
> - **Other** — describe"

The downstream skills need to know which patterns to expect in the code. A subscription-only customer needs cost-only emission (no usage events fire); a usage-based customer needs every output instrumented; a credit-system customer needs unit-conversion logic.

**Q2.2 — Pricing source of truth.** Ask:
> "Where is the pricing model **defined authoritatively**? Pick ALL that apply:
> - A public pricing page URL
> - An internal Notion / Confluence / Coda page
> - A spreadsheet (provide the path)
> - A configuration file in your repo (provide the path)
> - Sales-engineer-defined per customer (no central source)
> - In code (provide the file path)"

Multiple sources of truth = multiple ingest targets. Conflicts between them is itself a finding.

**Q2.3 — Billable units (customer-stated).** Ask:
> "List the **actual billable units** for each feature. Give me your own labels — examples:
> - 'completion' billed per 1k tokens
> - 'image render' billed per render
> - 'agent run' billed per run + overage on tokens
> - 'transcription' billed per minute
>
> Use your customer-facing terminology, not generic words."

**Q2.4 — Fair-usage thresholds + overages.** Ask whether each unit has:
- Free tier quota (N units free per user / per month / lifetime)
- Soft cap (warn but allow overage)
- Hard cap (block at limit)
- Burst allowance (X over Y minutes is fine)

**Q2.5 — Bundling.** Ask:
> "Do any features combine into bundles? E.g. 'a Pro subscription gives 10k completions + 100 renders + 5 hours of audio combined into one quota pool.' If yes, describe the bundles."

**Q2.6 — Pricing per-customer custom.** Ask:
> "Do you have any **per-customer custom pricing** that's NOT on the public page? If yes, where is it defined and how should I treat it (model the public price, ignore, or flag for finance)?"

---

### Category 3 — Repository + build

Goal: build `customer-context/repo-info.yaml`. Less interpretive than the previous categories — mostly fact-gathering.

**Q3.1 — Primary repo path.** Absolute path. (Avoid `../../../` style relative paths that resolve to a parent workspace by accident.)

**Q3.2 — Multi-repo?** Ask:
> "Is this a single repo, or do you have additional repos that are part of the same product? (Polyrepo / microservices spread across repos.) If yes, list them all with their paths."

**Q3.3 — Sub-services to target.** Ask:
> "Within the repo(s), are there specific services / subdirectories I should focus on? (Or scan everything.) Tell me about services to EXCLUDE too (test fixtures, legacy code being deprecated, vendored dependencies)."

**Q3.4 — Build + test commands.** Ask for the exact commands:
- Install/build (`uv sync`, `npm install`, `go mod download`)
- Run tests (`pytest`, `npm test`, `go test ./...`)
- Linter / format (optional but useful for codemod's PR description)

**Q3.5 — Branch strategy.** Ask:
> "How are PRs created in this repo? Gitflow (develop + feature/*)? Trunk-based (main + short-lived branches)? Custom? What's the branch naming convention I should use for codemod PRs?"

---

### Category 4 — Telemetry stack

Goal: build `customer-context/telemetry-stack.yaml`. Drives brownfield-vs-greenfield codemod branch.

**Q4.1 — Primary tracer.** Ask:
> "What's your **primary distributed tracer**? Pick one (or 'none' if you have no tracing):
> - OpenTelemetry
> - Datadog
> - Sentry
> - New Relic
> - Honeycomb
> - Custom (describe)
> - None — codemod will introduce OTel in greenfield mode"

**Q4.2 — Secondary instrumentation.** Ask:
> "Any additional instrumentation? Helicone, OpenLLMetry, Langfuse, OpenLLMetry, Braintrust, custom?"

**Q4.3 — Request-context propagation.** Ask:
> "How does your code get the current `request_id` / `trace_id` in a handler? Examples:
> - `request.state.request_id` (FastAPI middleware)
> - `req.headers['x-request-id']` (Express)
> - `request.META['HTTP_X_REQUEST_ID']` (Django)
> - From your tracer's API (e.g., `opentelemetry.trace.get_current_span()`)
> - Custom"

**Q4.4 — Existing attribute prefixes.** Ask:
> "Are there attribute prefixes I should AVOID colliding with? E.g., your team already uses `acme.*` or `gen_ai.*` on spans. Codemod will use `moolabs.*` but I want to confirm no conflict."

---

### Category 5 — Terminology overrides

Goal: build `customer-context/terminology.yaml`. Cross-cuts every other skill's output.

**Q5.1 — End-user term.** Ask:
> "What do you call **your customer's customer**? Examples: 'user', 'agent', 'developer', 'tenant', 'workspace', 'organization'."

**Q5.2 — Billable output term.** Ask:
> "What do you call a single billable output? Examples: 'completion', 'generation', 'response', 'render', 'transcript', 'analysis', 'run'."

**Q5.3 — Event type naming.** Ask:
> "When the SDK emits a usage event, what would you want the `type` field to look like? Examples: 'completion.delivered', 'generation.completed', 'render.finished'. I'll use this in codemod inserts."

**Q5.4 — Synonyms / aliases.** Ask:
> "Are there multiple words in your codebase for the same concept? E.g., the docs say 'generation' but the API spec says 'completion'. List any synonym pairs."

**Q5.5 — Unique customer concepts.** Ask:
> "Are there concepts unique to your product that the framework doesn't know about? Examples: 'promptbook', 'agent recipe', 'mood board'. I'll add them to terminology.yaml so other skills don't strip them."

---

### Category 6 — MCP + tooling

Goal: build `customer-context/mcp-config.yaml`. **This is the question I was missing.** The bootstrap itself + downstream skills can use MCP servers (web fetch, doc search, claude-mem, etc.) — but only the ones the customer actually has configured.

**Q6.1 — Active agent surface.** Ask:
> "What agent/IDE are you running this skill in right now? Claude Code? Cursor? Codex CLI? Windsurf? Gemini CLI? OpenCode? Other?"

This determines which MCP servers are available and how the suite renders output.

**Q6.2 — Active LLM.** Ask:
> "Which LLM is currently routing your turns? Claude (Opus/Sonnet/Haiku?), GPT-4 / GPT-4o, Gemini, custom? Some downstream review steps prefer cross-model — I want to know what you have so I can recommend a reviewer model that's different from your main model."

**Q6.3 — MCP servers configured.** Ask:
> "Which MCP servers do you currently have configured? List them all. Common ones the suite can leverage:
> - A web fetch / browser MCP (Playwright, Puppeteer, simple-web) — used to fetch pricing pages, doc URLs, the live Moolabs SDK README
> - A document indexing MCP (claude-mem, Notion, Google Drive, Linear) — used to read internal pricing docs
> - A code-search MCP (GitHub, GitLab, CodeGraph) — used by /cost-billing-discovery to traverse the repo more efficiently
> - A vault / secrets MCP (1Password, Vault) — used to look up the Moolabs SDK API key location WITHOUT exposing the value
> - Any custom internal MCPs"

For each MCP listed, ask: "Should I PREFER this MCP for its category, or is it just available? (Some teams have multiple MCPs and prefer one.)"

**Q6.4 — MCP-restricted scenarios.** Ask:
> "Are there things you DON'T want me to use MCPs for? E.g., 'never fetch external URLs', 'never store anything in claude-mem', 'never send code excerpts to a remote MCP'? List them — they become hard constraints."

---

### Category 7 — Integration + compliance

Goal: build `customer-context/integration-config.yaml`. Where the Moolabs SDK is wired and what compliance constraints the suite must respect.

**Q7.1 — Moolabs SDK API key location.** Ask:
> "Where does the Moolabs SDK API key live in your environment? Examples:
> - Environment variable (`MOOLABS_API_KEY`)
> - AWS Secrets Manager (path: `prod/moolabs/api-key`)
> - HashiCorp Vault
> - 1Password / Doppler / Infisical
> - `.env` file (NOT recommended for prod)
> - To be determined — please configure first"

I do NOT need the key value; I need the resolution PATH so codemod's emitted code knows how to read it.

**Q7.2 — Region(s).** Ask:
> "Which Moolabs region(s) does this customer route to? `sk_use1_*` (US East 1), `sk_apse1_*` (Asia-Pacific SE 1), `sk_euw1_*` (EU West 1), other? Multi-region setup?"

**Q7.3 — Environments.** Ask:
> "How many environments are we instrumenting? Dev only? Dev + staging + prod? Per-environment quirks (e.g., different API keys, different LLM providers used in test vs prod)?"

**Q7.4 — Multi-tenant shape.** Ask:
> "Are your end-users isolated by tenant? Examples:
> - Single-tenant (one customer's data only)
> - Multi-tenant on the same DB with tenant_id column
> - Multi-tenant with separate DBs per tenant
> - Workspace-based (multiple workspaces per account)
>
> The codemod uses tenant identification for attribution; I need to know how YOU model it."

**Q7.5 — Customer IP / data policy.** Ask:
> "Where can the review specs and bootstrap output live?
> - Default: in the customer's repo at `docs/superpowers/reviews/` and `.moolabs/customer-context/`.
> - Externally archived: at a path you specify (and I won't write inside the repo).
> - Hybrid: some artifacts in repo, sensitive ones external.
>
> Any compliance regimes I need to honor (SOC 2, HIPAA, GDPR, FedRAMP)?"

**Q7.6 — PII / regulated data.** Ask:
> "Are there fields in your handlers I should NEVER log as span attributes? Examples: user emails, full prompts (might contain PII), API responses (might contain PHI). I'll add these to the codemod's PII guard."

---

## Workflow

1. **Pre-check.** If `.moolabs/customer-context/` exists and `--refresh` not passed: show the user what's there with timestamps, ask "use as-is / refresh ALL / refresh ONE section". `--section pricing` jumps straight to category 2.

2. **Ask** categories 1 → 7 **in order**. Don't skip ahead. The answers chain (Q5 terminology depends on Q2 pricing units; Q6 MCP depends on Q4 telemetry; etc.).

3. **For each answered category**, immediately synthesize and write the relevant `customer-context/` file. The user sees draft output and can override before moving to the next category.

4. **Validate** every written file against its `$schema` ref. Validation failures surface as clarifying questions ("you said per-token pricing but didn't give me a price — what is it?").

5. **Report**:

```
customer-context/ generated at .moolabs/customer-context/

  product-summary.md        N lines from M source docs ingested
  pricing-model.yaml        <type>, K plans, J overage rates
  repo-info.yaml            <repo-type>, S services
  telemetry-stack.yaml      <tracer>, <mode>
  terminology.yaml          T overrides, U synonym groups
  mcp-config.yaml           <agent-surface>, V MCPs available
  integration-config.yaml   <key-location>, <region>, <env-count> envs

Next: run /cost-billing-discovery <repo>
```

## Files written

```
.moolabs/customer-context/
├── README.md                      # one-paragraph summary + when to refresh
├── product-summary.md             # synthesized from ALL doc sources
├── pricing-model.yaml             # pricing model TYPE + structure + sources
├── repo-info.yaml                 # repo shape + build/test commands + branch strategy
├── telemetry-stack.yaml           # tracer + mode + context propagation + collision-avoid prefixes
├── terminology.yaml               # overrides + synonyms + unique concepts
├── mcp-config.yaml                # agent surface + LLM + MCP inventory + MCP preferences + restrictions
├── integration-config.yaml        # SDK key location + region + environments + tenancy + IP policy + PII rules
└── bootstrap-log.yaml             # when ran, which LLM, which sources, which questions skipped
```

## What this skill MUST NOT do

- **Never** invent answers. If the customer's answer is unclear, ASK A FOLLOW-UP — don't fill in.
- **Never** silently default. Every default surfaces as a proposal the customer confirms or overrides.
- **Never** skip a question because "it seems obvious from context." Customers contradict their own context all the time.
- **Never** persist `customer-context/` outside the customer's repo unless they pass `--external-context-path=<path>` or answer Q7.5 with "externally archived".
- **Never** leak content from one customer's bootstrap into another. `customer-context/` is per-integration, not global.
- **Never** assume a specific MCP is available — ask Q6.3 first.

## Why "use whatever LLM they're using"

This skill is intentionally written so it runs under any agent surface (Claude Code, Cursor, Copilot, Gemini CLI, OpenCode). Q6.1 + Q6.2 capture the active surface + LLM. The synthesis step ("read this product doc and summarize the features") is LLM work — no platform-specific tooling required.

When deployed to a customer running GPT-4 via Cursor, the bootstrap runs under GPT-4 and produces the same customer-context shape. Downstream skills don't care which LLM generated the context.

## Reference files

- `references/synthesis-prompts.md` — the prompts the bootstrap uses for product-doc summarization, pricing-page extraction, etc.
- `references/refresh-triggers.md` — when to re-run bootstrap (product launches, pricing changes, repo refactors).
- `references/customer-context-readers.md` — for skill authors: how the other 5 skills read from `customer-context/`.

## Assets

- `assets/customer-context-templates/product-summary.template.md`
- `assets/customer-context-templates/pricing-model.template.yaml`
- `assets/customer-context-templates/repo-info.template.yaml`
- `assets/customer-context-templates/telemetry-stack.template.yaml`
- `assets/customer-context-templates/terminology.template.yaml`
- `assets/customer-context-templates/mcp-config.template.yaml`            # NEW in v0.2.0
- `assets/customer-context-templates/integration-config.template.yaml`   # NEW in v0.2.0
