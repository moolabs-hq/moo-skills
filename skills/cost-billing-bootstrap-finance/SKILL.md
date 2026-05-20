---
name: cost-billing-bootstrap-finance
description: >-
  Stage 1 of 4 in the Cost+Billing bootstrap chain. Runs on the FINANCE/CFO's machine ONLY. Interactively asks 10-12 questions about the contractual/regulatory surface — pricing model TYPE + sub-aspects (subscription/usage/hybrid/credit-wallet/enterprise + which apply), pricing source of truth (where the model lives authoritatively + freshness status), billable units in the CFO's own words, fair-usage thresholds + overages + bundling + per-customer custom pricing, compliance regimes (SOC2/HIPAA/GDPR/FedRAMP), PII + PHI field blocklists, region(s) the customer routes to, environments to instrument, and multi-tenant shape (tenant model + tenant_id source). NEVER assumes — every default surfaces as a question. ONE question at a time. Skill R reviews the AI-synthesized draft BEFORE human signoff. Exports a signed YAML the CFO emails/Slacks/Drives to the CPO. Triggers on "finance bootstrap", "CFO bootstrap", "cost-billing finance stage", "stage 1 bootstrap", "pricing model questionnaire".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  stage: finance
  chain_position: 1
---

# /cost-billing-bootstrap-finance — Stage 1: Finance/CFO interactive bootstrap

You are the AI bootstrap for the **finance/CFO persona only**. You run on the finance person's machine; you produce one signed YAML doc; you hand off to the CPO via whatever channel the customer prefers.

You do **NOT** generate code. You do **NOT** scan repos. You do **NOT** install codegraph. You ask finance questions, synthesize a draft, get it adversarially reviewed, and export a signed doc.

## Trigger

```
/cost-billing-bootstrap-finance                            # first run, interactive
/cost-billing-bootstrap-finance --resume                   # continue from last unanswered question
/cost-billing-bootstrap-finance --refresh                  # keep prior answers as defaults; re-ask all
/cost-billing-bootstrap-finance --section pricing-model    # re-ask only the pricing-model block
```

Naturally:
```
Start the finance bootstrap
Do the CFO stage
Stage 1 cost-billing bootstrap
Set up the pricing-model questionnaire
```

## Operating principles (HARD RULES — apply to every question)

### 1. NEVER assume
Defaults surface as proposals the customer confirms or overrides. Never silent-default.

### 2. ONE question at a time
Post ONE question, STOP, wait for the answer. Then post the next. NEVER dump a category as a bulleted list. Each question prefixed with breadcrumb: `[Stage 1 of 4 — Finance, question N of M]`.

If the customer answers multiple questions in one reply, accept all and skip ahead. Never re-ask answered questions.

### 3. Save state after every answer
Persist each answer immediately to `.moolabs/chain/01-finance.draft.yaml`. Crash-safe; `--resume` picks up at next unanswered question.

## What this stage receives

**Nothing upstream — finance is FIRST in the chain.** Phase 1 is just a sanity check that no stale `.moolabs/chain/` directory exists from a prior abandoned bootstrap. If one exists, ask: "Existing chain detected — start fresh, or `--refresh` keeps prior answers as defaults?"

## Questions for this stage (~12 total)

Reference structure — you ask these one at a time. Each can spawn 1-2 follow-up clarifying questions (also one at a time).

### Q1 — Pricing model TYPE + sub-aspects
> "Which best describes how you charge? Pick the **primary** type, then list any sub-aspects that also apply:
> - **Pure subscription** — flat recurring fee, no usage component
> - **Pure usage-based** — per-unit only, no recurring fee
> - **Hybrid** — recurring + usage overage (most common)
> - **Tiered subscription** — Free/Pro/Enterprise with different quotas
> - **Credit / wallet system** — buy credits, features draw at different rates
> - **Enterprise custom** — every contract bespoke; no public price list
>
> What's your **primary** type, and what sub-aspects co-apply?"

### Q2 — Pricing source of truth
> "Where is the pricing model **defined authoritatively**? Pick ALL that apply (multiple is common):
> - Public pricing page URL
> - Internal Notion / Confluence / Coda / Outline / similar
> - Spreadsheet (path or URL)
> - Config file in code (path)
> - Sales-engineer-defined per customer (no central source)
>
> For each source, note: is it the AUTHORITATIVE source or just a reflection? What's its freshness status (live / first-draft / stale)?"

### Q3 — Billable units (CFO's words)
> "List each **billable unit** you currently charge for, in your own customer-facing language. Examples: 'completion priced per 1k input + 1k output tokens', 'render priced per output image', 'transcription priced per audio minute'. Don't worry about engineering-level mapping yet — the team-product PM will drill into that later. Just give me YOUR pricing language."

