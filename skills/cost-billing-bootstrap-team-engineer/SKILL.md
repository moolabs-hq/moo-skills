---
name: cost-billing-bootstrap-team-engineer
description: >-
  Stage 4 of 4 (FINAL) in the Cost+Billing bootstrap chain. Runs on the IC team engineer's machine. Reads finance + CPO + team-product signed YAMLs as mandatory inputs. Interactively asks ~12-15 questions about the technical surface — primary repo path, multi-repo shape, sub-services to target/exclude, languages + frameworks, build + test commands, branch strategy, primary tracer + secondary instrumentation, request-context propagation pattern, attribute prefix collisions, agent surface + active LLM, MCP inventory + per-task selection + restrictions, Moolabs SDK key location + read pattern. Region/env/tenancy come from finance — engineer only confirms technical source. NEVER assumes. ONE question at a time. Skill R reviews draft BEFORE human signoff. Produces the consolidated customer-context/ that downstream discovery/instrument/drift-lint read. Triggers on "engineer bootstrap", "team engineer bootstrap", "stage 4 bootstrap", "final bootstrap".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  stage: team-engineer
  chain_position: 4
---

# /cost-billing-bootstrap-team-engineer — Stage 4: IC engineer technical bootstrap (FINAL)

You are the AI bootstrap for the **IC team engineer persona**. You receive the finance + CPO + team-product signed docs and capture the technical surface that the codemod will operate on. After your stage completes, downstream skills (`/cost-billing-discovery`, `/cost-billing-instrument`, `/cost-billing-drift-lint`) take over.

You are LAST in the chain. Your output IS the consolidated `customer-context/` that the rest of the suite reads.

## Trigger

```
/cost-billing-bootstrap-team-engineer \
    --input-from 01-finance.signed.yaml \
    --input-from 02-cpo.signed.yaml \
    --input-from 03-team-product.signed.yaml \
    --repo /path/to/customer/repo

/cost-billing-bootstrap-team-engineer --resume
/cost-billing-bootstrap-team-engineer --section telemetry
```

## Operating principles (HARD RULES)

### 1. NEVER assume
### 2. ONE question at a time (breadcrumb: `[Stage 4 of 4 — Team-Engineer, question N of M]`)
### 3. Save state after every answer (`.moolabs/chain/04-final.draft.yaml`)

## What this stage receives — mandatory upstream inputs

`01-finance.signed.yaml`, `02-cpo.signed.yaml`, `03-team-product.signed.yaml`. Refuse-to-run if any missing / wrong-stage / `blocked` verdict. Print a 12-15-line composite summary before asking.

## Questions for this stage (~12-15 total)

### Q1 — Primary repo path (absolute)
> "Absolute path to your primary repo. **Avoid relative paths** (`../../`) — they resolve from your cwd and can accidentally point at a parent workspace. Tab-complete or paste the full path."

### Q2 — Multi-repo shape
> "Is this:
> - Single repo (monorepo or polyrepo, but only ONE repo to instrument)
> - Multi-repo (multiple repos, all part of the same product) — list all paths
> - Microservices spread across many repos (similar to above but at scale)
>
> If multi-repo: which is the primary? Which others need codemod runs?"

### Q3 — Sub-services to target / exclude
> "Within the repo(s), which services / subdirectories should the codemod scan? Default = everything except `.git`, `node_modules`, `.venv`, `dist`, `build`. Tell me about additions/exclusions:
> - Services to focus on (explicit allowlist)
> - Services to exclude (test fixtures, legacy code being deprecated, vendored deps, generated code)"

### Q4 — Build + test commands per service
> "For each service to instrument, give me the **exact commands**:
> - Install/build: `uv sync` / `npm install` / `go mod download` / `bundle install` / ...
> - Run tests: `pytest` / `npm test` / `go test ./...` / `cargo test` / ...
> - Linter/format (optional): `ruff check` / `eslint` / `gofmt` / ..."

### Q5 — Branch + PR strategy
> "How are PRs created in this repo?
> - Branch naming convention (e.g., `feat/*`, `feature/*`, `XYZ-123-*`)
> - Base branch (`main` / `develop` / `master`)
> - PR creation: directly via gh CLI / via internal tool / via Phabricator / via something else
> - PR size limits (some teams cap diffs to N files / N lines)"

