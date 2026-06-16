# PRD: AWS CUR → Acute ingestion CLI (customer self-serve)

> **⚠️ Update (2026-06-15) — pivoted to CUR 2.0 + CSV. This reverses Decision 6.**
> The implementation now uses **CUR 2.0 / AWS Data Exports** (`bcm-data-exports:CreateExport`),
> **not** Legacy CUR (`cur:PutReportDefinition`). The body below documents the original
> Legacy-CUR design and is retained for history; where it conflicts with this block, this block wins.
> Concretely changed:
> - **API:** `bcm-data-exports` (`CreateExport`/`ListExports`/`GetExport`) over a SQL `QueryStatement`
>   on the `COST_AND_USAGE_REPORT` table — not `cur:PutReportDefinition`/`DescribeReportDefinitions`.
> - **Format:** `TEXT_OR_CSV` + `GZIP` (read with stdlib `csv`+`gzip`; **pyarrow dropped**) — not Parquet.
> - **Overwrite:** `OVERWRITE_REPORT` → one current file set, so the read path is a recursive glob of
>   `.csv.gz` with **no manifest read and no stale-assembly dedup** (that whole bug class is designed out).
> - **Columns:** region is `product_region_code` (was `product_region`); tags come from the single
>   `resource_tags` **map** column (JSON in CSV), not per-key `resource_tags_user_*` columns. The column
>   map is now deterministic from the export SQL — no manifest auto-fill (OQ-2 moot).
> - **IAM (configure):** `bcm-data-exports:CreateExport`/`ListExports`/`GetExport` + `s3:*` as before.
>   `cur:*`, `ce:*`, `organizations:*` are no longer used by any command.
> - **Gain:** CUR 2.0 unlocks `INCLUDE_IAM_PRINCIPAL_DATA` for Bedrock IAM-principal attribution
>   (the tradeoff OQ-5 called out under Legacy).
> - **Validation gap:** the CSV read path is validated against hermetic fakes only; it is **NOT yet
>   validated against a real CUR 2.0 delivery** (export takes ~24h to first-deliver). The exact CSV
>   serialization of the `resource_tags` map is assumed to be JSON and parsed defensively.
>
> **One-line:** A small, deterministic **customer-installed CLI package** (`moo-cloud-bill`, name TBD — OQ-9) that (1) **discovers or sets up an AWS Legacy CUR via the AWS SDK** — reusing an existing CUR when present, else creating one after explicit confirmation (`configure`), (2) on a schedule **reads the delivered CUR, aggregates hourly lines to a daily grain, and POSTs daily batches to Acute's `/api/v1/cloud-billing/import` with `Authorization: Bearer`** (`push`), and (3) surfaces untagged spend and seeds Acute's `resource_service_map` so previously-untracked spend becomes attributable (`scan`/`review`/`seed`). The Moolabs API key (generated in the Moolabs UI) is captured once via a hidden prompt (`init`). **No agent/LLM at runtime.** Acute already does attribution after import — **this PRD changes no moo-acute code.**
>
> **Packaging:** CLI tool / pip-installable package, run by the customer (`configure` once, `push` via cron). NOT an agent skill. **Scope: AWS only** — GCP/Azure ship as separate tools later.

---

## 0. Prerequisites

### Gate status

- [x] **Problem validated** — *untracked / unattributable AWS spend*: AI/infra spend (e.g. `AmazonBedrock`, S3 render-retry PUTs) lands in the customer's bill without the tags needed to attribute it. Acute can attribute it only if (a) the CUR rows are imported and (b) `resource_service_map` maps the untagged resources. Neither happens today; there is no ingestion path from a customer CUR into Acute.
- [x] **Deliverable identified** — a **new standalone CLI package**, customer-installed. (Supersedes the prose-only `cost-billing-cloud-bill` skill for the AWS CUR path — see OQ-10 on what happens to that skill.) **moo-acute is the consumer and is NOT modified.**
- [x] **Why a CLI, not a skill** — the runtime is a deterministic, idempotent, scheduled data push; an LLM in that loop would add nondeterminism, latency, and per-run cost to financial-data ingestion for zero benefit. The only judgment step (untagged-spend absorb-vs-fix) is a one-time human review of a YAML, not a runtime agent task.
- [x] **Architecture read** — Acute import code, model unique index, `auth.py`, cloud-billing tests; AWS Legacy CUR setup verified against the Datadog CCM reference. All review findings (C1/C2/I1/I2) resolved below.
- [x] **Stakeholder alignment** — suite owner (user) owns both repos; decisions confirmed interactively 2026-06-14.

### THE CONTRACT (pinned to moo-acute)

Source: `moo-acute/app/api/v1/cloud_billing/router.py`, `app/services/cloud_billing_service.py`, `app/models/cloud_cost_import.py`, `app/middleware/auth.py`, `tests/test_cloud_billing_service.py`.

