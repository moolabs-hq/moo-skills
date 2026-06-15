# moo-cloud-bill

Customer CLI that configures an **AWS CUR 2.0 export** (AWS Data Exports,
gzipped CSV) and pushes it to **Moolabs Acute** (`/api/v1/cloud-billing/import`)
so untagged cloud spend can be attributed to features/tenants. Deterministic —
**no LLM at runtime**. See `../../../tasks/prd-cloud-bill-cur-report.md` for the
full design.

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

# (optional) confirm Acute is reachable + your key works (and whether the CUR has data yet):
moo-cloud-bill verify

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

## Which AWS account / SSO role to use

Log in (`aws sso login --profile <name>`) to the **account whose bill you want to
ingest**. v1 is **single-account** — for an AWS Organizations consolidated bill you
would use the management/payer account, but multi-account is deferred.

**Account-level gotcha:** the account must have **"IAM access to Billing" enabled**
(Billing console → *Account* → *IAM access*). Without it, CUR API calls return 403
**even when the IAM policy is correct** — this is an account root toggle, separate
from IAM.

## Permissions — two distinct sets

**`configure` (one-time setup, your interactive SSO creds — not stored):**

```
sts:GetCallerIdentity
bcm-data-exports:CreateExport    (create the CUR 2.0 export; us-east-1 only)
bcm-data-exports:ListExports     (discover an existing export)
bcm-data-exports:GetExport       (inspect/reuse an existing export)
s3:ListAllMyBuckets              (bucket pick-list)
s3:CreateBucket                  (only if you create a new delivery bucket)
s3:PutBucketPolicy               (let AWS Data Exports write to the bucket)
```

**`push` (ongoing, the cron role — read only):**

```
s3:ListBucket                 (the delivery bucket)
s3:GetObject                  (arn:aws:s3:::BUCKET/PREFIX/REPORT_NAME/*)
```

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