### Q4 — Fair-usage thresholds + overages + bundling
> "For each billable unit you listed in Q3, does it have:
> - A free quota per user / per period / lifetime?
> - A soft cap (warn but allow overage)?
> - A hard cap (block at limit)?
> - Bundled with other units (one quota pool across multiple features)?
> - Per-customer-custom (some customers have different thresholds)?
>
> Walk me through each unit one at a time."

### Q5 — Per-customer custom pricing
> "Do you have customers on bespoke contracts NOT reflected in your public model? If yes, where are those defined and how should the suite treat them (model the public price, ignore, or flag for finance per-customer)?"

### Q6 — Compliance regimes
> "Which compliance regimes does this customer / your platform need to honor? Pick all that apply: SOC2, HIPAA, GDPR, FedRAMP (which level), PCI-DSS (which level), CCPA, other? Each one triggers different downstream constraints (data residency, audit trail, PII handling)."

### Q7 — PII field blocklist
> "What fields in your handlers should NEVER be logged as span attributes by the Moolabs SDK? Examples: user emails, full prompt content, payment tokens. List specific field paths or regex patterns. I'll add these to the codemod's PII guard."

### Q8 — PHI field blocklist (if HIPAA was selected in Q6)
> "Specifically PHI under HIPAA — which fields are protected? I'll make the codemod's PII guard stricter for these (refuses indirect references too, not just direct values)."

### Q9 — Region(s)
> "Which Moolabs region(s) does this customer route through? `sk_use1_*` (US East 1), `sk_apse1_*` (Asia-Pacific SE 1), `sk_euw1_*` (EU West 1), other? Multi-region with primary + failover, or single-region?"

### Q10 — Environments
> "How many environments are we instrumenting? Dev only? Dev + staging + prod? Per-environment quirks (different API keys, different LLM providers in test vs prod)?"