**Acute is a push API with NO CUR reader** (zero hits for `parquet`/`boto3`/`s3` in moo-acute; `/sync` pull is an explicit stub). The CLI reads the CUR and pushes rows.

| Aspect | Value | Evidence |
|--------|-------|----------|
| Endpoint | `POST /api/v1/cloud-billing/import` → `201` | router.py |
| **Auth header** | **`Authorization: Bearer <customer key>`** — Bearer = external/customer trust domain (direct to public ALB); `X-API-Key` is internal (BFF→ACUTE). `_select_credential` prefers Bearer. **tenant derived from key — never in body.** | auth.py:74, `_select_credential`, `get_tenant_from_credential` |
| Body | `ImportBatchRequest`: `cloud_provider` ∈ {aws,gcp,azure}, `billing_period_start`, `billing_period_end` (end>start), `reporting_currency` (default USD), `rows[]` (min 1) | router.py |
| Row | `CloudCostRowInput`: `service_name` (req), `resource_id?`, `region?`, `usage_type?`, `cost` (Decimal ≥ 0, req), `currency` (default USD), `tags` (dict) | router.py |
| **Row grain (MANDATORY)** | Plain `db.add()` under a **partial-unique index** on `(tenant, provider, period_start, period_end, service_name, COALESCE(resource_id), COALESCE(region), COALESCE(usage_type))` WHERE `active`. **Duplicate grain within a batch → IntegrityError → whole batch aborts.** The CLI MUST pre-aggregate (sum `cost`) to exactly this grain. | model unique index; `import_batch` insert loop (no upsert); test row is pre-aggregated |
| **Period** | One `(start,end)` per batch. We use **DAILY** periods (`[day 00:00Z, +1d 00:00Z)`). Re-POST of a `(provider, day)` supersedes. | C2 decision |
| Idempotency | **Re-import supersedes** the prior active batch atomically (advisory lock + nested txn). `push` is safely re-runnable per day; no client dedup. | `import_batch` |
| Attribution surface | `resource_id → service/team/environment` via `acute.resource_service_map`, upserted by `POST /api/v1/cloud-billing/resource-map` on conflict `(tenant, cloud_provider, resource_id)`. | router.py |
| FX | Locked at import inside Acute. CLI sends native `cost`+`currency`; does NOT convert. | `_apply_fx` |

### AWS Legacy CUR setup (verified — Datadog CCM reference)

Source: https://docs.datadoghq.com/cloud_cost_management/setup/aws/?tab=cloudformation (Legacy CUR; Datadog does not support CUR 2.0 / BCM Data Exports).

| Setting | Value |
|---------|-------|
| Export type | **Legacy CUR** (`cur:PutReportDefinition` / CFN `AWS::CUR::ReportDefinition`) |
| Time granularity | **Hourly** |
| Format | Parquet (preferred — underscore-normalized columns) or CSV+GZIP |
| Report versioning | **Create new report version** (`CREATE_NEW_REPORT`) — read the latest manifest |
| Schema elements | **Include resource IDs** (`RESOURCES`) + **Split cost allocation data** (`SPLIT_COST_ALLOCATION_DATA`) |
| Refresh | `RefreshClosedReports = true` |
| S3 prefix | **Must NOT be empty, start with `/`, or end with `/`.** Mid-path `/` OK. Use e.g. `cost/hourly`. |
| Min IAM (read) | `s3:ListBucket` · `s3:GetObject` (`…/PREFIX/REPORT_NAME/*`) · `cur:DescribeReportDefinitions` · `ce:Get*` · `organizations:Describe*`/`List*` |
| CUR API region | **us-east-1 only** (`put_report_definition` must target us-east-1). |

### Knowledge links

- Acute contract: `moo-acute/app/api/v1/cloud_billing/router.py`, `app/middleware/auth.py`
- AWS Legacy CUR: Datadog CCM AWS setup (above); AWS CUR (`cur`) API / `AWS::CUR::ReportDefinition`
- Reporting currency + Moolabs Bearer key: from the customer's Moolabs account/config (the CLI reads it from env/config, see US-005)

---

## 1. Overview

Acute can attribute AWS spend, but only for rows pushed into it whose untagged resources are mapped. Today there's no path from a customer's AWS bill into Acute, and `resource_service_map` is unseeded, so untagged AI spend stays invisible. This PRD specifies a small **customer-installed CLI** that: interactively creates a Legacy CUR (`configure`), reads it on a schedule and pushes daily aggregated batches to Acute (`push`), and surfaces + seeds attribution for untagged spend (`scan`/`seed`). It is deterministic, idempotent, testable, and runs with the customer's own AWS + Moolabs credentials. moo-acute is unchanged; AWS only.

---

## 2. WHAT — Requirements & Scope

### Problem Statement

**Untracked / unattributable AWS spend.** A customer's AWS bill carries AI/infra spend that (a) never reaches Acute (no ingestion path) and (b) even if imported, can't be attributed because resources are untagged and `resource_service_map` is empty. Result: per-feature/per-tenant margins are wrong, no reconciliation against the cloud invoice, no absorb-vs-fix surface. Affected: customer finance, customer engineering, and Moolabs (Acute starved of bill-grain input).