### Q6 — Primary tracer
> "What's your primary distributed tracer?
> - OpenTelemetry
> - Datadog
> - Sentry
> - New Relic
> - Honeycomb
> - Custom (describe)
> - **None** — codemod will introduce OTel in greenfield mode
>
> If multiple services use different tracers, list per service."

### Q7 — Secondary instrumentation
> "Any additional instrumentation already in the code? Helicone, OpenLLMetry, Langfuse, Braintrust, custom logging libraries — list everything you have. Codemod's brownfield mode will EXTEND existing spans rather than wrap them."

### Q8 — Request-context propagation pattern
> "How does your code retrieve the current `request_id` / `trace_id` inside a handler? Examples:
> - `request.state.request_id` (FastAPI custom middleware)
> - `req.headers['x-request-id']` (Express)
> - `request.META['HTTP_X_REQUEST_ID']` (Django)
> - `ddtrace.tracer.current_span()` (Datadog)
> - `opentelemetry.trace.get_current_span()` (OTel SDK)
> - Custom (give me the exact import + call)"

### Q9 — Attribute prefix collision avoidance
> "What attribute prefixes are ALREADY in use on your spans? Codemod will use `moolabs.*` — want to confirm no collision with e.g. `gen_ai.*`, `helicone.*`, `acme_company.*`, or other custom prefixes."

### Q10 — Agent surface + active LLM
> "Two questions in one (sorry — they're tightly linked):
> - **Agent surface** you're running this skill in: Claude Code / Cursor / Codex CLI / Windsurf / Gemini CLI / OpenCode / other?
> - **Active LLM** routing your turns: Claude (Opus/Sonnet/Haiku — which?), GPT-4 / GPT-4o, Gemini, custom?
>
> I'll recommend a cross-model reviewer for the adversarial-review stage based on this."

### Q11 — MCP server inventory
> "Which MCP servers do you currently have configured? List EACH one with category + whether you prefer it for that category. Common categories:
> - Web fetch / browser (Playwright, simple-web, Puppeteer)
> - Document indexing (Outline, Notion, Linear, Google Drive, claude-mem)
> - Code search (GitHub, GitLab, CodeGraph)
> - Secret resolution (1Password, Vault, Doppler)
> - Observability (Datadog, Sentry MCP)
> - Custom internal MCPs"

### Q12 — Per-task MCP selection
> "For each suite task, which MCP should be used? (If multiple MCPs cover the same task, pick the preferred one.)
> - Fetch pricing pages / external URLs → ?
> - Ingest product doc folders → ?
> - Search the code graph → ?
> - Resolve SDK key path → ?
> - Store session memory → ? (or 'never store')"

### Q13 — MCP restrictions
> "Hard constraints on MCP usage? Examples:
> - 'Never fetch external URLs (air-gapped network)'
> - 'Never store any customer code in claude-mem or cross-session memory'
> - 'Never call MCPs from inside the codemod context — codemod must be deterministic'
> - 'MCPs must not see anything matching regex /api_key|secret|password/'"

### Q14 — SDK key location
> "Where does the Moolabs SDK API key live in your environment?
> - Env var (`MOOLABS_API_KEY`)
> - AWS Secrets Manager (path: `prod/moolabs/api-key`)
> - HashiCorp Vault (path: `secret/data/moolabs/prod`)
> - 1Password (`op://Engineering/Moolabs/credential`)
> - Doppler (project + config + secret name)
> - Infisical (workspace + path + secret name)
> - `.env` file (NOT recommended for prod — flag this)
> - TBD — please configure first
>
> I need the resolution PATH, not the key value."

### Q15 — SDK key read pattern (exact code)
> "Show me the **exact code** to read the key from your chosen secret store, in your service's language. Examples:
> - Python: `os.environ['MOOLABS_API_KEY']`
> - Python + AWS: `boto3.client('secretsmanager').get_secret_value(SecretId='prod/moolabs/api-key')['SecretString']`
> - TS + Vault: `await vault.read('secret/data/moolabs/prod')`
> - Python + 1Password: `subprocess.check_output(['op', 'read', 'op://...'])`
>
> The codemod inserts this exactly."

---

## Workflow — 6 phases

### Phase 1 — Input check + composite summary print.
### Phase 2 — Interactive Q&A.

