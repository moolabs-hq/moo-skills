# moo-cloud-bill

Customer CLI that configures an **AWS Legacy Cost-and-Usage Report (CUR)** and
pushes it to **Moolabs Acute** (`/api/v1/cloud-billing/import`) so untagged
cloud spend can be attributed to features/tenants. Deterministic — **no LLM at
runtime**. See `../../../tasks/prd-cloud-bill-cur-report.md` for the full design.

Bundled in the Cost+Billing suite; the engineering-persona `install.sh` asks
interactively whether to set up the CUR now — it installs this CLI and runs
`configure`. It is **not** an agent skill — it's a customer runtime tool.

## Install

```bash
pip install .          # or: pipx install .
```

## Quick start

```bash
# 1. Generate an API key in the Moolabs UI → API Keys, then:
moo-cloud-bill init                 # hidden prompt → persisted chmod 600

# 2. Set up (or reuse) the CUR. Discovery-first: reuses an existing CUR if found.
moo-cloud-bill configure            # --dry-run to preview without mutating

# 3. After AWS delivers the first CUR (~24–48h), push it on a schedule:
moo-cloud-bill push                 # --dry-run to preview the batches

# 4. Attribute untagged spend:
moo-cloud-bill scan                 # writes untagged-findings.yaml
moo-cloud-bill review               # interactive decisions (or edit the YAML)
moo-cloud-bill seed                 # POST approved (decision=map) mappings
```

## Credentials (cron-safe)

`init` stores the key `chmod 600` at `~/.config/moo-cloud-bill/credentials`.
Because `push` runs non-interactively under cron, the key is resolved by
precedence **env `MOOLABS_API_KEY` > credentials file > Secrets Manager/SSM** —
it never prompts at run time. The key is never a CLI arg, never written to
`moo-cloud-bill.toml`, never logged unmasked.

Cron example:

```cron
0 6 * * *  set -a; . ~/.config/moo-cloud-bill/credentials; set +a; moo-cloud-bill push
```

## Minimum read IAM (for ongoing `push`)

```
s3:ListBucket                 (the delivery bucket)
s3:GetObject                  (arn:aws:s3:::BUCKET/PREFIX/REPORT_NAME/*)
cur:DescribeReportDefinitions
ce:Get*
organizations:Describe*, organizations:List*
```

`configure`'s create path additionally needs `cur:PutReportDefinition` and
`s3:PutBucketPolicy` — used interactively with your own credentials, not stored.

## The 24–48h floor

AWS delivers the first CUR ~24h after creation; resource-tag activation can add
up to ~48h. There is no way to skip this; run `configure` early and schedule
`push` once data lands.

## Develop / test

```bash
pip install -e ".[dev]"
pytest          # hermetic: boto3 + HTTP are stubbed; no AWS/Acute needed
ruff check .
```

## Scope

AWS only. GCP/Azure ship as separate tools reusing this one's mapper/pusher
pattern and the Acute push contract.