### CLI surface (the deliverable)

```
moo-cloud-bill init             # capture Moolabs API key (hidden prompt; key generated in Moolabs UI) → persist 0600
moo-cloud-bill configure        # discovery-first: detect/reuse existing CUR via SDK, else create; auto-fills column map
moo-cloud-bill push             # read CUR → aggregate daily → POST batches to Acute (cron this)
moo-cloud-bill scan             # write untagged-spend findings YAML (source of truth) for human review
moo-cloud-bill review           # interactive: walk findings, record decision, write same YAML (--seed to seed now)
moo-cloud-bill seed             # POST approved (decision=map) findings to resource_service_map
moo-cloud-bill detect           # (optional) confirm AWS usage / sanity-check config
  global flags: --dry-run, --config <path>, --profile <aws_profile>, --acute-base <url>
```

### Key User Stories

> Each sized for one focused session. No UI, no agent. The package is plain Python; the mapper/aggregation logic is pure and tested; the boto3/S3/HTTP edges are stubbed in tests.

#### US-001: `configure` — discovery-first Legacy CUR setup (reuse-or-create via boto3)
**Description:** As a customer operator, I want `moo-cloud-bill configure` to discover what already exists in my AWS account, reuse an existing CUR if there is one, and only prompt me for what it genuinely cannot find.
**Acceptance Criteria:**
- [ ] **Discovery (read-only, no confirmation):** `sts.get_caller_identity()` → account (shown to confirm, not asked); `cur.describe_report_definitions()` → list existing CURs; `s3.list_buckets()` for the create path.
- [ ] **Reuse path:** if a usable CUR exists (hourly + `RESOURCES`), offer to reuse it; read `S3Bucket`, `S3Prefix`, `ReportName`, `Format`, `TimeUnit`, `AdditionalSchemaElements` straight off the `ReportDefinition` — **no further AWS prompts**.
- [ ] **Create path (only if none usable):** present a bucket **pick-list** (not free-text); validate prefix (**non-empty, no leading/trailing `/`**); pure planner `build_report_definition(inputs)` (`TimeUnit=HOURLY`, Parquet, `CREATE_NEW_REPORT`, `[RESOURCES, SPLIT_COST_ALLOCATION_DATA]`, `RefreshClosedReports=true`); print the planned `ReportDefinition` + bucket policy; require explicit `yes`; `boto3.client("cur", region_name="us-east-1").put_report_definition(...)` under the operator's own creds. `--dry-run` previews without mutating.
- [ ] **Auto-fill column map (OQ-2):** read the delivered CUR **manifest** JSON from S3 → write `cur-column-map.yaml` with the *actual* physical column names. Falls back to documented defaults if no manifest yet (pre-first-delivery).
- [ ] Bucket policy (create path) applied via `s3:PutBucketPolicy` after a **separate** confirmation, or emitted for manual apply — operator's choice.
- [ ] Single-account only; AWS Organizations → warn + configure single-account.
- [ ] Writes `moo-cloud-bill.toml` capturing bucket, prefix, report name, region, acute_base so `push` is non-interactive. **No secrets in this file.**
- [ ] Prompts the operator **only** for what the SDK cannot provide: reporting currency (default = CUR `line_item_currency_code`) and Acute base URL (defaulted). The Moolabs API key is handled by `init` (US-005), not here.
- [ ] Tests: pure planner (settings + prefix validation); discovery via **stubbed boto3** (reuse path reads a stub `describe_report_definitions`; create path asserts `put_report_definition` once in us-east-1; **no apply on `--dry-run`/without confirm**); manifest→column-map parsing. `ruff` + `pytest` green.

#### US-002: `cur_row_mapper` — CUR → `CloudCostRowInput` + daily aggregation (pure core)
**Description:** As a developer, I want a tested transform that turns CUR line items into Acute's row shape at the correct daily grain.
**Acceptance Criteria:**
- [ ] Maps Legacy CUR columns → `CloudCostRowInput`: `service_name`←`line_item_product_code` (fallback `product_servicecode`), `resource_id`←`line_item_resource_id`, `region`←`product_region`, `usage_type`←`line_item_usage_type`, `cost`←configurable (default `line_item_unblended_cost`), `currency`←`line_item_currency_code`, `tags`←all `resource_tags_*`. **The map is data in `cur-column-map.yaml`, auto-filled from the delivered CUR manifest by `configure` (OQ-2 resolved); operator-overridable.**
- [ ] **Daily aggregation (C1):** group by `(billing_day_start, billing_day_end, service_name, resource_id, region, usage_type)` and **sum `cost`** so each batch satisfies the unique index. A test asserts two same-grain hourly lines collapse to one summed daily row.
- [ ] `cost < 0` (credits) excluded + recorded to `credits.yaml` (Acute rejects negatives), never silently dropped. Tested.
- [ ] Pure functions, no I/O. Tests: AWS row, missing optionals, empty tags, negative cost, non-USD currency, the aggregation collapse, a paired negative assertion (Bedrock `service_name` exact, not substring). `ruff` + `pytest` green.

