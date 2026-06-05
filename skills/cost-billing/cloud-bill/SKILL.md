---
name: cost-billing-cloud-bill
description: >-
  Walk Moolabs customers through wiring AWS / GCP / Azure cloud-bill exports into the Moolabs platform's cloud-bill ingestion path. Configures AWS CUR 2.0 (hourly, with INCLUDE_RESOURCES + INCLUDE_IAM_PRINCIPAL_DATA for Bedrock attribution), GCP BigQuery billing export, and Azure Cost Management Export (daily-only — Azure has no hourly, communicated up-front). Audits tag-propagation health per cloud (AWS 48h activation lag, services not propagating tags, Azure resource-group-only exclusion). Produces cell ③ findings — cost that's real but cannot be attributed to a feature/customer — for PM/finance review with absorb-vs-fix decisions. Communicates the unavoidable 24-48h floor before first usable data. Skill B in the suite; feeds the Moolabs platform's cloud-bill ingestion path. Triggers on "wire cloud bills", "set up AWS CUR", "GCP billing export", "Azure cost management export", "Skill B", "audit tag propagation", "find untagged AI spend".
license: MIT
metadata:
  author: Moolabs
  version: 0.1.0
  created: 2026-05-19
  last_reviewed: 2026-05-19
  review_interval_days: 60
  source: docs/grooming/2026-05-19-cost-billing-discovery-requirements.md §4.2
---

# /cost-billing-cloud-bill — Skill B: Cloud bill integration

You are an expert in cloud-billing data plumbing for AWS / GCP / Azure. You walk a Moolabs customer through wiring their cloud bills so the Moolabs attribution backend can ingest at Tier 5 (bill-grain). You audit tag propagation, surface cell ③ findings, and communicate the unavoidable 24–48h floor before first usable data.

## Trigger

```
/cost-billing-cloud-bill                                        # walk through all 3 clouds
/cost-billing-cloud-bill --cloud aws
/cost-billing-cloud-bill --cloud aws,azure
/cost-billing-cloud-bill --rescan         # re-run cell ③ scan after tags changed
```

Naturally:

```
Wire up our customer's AWS bill
Set up GCP BigQuery export for billing
Configure Azure Cost Management Export
Audit tag propagation on the customer's AWS account
Run Skill B for the pilot customer
```

## Operating principles (apply to EVERY decision in this skill)

See `cost-billing-shared/operating-principles.md`. Key cloud-bill manifestations:

1. **NEVER assume the customer's clouds.** Cross-reference code imports (`boto3`, `google-cloud-*`, `azure-*`) with EXPLICITLY confirmed cloud accounts. If finance's stage 1 said "AWS only" but the code imports `azure-storage-blob`, ASK before recommending an Azure export.
2. **When in doubt, ASK.** Untagged spend bucket size: ALWAYS ask "is this real spend, an artifact of incomplete tag propagation, or something else?" Never silently absorb into "cell ③ — unattributable".
3. **Region setup** — finance set the region(s) in Stage 1. If the cloud account's actual default region differs from finance's commitment, FLAG IT (don't reconcile silently).
4. **Compliance regimes (from finance)** drive export gotchas — HIPAA → no PHI in export tags → CRM tag must be redacted. Never proceed past a compliance regime requirement without explicit acknowledgement.
5. **24-48h floor** — communicate clearly; never optimistically "skip the wait" by using stale demo data.

## Read first (shared/)

- `anchor-taxonomy.md` — what "cell ③" means, attribution-key cascade, FOCUS half-open interval.
- `v1-decisions-log.md` — cell ③ always-surface (v1 #10), single-account v1 default (open #1).
- `gaps-tracker.md` §6.2 — Skill B-specific open questions.

## Workflow — 4 phases per detected cloud

### Phase 1: Detect customer's clouds (deterministic)

Run `scripts/cloud_detect.py <customer-repo>`. Cross-reference customer's code (imports of `boto3`, `google-cloud-*`, `azure-*`) with **actual cloud accounts** the customer provides (you ask explicitly — see Phase 2). Never recommend an export setup for a cloud the customer doesn't actually use.

```yaml
detected_in_code:
  - aws (boto3 import detected at 12 sites)
  - gcp (google-cloud-bigquery, google-cloud-storage)
confirmed_accounts:
  - aws: <pending — ask customer>
  - gcp: <pending — ask customer>
```

### Phase 2: Per-cloud setup — AWS

**Goal:** configure CUR 2.0 export, audit IAM, document the 24–48h floor.

**Required AWS settings (per requirements §4.2):**

```yaml
export_name: moolabs-acute-cur
delivery_format: Parquet                    # smaller, faster, preferred
time_granularity: HOURLY
include_resources: true                     # required for resource-level attribution
include_iam_principal_data: true            # required for Bedrock IAM-principal attribution
include_split_cost_allocation_data: false
report_versioning: OVERWRITE_REPORT
s3_bucket: <customer-provides>
s3_prefix: cur/v2/                          # convention
refresh_closed_reports: true
```

**Walk the customer through:**

1. Confirm AWS Organizations vs single-account. **v1 supports single-account only.** Multi-account → defer to v1.5; emit a note in `.moolabs/cloud-bill-config.yaml`.
2. Pick the S3 bucket for delivery (must exist; customer creates if not).
3. Permissions document: minimum IAM read scope — **emit but don't apply** (`references/aws-cur-iam-minimum.md`). Customer creates the role and pastes the ARN.
4. CUR 2.0 export creation — emit the AWS CLI command or CloudFormation template:
   ```bash
   aws bcm-data-exports create-export ...   # full command in references/
   ```
5. **Communicate the floor:** "First usable export delivery: 24h minimum (AWS schedules CUR ~24h after first export creation). Tag activation: up to 48h additional. Skill B parks here; Skill A (`/cost-billing-discovery`) runs in parallel while you wait."

**Tag-propagation audit (run after first export lands):**

`scripts/aws_tag_audit.py` flags:
- Untagged AI spend (`AmazonBedrock` lines with empty `resourceTags/*`).
- Services known not to propagate tags to child resources (e.g., S3 → CloudFront origin, ECS task → EBS volume).
- Activation lag tagged within 48h window.

### Phase 3: Per-cloud setup — GCP

**Required GCP settings:**

```yaml
export_destination: BigQuery
dataset: moolabs_acute_billing
table_prefix: gcp_billing_export
detailed_export: true                       # required for SKU-level breakdown
service_account_required_roles:
  - bigquery.dataViewer
  - bigquery.jobUser
```

**Walk through:**

1. Create the BigQuery dataset (customer's project).
2. Enable Cloud Billing detailed export to BigQuery.
3. Create a service account with the two required roles; emit the JSON keyfile or recommend Workload Identity Federation.
4. Floor: ~24h before first export populates.

**Tag-propagation audit:** GCP propagates labels less aggressively than AWS tags. `scripts/gcp_label_audit.py` flags resources without `tenant`, `product`, `feature`, `environment` labels.

### Phase 4: Per-cloud setup — Azure

**Required Azure settings:**

```yaml
export_type: Cost Management Export
target: Storage Account                     # blob container
schedule: Daily                             # Azure has no hourly option — COMMUNICATE
date_range: month_to_date
file_format: CSV
data_set: ActualCost                        # vs. AmortizedCost — discuss
```

**Critical communication:**

> **Azure does not offer hourly granularity** in Cost Management Export. The customer's per-day attribution will be interpolated, not measured. For customers needing financial-grade hourly attribution (e.g., per-end-user margin on Azure-hosted workloads), this is a known limitation. Document it in the customer's `.moolabs/cloud-bill-config.yaml` under `azure.granularity_caveat`.

**Walk through:**

1. Create a Storage Account + blob container (customer's subscription).
2. Set up the Cost Management Export with the settings above.
3. Permissions: `Cost Management Reader` + `Storage Blob Data Contributor` on the destination.
4. Floor: ~24h until first export.

**Tag-propagation audit:** `scripts/azure_tag_audit.py` flags:
- Resource-group-only tags — these are **NOT** included in Azure cost exports. If the customer's tag schema is resource-group-only, lift to resource-level tags or accept best-effort daily-grain attribution.
- Resources without `tenant_id`, `product`, `feature`, `environment` direct tags.

## Cell ③ findings

After exports land and tag audits complete, run `scripts/cell_3_scan.py`. Output `.moolabs/cloud-bill/cell-3-findings.yaml`:

```yaml
findings:
  - cloud: aws
    service: AmazonS3
    monthly_cost_estimate_usd: 1240.50
    untagged_share_pct: 78
    primary_pattern: "render-retries to S3 PUT, tag propagation broken from ECS"
    severity: high
    suggested_action: "Tag ECS tasks with feature; CDK update needed"
  - cloud: aws
    service: AmazonBedrock
    monthly_cost_estimate_usd: 8920.10
    untagged_share_pct: 100
    primary_pattern: "Bedrock invocations pre-2026-04-08 lack IAM-principal data"
    severity: critical
    suggested_action: "Toggle INCLUDE_IAM_PRINCIPAL_DATA=true (gated April 8 2026+); historical cost stays un-attributable"
  - cloud: azure
    service: Microsoft.MachineLearningServices
    monthly_cost_estimate_usd: 412.30
    untagged_share_pct: 100
    primary_pattern: "resource-group-only tagging"
    severity: medium
    suggested_action: "Lift tags to resource-level; until then, best-effort daily attribution"
```

**Always surface every finding — no monetary threshold v1** (per `v1-decisions-log.md` #10). PM/finance decides per row: **absorb** (write off as operations overhead), **fix** (engineer task), or **accept best-effort** (mark for FOCUS-export consumers downstream).

## Outputs

| File | Consumed by |
|---|---|
| `.moolabs/cloud-bill/aws-cur-config.yaml` | the Moolabs platform's cloud-bill ingestion connector config |
| `.moolabs/cloud-bill/gcp-bq-config.yaml` | the Moolabs platform's cloud-bill ingestion connector config |
| `.moolabs/cloud-bill/azure-cost-config.yaml` | the Moolabs platform's cloud-bill ingestion connector config |
| `.moolabs/cloud-bill/tag-propagation-report.md` | customer engineer + finance |
| `.moolabs/cloud-bill/cell-3-findings.yaml` | PM/finance review (in three-role surface) |
| `.moolabs/cloud-bill/wait-status.yaml` | tracks 24–48h floor for resume |

## Degraded modes

| Condition | Behavior |
|---|---|
| Customer refuses one cloud's setup | Continue with the others. Report incomplete coverage in `cloud-bill-config.yaml`. |
| Customer's Azure tags are resource-group-only | Cannot lift to financial-grade. Report best-effort daily-grain attribution; flag `azure.financial_grade=false`. |
| First-export empty (brand-new customer, no spend in window) | Emit "no findings; re-run after 30 days" + `cloud-bill-config.yaml` recorded. |
| Customer's AWS Organizations setup | v1 supports single-account; flag as v1.5 follow-up; configure single-account in the meantime. |
| Tag schema conflict (customer has FinOps-defined schema) | v1 emits a mapping report (no auto-rewrite); customer decides reconciliation strategy. |

## What this skill MUST NOT do

- **Never** create IAM roles / service accounts / RBAC assignments automatically — emit the commands; customer applies.
- **Never** recommend an export for a cloud the customer doesn't use (per requirements §4.2).
- **Never** silently hide cell ③ findings under a cost threshold (v1 #10).
- **Never** claim financial-grade attribution where Azure-daily / Bedrock-pre-Apr-2026 makes that impossible — communicate honestly.

## Skill R applies (per `gaps-tracker.md` §6.5 #27)

After the first-export scan completes, invoke `/cost-billing-adversarial-review --phase post-skill-b --target <cell-3-findings>`. This catches misclassified untagged spend before PM/finance review.

## Reference files

- `references/aws-cur-2-setup.md` — full CUR 2.0 export creation walk-through.
- `references/aws-cur-iam-minimum.md` — minimum IAM read scope.
- `references/gcp-billing-export.md` — BigQuery export setup.
- `references/azure-cost-management.md` — Azure Cost Management Export setup.
- `references/tag-propagation-rules.md` — per-cloud propagation gotchas.
- `references/cell-3-findings.md` — what cell ③ means; how to write a finding entry.
- `references/24-48h-floor.md` — how to communicate the wait.

## Scripts

- `scripts/cloud_detect.py` — Phase 1 cross-reference code imports vs. confirmed accounts.
- `scripts/aws_cur_configure.py` — emit CUR 2.0 setup commands.
- `scripts/gcp_billing_configure.py` — emit BQ export setup.
- `scripts/azure_cost_configure.py` — emit Azure export setup.
- `scripts/aws_tag_audit.py` / `scripts/gcp_label_audit.py` / `scripts/azure_tag_audit.py` — tag propagation audit per cloud.
- `scripts/cell_3_scan.py` — surface untagged-spend findings.

## Assets

No standalone assets ship today (the `assets/` directory is empty). The per-cloud connector-config shape and the AWS untaggable-services catalog live inline in this SKILL.md prose. Extracting them into `assets/connector-config.schema.yaml` / `assets/aws-untaggable-services.yaml` is on the roadmap once the connector contract stabilizes upstream.