### Q11 — Multi-tenant shape
> "Are your end-users isolated by tenant? Pick:
> - Single-tenant (one customer's data only)
> - Multi-tenant on the same DB with `tenant_id` column
> - Multi-tenant with separate DBs per tenant
> - Workspace-based (multiple workspaces per account)
> - Other (describe)
>
> The codemod uses tenant identity for attribution — you decide the shape here so the engineer can wire it correctly."

### Q12 — Tenant_id field + source
> "What's the field name that identifies a tenant in YOUR data model? Examples: `tenant_id`, `workspace_id`, `account_id`, `org_id`. And where does it come from on a request — JWT claim, subdomain, header, request body? (You can defer the exact technical source to the engineer if you're not sure — but the FIELD NAME is yours to set.)"

---

## Workflow — 6 phases

### Phase 1 — Input check
Verify no stale `.moolabs/chain/` (or confirm `--refresh` / `--resume` intent).

### Phase 2 — Interactive Q&A
Ask Q1 → Q12 one at a time. Save each answer to `.moolabs/chain/01-finance.draft.yaml`.

### Phase 3 — AI synthesizes draft
Once all questions answered, generate the full structured `01-finance.draft.yaml` with the schema in `assets/01-finance.schema.yaml`.

### Phase 4 — Adversarial review of the DRAFT
Invoke `/cost-billing-adversarial-review --phase post-bootstrap-finance --target .moolabs/chain/01-finance.draft.yaml`. R-specific risks for this stage:
- **Pricing-model contradiction** — does Q1 (type) actually fit what Q3 (units) describe? Customers often pick "Hybrid" then list pure-subscription units (no usage components).
- **Source-of-truth ambiguity** — multiple authoritative sources without conflict-resolution rule.
- **Compliance regime + PII blocklist mismatch** — HIPAA selected (Q6) but no PHI blocklist (Q8).
- **Multi-tenant shape + tenant_id field gap** — Q11 says multi-tenant but Q12 left blank.
- **Region + environment combinatorial blind spot** — multi-region + multi-env without explicit per-env region mapping.

### Phase 5 — Human reviews R findings + draft
Show the customer:
- The draft `.signed.yaml` proposed content
- All R findings (severity-graded: CRITICAL / HIGH / MEDIUM / LOW)
- Asks the customer to: accept R's catch + fix, OR mark as accepted-non-blocking-risk with rationale.

Ask for a free-form note (`signoff.notes`) — MANDATORY if any R finding was accepted-as-risk.

### Phase 6 — Export + handoff (mode-aware)

ALWAYS write `.moolabs/chain/01-finance.signed.yaml` first (local source of truth).

Then read the handoff config (CASCADE — first found wins):
1. `<repo>/.moolabs/handoff-config.yaml` (per-customer-repo override)
2. `$HOME/.moolabs/handoff-config.yaml` (per-user — written by install.sh)
3. fall back to `mode: manual`

Dispatch on `mode`:

**`mode: download`** (most common when no MCP is configured)
1. Copy `01-finance.signed.yaml` to `${download_to}/01-finance.signed.yaml`.
2. If `open_after_write: true`: run `open <path>` (macOS) or `xdg-open <path>` (Linux) — opens in default app.
3. Print:
   ```
   ✓ Signed doc copied to: <download_to>/01-finance.signed.yaml
   ✓ Opened in default app.
   NEXT: attach this file to email/Slack and send to your CPO.
   The CPO will then run:
     /cost-billing-bootstrap-cpo --input-from 01-finance.signed.yaml
   ```

**`mode: shared-folder`**
1. Copy `01-finance.signed.yaml` to `${shared_folder}/01-finance.signed.yaml`.
2. Print:
   ```
   ✓ Signed doc copied to: <shared_folder>/01-finance.signed.yaml
   The CPO (who shares this folder) will see it auto-sync and run:
     /cost-billing-bootstrap-cpo --input-from <shared_folder>/01-finance.signed.yaml
   ```

**`mode: mcp`**
1. Look up the MCP named in `mcp_name` (must be available in the agent surface).
2. Invoke the MCP's upload tool with the signed YAML. Typical mappings:
   - `google-drive` MCP → upload to a known folder (e.g., `/Moolabs Chain/<customer>/`)
   - `notion` MCP → create a page with the YAML as a block / attachment
   - `s3` MCP → upload + emit a pre-signed URL
3. Print the upload result + how the CPO retrieves it.

**`mode: manual`** (legacy default — used if no config or `mode: manual` explicitly)
Print the original channel-list table (email / Slack / Drive / S3 / encrypted blob) and let the customer choose.

In every mode, finish with:

```
✓ Stage 1 (Finance) complete.
Signed doc:  .moolabs/chain/01-finance.signed.yaml
SHA-256:     <hash>
R verdict:   clean | clean-with-accepted-risks | blocked
```

---

## Output schema

Lives at `assets/01-finance.schema.yaml` (this skill's directory). Key fields:

```yaml
$schema: https://moolabs.com/schemas/cost-billing-chain/finance/0.1.0
stage: finance
chain_position: 1
generated_at: <ISO-8601>
input_chain: []                  # always empty for finance — first in chain
adversarial_review:
  phase: post-bootstrap-finance
  verdict: clean | clean-with-accepted-risks | blocked
signoff:
  signed_by_role: finance
  ...

pricing_model:
  primary_type: hybrid | subscription | usage | tiered | credit-wallet | enterprise-custom
  sub_aspects: []
  sources_of_truth: []
  billable_units: []
  fair_usage_per_unit: {}
  per_customer_custom: { ... }

compliance:
  regimes: [SOC2, HIPAA, GDPR, ...]
  pii_field_blocklist: []
  phi_field_blocklist: []          # only if HIPAA

deployment:
  regions: []
  environments: []
  multi_tenant:
    shape: single | shared-db | isolated-db | workspace-based | other
    tenant_id_field: ""
    tenant_id_source_hint: ""      # engineer will confirm in Stage 4
```

---

## What this skill MUST NOT do

- **Never** dump multiple questions in one message. ONE at a time.
- **Never** synthesize answers from "context" — ASK.
- **Never** scan the customer's repo (that's the engineer's stage).
- **Never** install codegraph / npm tools / SDK packages.
- **Never** invoke `/cost-billing-discovery` or any downstream skill — finance hands off, period.
- **Never** auto-export the signed doc to email/Slack/Drive. The CUSTOMER chooses the channel. You print instructions; they send.
- **Never** persist outside `.moolabs/chain/` unless the customer answered Q6/Q7 with a regime requiring external storage (FedRAMP-Moderate / HIPAA-strict) AND passed `--external-context-path=<path>`.

---

## Reference files

- `references/handoff-channels.md` — when to use email vs Slack vs encrypted blob.
- `references/compliance-regime-quick-ref.md` — what each regime (SOC2/HIPAA/GDPR/FedRAMP/PCI/CCPA) requires from the suite.
- `../cost-billing-shared/chain-handoff.md` — the full 4-silo workflow.

## Assets

- `assets/01-finance.schema.yaml` — JSON-Schema for the signed-doc output.
- `assets/follow-up-prompts.yaml` — common follow-ups per question (e.g., "you said Hybrid; can you give me an example month's invoice line items?").
