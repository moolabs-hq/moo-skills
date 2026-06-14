# Adversarial PR Review — moo-cloud-bill (PR #17)
Date: 2026-06-14
Operator: Claude Opus 4.8

## PRs in scope
| PR  | Branch                  | Base | Head SHA  | Status      |
|-----|-------------------------|------|-----------|-------------|
| #17 | feat/cloud-bill-cur-cli | main | 10a694f   | in-progress |

## Cross-PR dependencies
None.

## Codebase profile (Phase 1.5)
- Language/stack: Python 3.12; stdlib + boto3 + pyarrow + httpx + pyyaml; CLI via argparse; packaging via hatchling (`src/` layout).
- Test runner: pytest (`pythonpath=["src"]`). Scoped: `python3 -m pytest tests/test_x.py`; full: `python3 -m pytest`; lint: `ruff check .`. 70 tests today.
- Migrations/DDL: none — consumes moo-acute's existing `/api/v1/cloud-billing/*` endpoints; **moo-acute unmodified**.
- Codegen: none.
- Config style: flat TOML (`moo-cloud-bill.toml`, no secrets) + env vars + a `chmod 600` credentials file.
- Auth surface: `Authorization: Bearer <key>` to Acute; key capture/persistence in `credentials.py`; tenant derived server-side.
- Deployment: customer-installed CLI; `push` runs under cron (non-interactive).
- Conventions (user CLAUDE.md): immutability, many small files, no hardcoded secrets, 80% test coverage, no print()-as-logging (here print is the CLI output sink, intentional).
- CI quirks: **no CI configured** (no `.github/workflows/`).

## Per-PR detail

### PR #17 — moo-cloud-bill: AWS CUR → Acute ingestion CLI

**Original intention:** greenfield package — no prior code. The binding "original" contracts are EXTERNAL: (a) moo-acute's import API (`POST /api/v1/cloud-billing/import`, `ImportBatchRequest`/`CloudCostRowInput`, Bearer auth, tenant server-derived, plain `db.add()` under the partial-unique index, per-period supersession, `cost>=0`); (b) AWS Legacy CUR semantics; (c) the PRD's locked decisions.

**New intention:** a deterministic CLI that (1) configures/reuses a Legacy CUR via boto3, (2) reads the CUR, aggregates hourly lines to a **daily** grain summing cost, and POSTs daily batches to Acute with Bearer, (3) seeds `resource_service_map` from untagged findings, (4) captures/persists the Moolabs key securely (0600, never logged/arg/toml; env>file resolution for cron).

#### Success criteria (Phase 1d, post-1f)
1. Every Acute POST (import + resource-map) carries `Authorization: Bearer <key>` and **no `tenant_id`** in the body.
2. CUR rows aggregate to the unique-index grain `(period, service, resource, region, usage_type)` with **summed cost** — no duplicate grain can be emitted within a batch; **money stays Decimal, never float**.
3. Daily period bounds are half-open UTC days; the same input re-run yields identical batches (Acute supersedes).
4. Negative-cost (credit) lines are **excluded** from POSTs and recorded — never sent (Acute 422s on `cost<0`).
5. The API key is never written to the toml config, never logged unmasked, never a CLI arg; persisted 0600; resolved **env > file** so cron `push` never prompts.
6. `configure` mutates AWS only after explicit confirmation; `--dry-run` performs zero mutation; the reuse path performs **no** `put_report_definition`.
7. The Legacy CUR planner emits exactly the required settings (HOURLY/Parquet/CREATE_NEW_REPORT/RESOURCES+SPLIT_COST_ALLOCATION_DATA/RefreshClosedReports) and **rejects** an empty / leading-`/` / trailing-`/` S3 prefix.
8. `seed` posts only `approved && decision==map` findings; `scan` applies **no monetary threshold**.

#### Codebase-specific challenges (Phase 1e, post-1f)
1. **Money precision via pyarrow:** CUR `unblended_cost` is a Parquet `double`. `pyarrow.to_pylist()` returns a Python `float`; the mapper does `Decimal(str(float))`. Does a float like `0.1+0.2`-style value serialize to a lossy/ugly Decimal string that mis-sums or fails Acute's Decimal parse? Failure mode: wrong billed cents.
2. **Timestamp type from Parquet:** `usage_start` is a Parquet `timestamp`, so `to_pylist()` returns a `datetime`, not a string. `parse_timestamp` does `str(value).replace("Z",...)` → "2026-05-14 03:00:00+00:00" (space sep). Does `datetime.fromisoformat` accept it? Naive datetimes? Epoch ints? Failure mode: `push` crashes or buckets rows into the wrong UTC day.
3. **Currency not in the aggregation grain:** `build_daily_batches` groups by grain but ignores currency. Two same-grain lines in different currencies sum into one cost with the FIRST currency. Failure mode: cross-currency mis-sum sent to Acute (which then locks the wrong FX).
4. **Column-map vs actual row keys mismatch:** if the manifest maps a field to a physical column that isn't a key in the Parquet row dict, `raw.get(col)` silently returns None → dropped region/usage_type → wrong grain. Failure mode: silent attribution loss.
5. **Whole-CUR in memory:** `read_cur_rows` loads every object's `to_pylist()` into one list. A multi-GB monthly CUR exhausts memory. Failure mode: OOM on a real large account (OQ-3 chunking is open — confirm it's documented, not silently broken).

#### Phase 1f self-review — round 1
- Intentions: 1 edit — made explicit that "original" here = the external Acute/CUR/PRD contracts (greenfield has no prior code), so contract-drift hunting targets those.
- Success criteria: 1 sharpened (C2 now explicitly demands "money stays Decimal, never float" — the precision axis, not just the grain).
- Challenges: 2 added (money precision, timestamp type) after recalling the read path goes through pyarrow which returns native Python types, not strings.

#### Phase 1f self-review — round 2
- Intentions: no edits.
- Success criteria: no edits.
- Challenges: 1 added (currency-not-in-grain) — found while re-reading the mapper's grain key during the round-2 pass. This is a substantive round-2 finding → flag extra Pass-1 scrutiny to the reviewer.
- Suspicions deferred to Phase 2: does `to_body()` `str(Decimal)` ever produce scientific notation (e.g. very small costs → "1E-8") that Acute's Decimal parse rejects?

#### Risk map by subsystem (Phase 1g)
- `mapper.py` (highest): float/Decimal precision (C1), timestamp parsing (C2), currency-blind aggregation (C3). Money correctness lives here.
- `aws.py` / `push.read_cur_rows`: pyarrow native-type assumptions (C2), whole-CUR memory (C5), column-key mismatch (C4).
- `acute_client.py`: Bearer header present + no tenant_id (criterion 1); retry only on 5xx; str(Decimal) body shape.
- `credentials.py`: 0600 perms, masking, env>file resolution, never-logged (criterion 5).
- `configure.py`: confirm-before-mutate, dry-run, reuse-no-put (criterion 6).
- `report_definition.py`: settings + prefix validation (criterion 7).
- `scan.py`/`seed.py`: no-threshold + only-seedable (criterion 8).

## Review rounds
(round 1 pending dispatch)

## Final summary
- PR #17: in-progress.
- Merge status: NOT MERGED.