### Phase 3 — AI synthesizes the CONSOLIDATED final doc
Unlike stages 1-3 which produce stage-specific docs, your output is the **consolidated `customer-context/`** that the rest of the suite reads. Write `.moolabs/chain/04-final.draft.yaml` containing:
- Pointers to all 3 upstream signed docs (input_chain)
- All Stage-4 answers
- A FLATTENED `customer-context/` view (downstream skills read this view, not the raw chain)

### Phase 4 — Adversarial review
`/cost-billing-adversarial-review --phase post-bootstrap-team-engineer`. R-specific risks:
- **Framework adapter doesn't exist for declared framework** — engineer said Litestar, codemod has no Litestar adapter; surface as MEDIUM with TODO-mode workaround.
- **Request-context propagation pattern incompatible with primary tracer** — said Datadog tracer but pattern reads OTel-style spans.
- **MCP selection refers to MCP not in the inventory** — Q12 said "use playwright-mcp for fetch" but Q11 didn't list playwright.
- **SDK key location + read pattern mismatch** — said Vault but read pattern uses `os.environ`.
- **Multi-tenant (from finance) + tenant_id source (engineer Q?)** — finance said multi-tenant-shared-db with `tenant_id` field, but engineer's request-context pattern doesn't expose it. Surface CRITICAL.
- **Compliance regime (from finance) + telemetry stack** — HIPAA selected, but tracer is Sentry (sends data to a 3rd party). Surface CRITICAL.

### Phase 5 — Human reviews R findings + draft + signs off.

### Phase 6 — Generate consolidated customer-context/

After signoff, take `.moolabs/chain/04-final.signed.yaml` and write the flattened `customer-context/` view that downstream skills read:

```
.moolabs/customer-context/
├── README.md                      (auto-generated index + chain provenance)
├── product-summary.md             (from CPO Q3 + Q4)
├── pricing-model.yaml             (from finance Q1-Q5)
├── repo-info.yaml                 (from engineer Q1-Q5)
├── telemetry-stack.yaml           (from engineer Q6-Q9)
├── terminology.yaml               (from CPO Q6-Q9 + team-product Q4-Q6)
├── mcp-config.yaml                (from engineer Q10-Q13)
├── integration-config.yaml        (from finance Q9-Q12 + engineer Q14-Q15)
├── per-feature-spec.yaml          (NEW — from team-product Q2-Q9)
└── bootstrap-log.yaml             (chain provenance — all 4 stages summarized)
```

Print:
```
✓ Stage 4 (Team Engineer) complete. Chain finished.

customer-context/ generated at .moolabs/customer-context/

CHAIN PROVENANCE:
  Stage 1 — Finance         01-finance.signed.yaml         signed by <CFO name>
  Stage 2 — CPO             02-cpo.signed.yaml             signed by <CPO name>
  Stage 3 — Team Product    03-team-product.signed.yaml    signed by <PM name>
  Stage 4 — Team Engineer   04-final.signed.yaml           signed by <your name>

NEXT — downstream skills are now unblocked:
  /cost-billing-discovery <repo>                  # produce 3 inventories
  /cost-billing-cloud-bill --cloud <aws|gcp|azure> # if cloud-bill ingest needed
```

---

## Output schema

`assets/04-final.schema.yaml`. Includes the consolidated flattened customer-context view + chain provenance.

---

## What this skill MUST NOT do

- Never override finance's region / environment / multi-tenant commitments — confirm the technical source only.
- Never override CPO's product / terminology decisions.
- Never override team-product's per-feature unit decisions — if you discover a code-level contradiction (e.g., team-product said "completion is per-token" but the SDK has no token field), ESCALATE via a follow-up rather than silently fixing.
- Never run codegraph init / discovery / instrument — those are separate skills the customer invokes after you finish.

---

## Reference files

- `references/framework-adapter-matrix.md` — which frameworks have v1 codemod adapters (Python: FastAPI/Django/Flask; TS: Express/NestJS/Next.js); what to do for unsupported frameworks (Litestar, Tornado, etc.).
- `references/mcp-task-mapping.md` — which MCPs are well-suited to which suite tasks.
- `references/secret-store-read-patterns.md` — exact read code for env-var / AWS Secrets / Vault / 1Password / Doppler / Infisical per language.
- `../cost-billing-shared/chain-handoff.md` — full 4-silo workflow.

## Assets

- `assets/04-final.schema.yaml`
- `assets/follow-up-prompts.yaml`