#### US-003: `push` — read CUR → aggregate → POST daily batches to Acute
**Description:** As a customer operator, I want `push` (cron-scheduled) to read my CUR and POST daily batches to Acute with my Bearer key.
**Acceptance Criteria:**
- [ ] Reads the delivered CUR Parquet from S3 (**boto3 `s3:GetObject` + pyarrow**, latest report version per the CUR manifest) → maps + **aggregates to daily grain** via `cur_row_mapper` → for each `(aws, day)` POSTs `ImportBatchRequest` (`billing_period_start/end` = the day's `[00:00Z, +1d 00:00Z)`) to `POST {acute_base}/api/v1/cloud-billing/import`.
- [ ] **Auth:** `Authorization: Bearer <key>`, key from env/config (`MOOLABS_API_KEY` or config); **never hardcoded/logged**; body MUST NOT contain `tenant_id`.
- [ ] `reporting_currency` from config (customer's reporting currency), not hardcoded USD (M2).
- [ ] `end > start` validated; `rows ≥ 1`; empty days skipped. Re-running a day is safe (supersedes).
- [ ] Non-2xx surfaces with `(day, status)`, never swallowed; transient 5xx retried with backoff. Per-day row ceiling chunked within the day (OQ-3).
- [ ] `--dry-run` prints the batches it would POST without sending. Exit code non-zero on any failed day (so cron alerts).
- [ ] Tests via **stub HTTP**: URL, `Authorization: Bearer` present, schema-valid body, **no `tenant_id`**, daily bounds, aggregation applied, negative-cost excluded, retry-on-5xx, dry-run sends nothing. `ruff` + `pytest` green.

#### US-004: `scan` / `review` / `seed` — untagged-spend findings → `resource_service_map`
**Description:** As finance/ops, I want every untagged-spend finding surfaced, a way to decide each (map/absorb/ignore), and to seed the approved mappings so Acute attributes them. Only the **untagged residual** needs human input — tagged resources attribute automatically in Acute via their CUR tags; `resource_service_map` is the fallback for what's untagged (info missing by definition).
**Acceptance Criteria:**
- [ ] `scan` reads a delivered CUR sample → writes `untagged-findings.yaml` (the persistent, git-reviewable **source of truth**) with evidence + default decision fields (schema below). Flags untagged `AmazonBedrock` + known non-propagating services (S3→CloudFront, ECS→EBS).
- [ ] **No monetary threshold** — every finding surfaces (test asserts a $0.50 finding isn't dropped).
- [ ] `review` is an **interactive front-end** that walks findings one at a time, asks the decision (`[m]ap/[a]bsorb/[i]gnore`) + `service_name` (+ optional team/env), and **writes the same `untagged-findings.yaml`** — so the mapping persists, is reviewable, and is **reused next period** (you don't re-decide the same resources monthly). `review --seed` walks + seeds approved rows immediately.
- [ ] `seed` reads the file and POSTs **only `approved: true, decision: map`** rows as `resource-map` upserts (`cloud_provider=aws`, `resource_id`, `service_name`, optional `resource_type`/`team_name`/`environment`/`tags`), `Authorization: Bearer`, **no `tenant_id`** (server-derived).
- [ ] Output validates against `assets/untagged-findings.schema.yaml`. `review` (injected input) + `seed` (stub HTTP) tested. `ruff` + `pytest` green.

`untagged-findings.yaml` shape — `scan` emits evidence + default decision; human edits decision (directly or via `review`); `seed` consumes:
```yaml
generated_at: 2026-06-14T00:00:00Z
billing_period: 2026-05
findings:
  - resource_id: arn:aws:bedrock:us-east-1:123:provisioned-model/abc   # join key → resource_service_map.resource_id
    service: AmazonBedrock
    monthly_cost_estimate_usd: 8920.10        # evidence (scan)
    untagged_share_pct: 100
    primary_pattern: "Bedrock invocations with no feature/tenant tag"
    severity: critical
    suggested_service_mapping: ai-chat        # scan's guess
    decision: map                             # HUMAN: map | absorb | ignore
    approved: false                           # seed skips anything not true
    service_name: ai-chat                     # → resource_service_map.service_name (required when decision=map)
    team_name: null                           # optional
    environment: prod                         # optional
    resource_type: null                       # optional
    tags: {}                                  # optional
```

#### US-005: Packaging, credentials (`init`), config, docs
**Description:** As a customer, I want to install the tool, capture my Moolabs API key once, configure it, and schedule it with a clear README.
**Acceptance Criteria:**
- [ ] `pyproject.toml` with console entry point `moo-cloud-bill` (e.g. `click`/`argparse`); `pip install .` / `pipx install` works; pinned `boto3`, `pyarrow`, HTTP client, `pyyaml`.
- [ ] `moo-cloud-bill init` (or a `setup.sh`) captures the **Moolabs API key generated in the Moolabs UI** via a **hidden prompt** (`getpass` / `read -rs`) and persists it `chmod 600` to `~/.config/moo-cloud-bill/credentials` (or AWS Secrets Manager/SSM for prod). The key is **never** a CLI arg, **never** in `moo-cloud-bill.toml`, **never** logged (masked to `mlk_…****`).
- [ ] **Cron caveat handled:** because `push` runs non-interactively (no TTY), the key is persisted at `init` time; `push` resolves it by precedence **env `MOOLABS_API_KEY` > `~/.config/moo-cloud-bill/credentials` (0600) > Secrets Manager/SSM** — never prompts at run time.
- [ ] Non-secret config precedence: CLI flags > env (`ACUTE_BASE`, `AWS_PROFILE`) > `moo-cloud-bill.toml`.
- [ ] README: install, **link to Moolabs UI → API Keys**, `init` + `configure` walkthrough, the verified minimum read IAM policy, the 24–48h CUR floor, a cron example (`set -a; . ~/.config/moo-cloud-bill/credentials; set +a; moo-cloud-bill push`), and a `scan`/`review`→`seed` walkthrough.
- [ ] `--help` for every subcommand. `ruff` + `pytest` green; CI runs the full suite with boto3 + HTTP stubbed (no AWS/Acute needed).

### Testing Plan

**Automated** (offline; committed CUR fixtures; stub boto3 + HTTP):

| Test | Setup | Steps | Expected |
|------|-------|-------|----------|
| CUR planner | inputs | `build_report_definition` | all settings; prefix rejects trailing `/` |
| CUR reuse (stub boto3) | stub `describe_report_definitions` returns a usable CUR | `configure` | reuses it; reads bucket/prefix/columns; **no `put_report_definition` call** |
| CUR apply (stub boto3) | no existing CUR; confirmed inputs | `configure` w/ stub | `put_report_definition` once in us-east-1; **no apply on dry-run/no-confirm** |
| Manifest → column map | stub manifest JSON | `configure` | `cur-column-map.yaml` filled with actual column names |
| Key capture (`init`) | hidden-prompt stub | `init` | key written `chmod 600`; **never in toml; masked in output** |
| Review (interactive) | findings fixture + injected input | `review` | decisions written to same YAML; `--seed` posts only approved map rows |
| Mapper — AWS row | one CUR line | map | exact row; tags carried; Bedrock service exact (paired negative) |
| **Daily aggregation (C1)** | two same-grain hourly lines | map+aggregate | one summed daily row |
| Mapper — negative cost | credit line | map | excluded + recorded |
| Push — body + auth | small daily fixture | `push` w/ stub | URL; **Bearer**; schema-valid; **no `tenant_id`**; daily bounds |
| Push — re-run safety | same day twice | run twice | both POST; Acute supersedes; no error |
| Push — 5xx retry / dry-run | stub 503→201 / dry-run | run | retried+succeeds / sends nothing |
| Scan — no threshold | $0.50 untagged fixture | `scan` | finding present |
| Seed — upsert | approved findings | `seed` w/ stub | correct upsert; Bearer; no `tenant_id` |
| Packaging | clean venv | `pip install .` | `moo-cloud-bill --help` works |

**Manual CUJ** (real AWS + Acute — pilot):

| Test | Steps | Expected |
|------|-------|----------|
| CUR end-to-end | `configure`; wait ~24h | CUR Parquet at `s3://…/cost/hourly/<report>/`, hourly |
| Push | `push` vs staging Acute | `201`; `GET /imports` shows daily batches; aggregated rows match |
| Re-import | `push` same day again | old day `superseded`, new `active` |
| Attribution | `scan`→approve→`seed`, then check allocation | untagged spend attributed (confirm Acute re-attributes — OQ-4) |

### Functional Requirements

- **FR-1 (discovery-first configure):** `configure` discovers existing state via read-only SDK calls (`sts`, `cur.describe_report_definitions`, `s3.list_buckets`) and **reuses an existing CUR** when present; only the create path mutates, and only after explicit confirmation with the operator's own creds (`--dry-run` never mutates; read IAM role never auto-created). It reads the CUR manifest to auto-fill `cur-column-map.yaml`, and prompts only for what the SDK cannot provide (reporting currency, Acute base).
- **FR-2 (Legacy CUR settings):** Hourly · Parquet · `CREATE_NEW_REPORT` · `RESOURCES`+`SPLIT_COST_ALLOCATION_DATA` · `RefreshClosedReports=true` · S3 prefix not ending in `/` · CUR API in us-east-1.
- **FR-3 (mapper + aggregation, C1):** map every required field; **aggregate-and-sum to `(day, service, resource, region, usage_type)`** to satisfy the unique index; `cost<0` excluded+recorded; pure + tested.
- **FR-4 (push contract):** POST `ImportBatchRequest` to `/api/v1/cloud-billing/import`; **`Authorization: Bearer`**; tenant server-derived; **`tenant_id` never in body**; daily periods; `end>start`; `rows≥1`.
- **FR-5 (idempotency):** rely on Acute per-period supersession; re-POST the day; no client dedup; failures surface with `(day,status)` + non-zero exit; 5xx retried.
- **FR-6 (credential capture + safety):** the Moolabs Bearer key is generated in the Moolabs UI and captured once via a **hidden prompt** at `init`, persisted `chmod 600` (or Secrets Manager/SSM); resolved at run time by precedence **env > cred file > secrets manager** so cron `push` is non-interactive. Never a CLI arg, never in `moo-cloud-bill.toml`, never logged (masked). AWS creds from the operator's profile/role.
- **FR-7 (reporting currency):** from config, not hardcoded USD.
- **FR-8 (untagged surfacing + decision):** every finding surfaces (no monetary threshold) to `untagged-findings.yaml`; the absorb-vs-fix decision is captured as `decision`/`approved` fields, editable directly or via interactive `review`; `seed` sends only `approved: true, decision: map` rows.
- **FR-9 (deterministic, no LLM):** no agent/LLM anywhere in `configure`/`push`/`scan`/`seed`. Pure, reproducible.
- **FR-10 (packaging):** pip-installable, console entry point, pinned deps, `--help`, README; CI green with stubbed boto3+HTTP.
- **FR-11 (no Acute changes):** no moo-acute edits; contract gaps → Open Question.

### Non-Goals (Out of Scope)

- **An agent skill / SKILL.md / suite install** — this is a CLI package; no runtime agent. (Optionally a 10-line pointer skill — OQ-10.)
- **GCP and Azure** — separate tools later; AWS only now.
- **Any moo-acute code change.**
- **CUR 2.0 / BCM Data Exports** — Legacy CUR per the Datadog reference (OQ-5 tradeoff: loses CUR-2.0 Bedrock IAM-principal data).
- **Parsing/attributing CUR values** — Acute does attribution after import.
- **Implementing Acute's `/sync` pull** — client-side push only.
- **Client-side FX conversion** — Acute locks FX.
- **AWS Organizations / multi-account** — single-account v1.
- **Auto-reconciling an existing FinOps tag schema** — mapping report only.

---

## 3. HOW — Design Decisions

### Decision 1: CLI package, not an agent skill (chosen)
| | A: Agent skill (SKILL.md) | **B: CLI package (chosen)** | C: Both |
|--|--|--|--|
| Runtime determinism | LLM in loop — nondeterministic | pure, reproducible | mixed |
| Per-run cost/latency | tokens + latency every run | none | partial |
| Fit for scheduled billing push | poor | ideal | overkill |
| Customer self-serve | needs an agent host | `pip install` + cron | most work |
**Rationale:** the runtime is a deterministic, idempotent, scheduled data push — exactly what a script is for; an LLM would add nondeterminism, latency, and cost to financial-data ingestion for no benefit. The lone judgment step (untagged absorb-vs-fix) is a one-time human YAML review, not a runtime agent task. User confirmed customer self-serve.

### Decision 2: Setup is discovery-first (reuse-or-create) via boto3 (chosen)
`configure` first *discovers* state with read-only SDK calls and **reuses an existing CUR** if one is present (common — e.g. customers already running Datadog CCM), reading bucket/prefix/columns off the `ReportDefinition` + the delivered manifest. Only when none exists does it create one — pure planner → print plan + bucket policy → explicit confirm → `put_report_definition` (us-east-1), operator's own creds, `--dry-run` to preview. **Rationale:** most setup inputs already live in the account; asking the operator to retype them is error-prone and redundant. Discovery is read-only (no confirmation); only the create path mutates. The manifest read resolves the column-name unknown (OQ-2); reuse avoids creating duplicate CUR exports. Pure planner split from apply for testability.

### Decision 3: Period = Daily; aggregate to the unique-index grain (chosen, C1+C2)
One batch per `(aws, day)`; hourly CUR lines summed to `(day, service, resource, region, usage_type)`. Required — Acute's plain `db.add()` under `uq_cloud_cost_imports_active_row` aborts the batch on duplicate grain. Daily balances fidelity vs POST volume (~30/mo).

### Decision 4: Idempotency via Acute supersession (chosen)
Re-POST the day; Acute supersedes atomically. No client content-hash dedup (would duplicate server logic, risk drift).

### Decision 5: Negative cost → skip + record (chosen)
Acute's `cost` is `Field(ge=0)` → negatives 422. Credits excluded from POST, written to `credits.yaml`. Revisit if Acute adds credit support (OQ-6).

### Decision 6: Legacy CUR per the Datadog reference (chosen, I2)
Datadog CCM uses Legacy CUR (not CUR 2.0). Settings/columns/IAM verified from that doc. Tradeoff: no CUR-2.0 `INCLUDE_IAM_PRINCIPAL_DATA` for Bedrock IAM-principal attribution (OQ-5).

### Decision 7: AWS only; GCP/Azure as separate tools (chosen)
Prove the configure+push+aggregate+seed path for AWS first; other clouds reuse the mapper/pusher pattern in their own tools.

---

## 4. Architecture

```mermaidjs
graph TD
    Op[Customer operator] -->|moo-cloud-bill configure| Wiz[configure: interactive boto3 wizard]
    Wiz -->|put_report_definition us-east-1, operator creds, on confirm| AWS[(Customer AWS)]
    AWS -->|Legacy CUR Parquet, hourly| S3[(S3 cost/hourly/REPORT/)]
    Cron[cron] -->|moo-cloud-bill push| Push[push: boto3 S3 read + pyarrow]
    S3 --> Push
    Push -->|aggregate→daily; POST /import  Authorization: Bearer| Acute[(moo-acute — UNCHANGED)]
    Op -->|moo-cloud-bill scan| Scan[scan]
    S3 --> Scan
    Scan --> Findings[untagged-findings.yaml]
    Findings -->|operator approves| Seed[moo-cloud-bill seed]
    Seed -->|POST /resource-map  Bearer| Acute
    Acute -->|AllocationEngine + resource_service_map → ClickHouse + budget alerts| Done[Attributed cost]
```

### Data Flow (AWS)

1. `init` captures the Moolabs API key (hidden prompt; key from the Moolabs UI) → persisted `chmod 600`. `configure` discovers existing CUR via SDK and **reuses** it, or creates one (us-east-1) on confirmation; reads the manifest to auto-fill the column map; writes non-secret local config.
2. After the 24–48h floor, hourly CUR Parquet lands in `s3://<bucket>/cost/hourly/<report>/`.
3. `push` (cron) reads the latest CUR via boto3+pyarrow, **aggregates hourly lines to daily grain (summing cost)**, and POSTs one `ImportBatchRequest` per `(aws, day)` to Acute `/import` with `Authorization: Bearer`. Re-runs supersede.
4. `scan` surfaces untagged spend → operator approves → `seed` POSTs `/resource-map`.
5. Acute attributes (AllocationEngine + resource_service_map), syncs ClickHouse, fires budget alerts. The CLI's job ends at the POSTs.

### Security Considerations

- [x] **Guarded apply:** `configure` mutates only after interactive confirmation, operator's own creds; `--dry-run` non-mutating; read IAM role never auto-created. Setup-time write perms (`cur:PutReportDefinition`, `s3:PutBucketPolicy`) are the operator's, used interactively, never stored.
- [x] **Credential safety:** Bearer key captured once via hidden prompt at `init`, persisted `chmod 600` (or Secrets Manager/SSM); resolved at run time by env > cred file > secrets manager (cron-safe, no prompt). Never a CLI arg, never in `moo-cloud-bill.toml`, never logged (masked); `tenant_id` never in body. AWS creds from the operator's profile/role.
- [x] **Least privilege:** documented read-only minimum for ongoing `push`; operator creates the read role.
- [x] **Input validation:** mapper/scan validate parsed CUR rows; outgoing bodies schema-validated; `end>start`; aggregation guarantees unique-grain rows.
- [x] **Compliance regimes:** HIPAA → redact PHI-bearing tags before they enter `tags`/findings; never echo raw tag values that may carry PII.
- [x] **Transport:** HTTPS to Acute; surface non-2xx; retry only idempotent 5xx; non-zero exit on failure for cron alerting.

---

## 5. Implementation Plan

| Phase | PR | Description | Deps |
|-------|----|-------------|------|
| 1 | Package scaffold + `init` | `pyproject.toml`, entry point, config loader, `init` (hidden key capture → 0600), `detect`; CI w/ stubbed boto3+HTTP | None |
| 2 | `configure` (discovery-first) | discover/reuse via `describe_report_definitions`; create path (pure planner + stubbed-apply); manifest→`cur-column-map.yaml`; IAM + bucket-policy docs | 1 |
| 3 | `cur_row_mapper` + aggregation | pure mapper + daily aggregation; consumes `cur-column-map.yaml` | 1 |
| 4 | `push` | boto3 S3 read + pyarrow + HTTP POST; daily batching, Bearer, retry, dry-run, currency, chunking | 3 |
| 5 | `scan` / `review` / `seed` | findings YAML + schema; interactive `review`; resource-map seeder (stub HTTP) | 3 |
| 6 | Docs + release | README (install, Moolabs UI key link, IAM, 24–48h floor, cron, scan/review→seed), `--help`, version, release | 1–5 |

> Run `/pr-review` per PR. Green build proves compilation; review proves correctness.

### Rollback Plan

**Two-way door.** A standalone package; reverting a PR removes code. `push` only reads the CUR + POSTs (idempotent by daily supersession) — nothing to un-apply on AWS; a bad day is fixed by re-running it. `configure` mutations are operator-confirmed and reversible (delete the report definition).

---

## 6. Operational Readiness

### Monitoring & Alerting

| Metric | Threshold | Channel |
|--------|-----------|---------|
| `pytest`/`ruff` green | 100% / 0 | repo CI |
| `push` exit code | non-zero on failed day | customer cron / alerting |
| `push` non-2xx rate | surfaced in logs | customer ops |

No Moolabs-side runtime telemetry — nothing new deployed in moo-acute.

### Cost Impact

Negligible for Moolabs. Customer-side: Legacy CUR → S3 Parquet storage (cents–low-$/mo typical). No LLM/token cost (deterministic CLI). Daily bill-grain import volume is low.

### Performance Impact

Mapper/scan sub-second on samples. `push` latency = CUR size × ~30 daily POSTs/month, bounded by daily batching + per-day chunking (OQ-3). Dominant wait is the unavoidable 24–48h CUR floor.

---

## 7. Success Metrics

- A pilot customer goes: `pip install` → `configure` → (24–48h) → cron `push` → daily `201` batches in Acute (`GET /imports` confirms; aggregated rows match) → `scan`→approve→`seed` → previously-untracked spend attributed — **with no moo-acute change, no LLM in the loop, and no unconfirmed cloud mutation.**
- Re-running a day supersedes cleanly.
- 100% of new code tested; CI green with stubbed boto3+HTTP; `pip install .` yields a working `moo-cloud-bill`.

---

## 8. Open Questions

| # | Question | Owner | Status | Resolution |
|---|----------|-------|--------|------------|
| OQ-1 | Acute import contract | — | **RESOLVED** | Push API; Bearer; daily period + aggregate to unique-index grain; per-period supersession. Pinned from moo-acute. |
| OQ-2 | Exact CUR Parquet physical column names (format-dependent) | — | **RESOLVED** | `configure` reads the delivered CUR **manifest** and auto-fills `cur-column-map.yaml` with the actual names; documented defaults pre-first-delivery; operator-overridable. |
| OQ-3 | Per-request row ceiling on `/import` (per-day chunking) | Suite owner (owns Acute) | Open | Check Acute body-size limits; set a safe chunk; document. |
| OQ-4 | Does Acute re-attribute existing batches when `resource_service_map` changes, or only future? | Suite owner | Open | Determines whether `seed` fixes already-imported spend or only new; may need re-`push` of affected days. |
| OQ-5 | Legacy CUR loses CUR-2.0 `INCLUDE_IAM_PRINCIPAL_DATA` (Bedrock IAM-principal). Accept? | Suite owner | Open | Legacy chosen now (Datadog ref). Revisit if Bedrock per-principal attribution needed. |
| OQ-6 | Does Acute want credits (`cost<0`) at all? Today it 422s | Suite owner | Open | Until supported, skip+record (Decision 5). |
| OQ-7 | `cost` column — unblended vs amortized/net | Finance | Open | Default unblended; configurable. |
| **OQ-9** | **CLI/package name + distribution** (`moo-cloud-bill`? where hosted — own repo, part of `moolabs-py`, pip/pipx?) | Suite owner | Open | Pick name + a repo; decide pip vs git-clone install for customers. |
| **OQ-10** | **Fate of the existing prose-only `cost-billing-cloud-bill` skill** | Suite owner | Open | Options: deprecate it for the AWS path, or replace its body with a 10-line pointer to this CLI. |

---

## 9. References

- **Acute contract (authoritative):** `moo-acute/app/api/v1/cloud_billing/router.py`, `app/middleware/auth.py`, `app/services/cloud_billing_service.py`, `app/models/cloud_cost_import.py`, `tests/test_cloud_billing_service.py`
- **AWS Legacy CUR (verified):** Datadog CCM AWS setup — https://docs.datadoghq.com/cloud_cost_management/setup/aws/?tab=cloudformation ; AWS CUR (`cur`) API / `AWS::CUR::ReportDefinition`
- Attribution vocabulary (untagged/"cell ③" spend, key ladder, FOCUS): `skills/cost-billing/shared/anchor-taxonomy.md:47-64` (background only — not a runtime dep of the CLI)

---

*Generated by `/prd` 2026-06-14; revised through several review passes. Reframed from an agent skill to a deterministic customer-installed CLI package (no LLM at runtime). Resolved: C1 (daily aggregation to unique-index grain), C2 (daily period), I1 (Authorization: Bearer), I2 (Legacy CUR + verified columns/IAM via Datadog ref), OQ-2 (column map auto-filled from CUR manifest). Setup is discovery-first (reuse existing CUR via SDK, else create). API key generated in the Moolabs UI, captured via hidden prompt at `init`, persisted 0600 (cron-safe). Untagged decision captured as `decision`/`approved` in `untagged-findings.yaml`, editable directly or via interactive `review`. Scope: AWS only. moo-acute unchanged.*
