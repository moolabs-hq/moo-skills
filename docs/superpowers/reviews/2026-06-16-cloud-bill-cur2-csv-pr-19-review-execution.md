# Adversarial PR Review — cloud-bill CUR 2.0/CSV pivot (PR #19)
Date: 2026-06-16
Operator: Claude Opus 4.8 (Claude Code)

## PRs in scope
| PR  | Branch                   | Base | Head SHA  | Status      |
|-----|--------------------------|------|-----------|-------------|
| #19 | feat/cloud-bill-cur2-csv | main | a509e34   | in-progress |

## Cross-PR dependencies
None. Successor to #17 (merged) which shipped the Legacy-CUR/Parquet version.

## Codebase profile (Phase 1.5)
- Language/stack: Python 3.11, `src/` layout, stdlib-only runtime for the read path
  (csv+gzip), boto3 for AWS, httpx for Acute, pyyaml for the column map. Plain
  dataclasses (no pydantic at runtime).
- Test runner: `python3 -m pytest -q` (pythonpath=src, testpaths=tests). Scoped:
  `pytest -q tests/test_x.py`. 112 tests, hermetic (fakes; no boto3/network).
- Lint: `python3 -m ruff check .` (line-length 100).
- Installer: `skills/cost-billing/shared/install.sh` (bash, `set -euo pipefail`,
  per-platform `$0` fan-out, `bash -n` clean).
- Config style: TOML config + 0600 credentials file; API key resolved env>file.
- AWS surface: bcm-data-exports (us-east-1), S3. CUR 2.0 / Data Exports.
- Acute contract: Bearer auth, tenant server-derived, daily grain, per-period
  supersession. Verified against moo-acute router source (test_acute_contract.py).
- Documented rules consulted: user golang/python rules (immutability, error
  propagation, no silent swallow), self-review.md (config default semantics,
  condition consistency, test-assertion validity), patterns.md (atomic update,
  flag-eval-once).

## Per-PR detail

### PR #19 — pivot Legacy CUR/Parquet → CUR 2.0/CSV + cron automation + Acute contract test

Head SHA at start: a509e34. 22 files, +672/−504. 4 commits (edb2a0e Legacy fixes,
71fa41c pivot, 784c7ef cron, a509e34 contract test).

#### 1c. Intentions

**Original intention (pre-PR, = #17 merged state):** `configure` created/reused a
**Legacy CUR** (`cur:PutReportDefinition`, Parquet, CREATE_NEW_REPORT) and read its
**manifest** to dedup stale assemblies and auto-fill the column map; `push` read
Parquet via pyarrow. Contract: read path depends on a manifest; column names come
from the manifest; double-count avoided via manifest `reportKeys`.

**New intention:** `configure` creates/reuses a **CUR 2.0 export** (`bcm-data-exports
:CreateExport`, CSV+GZIP, **OVERWRITE_REPORT**) whose SQL fixes the columns; `push`
globs `.csv.gz` and reads gzipped CSV with the stdlib. Contract: **no manifest** —
OVERWRITE_REPORT means one current file set so a glob is correct; column map is
deterministic from the export SQL; `resource_tags` is one JSON-map column; a missing
cost cell is `""` (not `None`). `install.sh` additionally schedules a daily `push`
via cron. Acute request bodies are pinned by a contract test against the live schema.

#### 1d. Success criteria
1. `build_data_export` produces a valid CUR 2.0 Export: CSV+GZIP+CUSTOM+OVERWRITE_REPORT,
   HOURLY+INCLUDE_RESOURCES, SQL selects exactly the 8 mapper columns.
2. The CSV read path (`iter_cur_rows` + `list_data_object_keys`) reads gzipped CSV,
   globs only `.csv.gz`/`.gz` data files, and ignores non-data siblings
   (manifest/metadata JSON).
3. `parse_cost` treats both `None` and `""`/whitespace as zero (the Parquet→CSV null
   seam) and still raises loudly on a genuinely non-numeric cost.
4. `extract_tags` parses the CUR 2.0 `resource_tags` JSON-map column, returns `{}`
   (never crashes) on malformed/empty, and the legacy `resource_tags_*` fallback
   still works.
5. No empty-rows batch can be POSTed (server `min_length=1`); negatives dropped
   (server `ge=0`); no `tenant_id` in any body — all enforced by the mapper and
   pinned by test_acute_contract.py.
6. The cron entry is self-contained (absolute paths), carries no secret, is
   idempotent across re-runs, and is removed on uninstall.
7. No stale Legacy-CUR references remain in code/docs that would mislead a
   customer (IAM perms, format, API names).

#### 1e. Codebase-specific challenges
1. **Reuse of a non-conforming existing export:** `is_usable_export` only checks
   HOURLY + TEXT_OR_CSV. A customer's pre-existing CUR 2.0 export that is hourly+CSV
   but selects DIFFERENT columns (or lacks INCLUDE_RESOURCES) would be reused, then
   `push` reads a CSV whose header doesn't match DEFAULT_COLUMN_MAP. Does it fail
   loudly (good) or silently mis-map (bad)? Trace `_require_columns`.
2. **Non-data `.gz` under the prefix:** Data Exports writes data `.csv.gz` AND may
   write gzipped/Manifest/metadata files under the same prefix tree. `list_data_object
   _keys` filters `.gz`. If a non-CSV `.gz` (e.g. a gzipped manifest) matches, `iter_cur
   _rows` runs csv.DictReader on it → garbage rows or a loud ColumnMapError. Which?
3. **OVERWRITE_REPORT history accumulation:** the glob is `<prefix>/<report_name>/`
   recursive. Across months, prior billing-period partitions accumulate. Every `push`
   re-reads and re-POSTs the entire history. Idempotent (Acute supersedes per period)
   but read cost + POST volume grow unbounded. Is that intended/bounded/logged?
4. **resource_tags CSV serialization unknown:** the mapper assumes JSON `{"k":"v"}`.
   If Data Exports CSV serializes the map differently, tags silently empty (degraded
   attribution, no crash, no failing test). Is the failure mode safe (empty) vs
   corrupting?
5. **cron under SSO / minimal env:** the scheduled `push` runs unattended with the
   persisted `--profile`. SSO tokens expire (warned). Also: cron's minimal env — does
   `push` resolve config dir (`~/.config/moo-cloud-bill`) and credentials when `HOME`
   is set but PATH is minimal? Is the command absolute?

#### 1f. Self-review of 1c/1d/1e
- Round 1: sharpened criterion 2 to name the glob-vs-data-file distinction; added
  challenge 2 (non-data .gz) after re-reading list_data_object_keys; corrected
  original-intention to note manifest auto-fill of column map (now removed).
- Round 2: added criterion 7 (stale-reference sweep) — the pivot's biggest silent-
  drift risk is docs/IAM text contradicting the code; added challenge 5's env-resolution
  half after noticing cron builds an absolute command but relies on HOME for config.
  Suspicion deferred to Phase 2: is `is_usable_export` too weak (challenge 1) a real
  bug or accepted (loud-fail-at-push)?

#### 1g. Risk map by subsystem
- `report_definition.py`: export dict shape vs real bcm-data-exports API (extra/missing
  keys → CreateExport ValidationException); is_usable_export permissiveness (challenge 1).
- `aws.py iter_cur_rows / list_data_object_keys`: non-data .gz (challenge 2); whole-file
  in-memory decompress (OQ-3 memory).
- `mapper.py`: parse_cost None vs "" (criterion 3 — has tests); extract_tags JSON map
  (challenge 4); grain key currency exclusion (regression risk — unchanged but adjacent).
- `configure.py`: reuse path saves DEFAULT_COLUMN_MAP regardless of reused export's SQL
  (challenge 1).
- `install.sh schedule_push_cron`: quoting of profile/cli_dir; idempotency; SSO (challenge 5).
- docs/PRD/README: stale Legacy references (criterion 7).

## Review rounds

### Round 1 (head a509e34 → fixes on top)
External reviewer (code-reviewer agent), 2 passes. Confirmed findings (operator-adjusted):
- **F1 IMPORTANT** (challenge 1) — `is_usable_export` too permissive: a reused hourly+CSV
  export missing `product_region_code`/`resource_tags` → silent under-attribution.
  **FIXED** report_definition.py: now also requires INCLUDE_RESOURCES, GZIP,
  OVERWRITE_REPORT, and all EXPORT_COLUMNS in the SQL (superset OK). + 1 negative test.
- **F2 IMPORTANT** (challenge 3) — recursive glob re-reads all billing periods forever
  (idempotent via Acute supersession but O(months) growth); the "no stale assemblies →
  glob is correct" comment was wrong cross-period. **FIXED** aws.py: corrected the
  comment, added a FALLBACK-SAFE current+prior-month scope (only scopes when every key
  carries a recognizable `BILLING_PERIOD=YYYY-MM`; falls back to ALL keys on an unfamiliar
  layout or if scoping would empty the read — never zeroes the read pre-real-data
  validation). + 5 tests. Operator note: did NOT hardcode a blind partition path (the
  reviewer's literal fix would zero the read if the layout differs from the assumed
  `BILLING_PERIOD=YYYY-MM`, which is unverified until real delivery — the validation gap).
- **F3 MINOR** (SC6) — unquoted `$aws_profile` in the cron command string → word-splits on
  a profile name with spaces. **FIXED** install.sh: quoted.
- **F4 MINOR** (challenge 2) — `.gz` filter broader than the `.csv.gz` we configure the
  export to produce; a gzipped manifest/metadata sibling could reach csv.DictReader.
  **FIXED** aws.py: filter → `.csv.gz`. + exclusion test.
- **F5 MINOR** (SC2) — `TAGS_COLUMN` constant dead; `extract_tags` hardcoded the literal.
  **FIXED** mapper.py: imports and uses TAGS_COLUMN.

Findings rejected (Phase 3a):
- **N1 NIT** — claim that the fallback `push_bin`'s `env PYTHONPATH=\"$cli_dir/src\"`
  doesn't protect spaces. FALSE POSITIVE: the `\"` yields a LITERAL `"` in the crontab
  line, which `/bin/sh` honors at cron exec. (F3 is the real bug — it had NO quotes.)

CI status: see round-1 CI check below.
Low-only streak: 0 (round had 2 IMPORTANT confirmed).
Operator spot-check: read `mapper.py:_grain_key` + `build_daily_batches` setdefault path
myself to confirm SC5 (no empty batch) holds independent of the contract test — a bucket is
created only by a non-negative row, so an all-credit day yields 0 batches. Verified.
Verification: `python3 -m pytest -q` → 118 passed; `ruff check .` clean; `bash -n install.sh` clean.

### Round 2 (head c4e2bab → fixes on top)
Fresh reviewer verified all four round-1 fixes correct (substring col check sound; never-empty
glob guard holds; cron profile quoting correct under /bin/sh; no circular import). New findings:
- **MINOR** — `extract_tags`: valid-but-non-object JSON (`[1,2]`, `5`, `"s"`) → `d.items()`
  raises AttributeError, escaping the `(ValueError, TypeError)` guard → crashes build_daily_batches.
  **FIXED** mapper.py: added AttributeError to the guard (degrade to {}). +test.
- **NIT** — `_billing_period_of` regex unanchored (`MY_BILLING_PERIOD=` false-match; over-include
  only). **FIXED** aws.py: anchored `(?:^|/)`.
- **NIT** — bucket policy put `aws:SourceAccount` (no wildcard) under StringLike. **FIXED**
  report_definition.py: StringEquals (matches AWS confused-deputy doc).
- **NIT** — FakeExports ARN index parsing brittle. **ADDRESSED** _fakes.py: guarding comment.

CI status: no checks configured for this PR (verified via MCP get_check_runs: total_count=0).
Severity this round (operator-adjusted): CRIT=0 IMP=0 MIN=1 NIT=3 → **LOW-only**.
Low-only streak: 1 (round 1 had IMPORTANT → was 0; round 2 LOW-only → 1).
Operator spot-check: read `extract_tags` myself and reasoned the AttributeError path
(`json.loads("[1,2]").items()`), and re-read the `list_data_object_keys` `if scoped:` guard to
confirm the never-empty property (challenge 3). Both verified, now pinned by tests.
Verification: 119 passed; ruff clean.

### Round 3 (head 5df181a) — CONVERGED
Fresh reviewer verified all three round-2 fixes correct + regression-free and ran a final
completeness sweep. Confirmed findings: CRIT=0 IMP=0 MIN=2 NIT=0 → **LOW-only**.
Both MINORs were test-coverage gaps on the round-1/2 fixes (not behavioral bugs):
- **MINOR** — no test asserted the bucket-policy confused-deputy condition split. **FIXED**
  test_report_definition.py: `test_bucket_policy_confused_deputy_condition_split`.
- **MINOR** — no test for the decline-reuse → create-fresh configure branch. **FIXED**
  test_configure.py: `test_decline_reuse_falls_through_to_create`.
These are test-only additions (no behavior change → streak not reset). Verified: 121 passed.
CI status: no checks configured (verified). Low-only streak: 2 → **EXIT GATE MET**.
Operator spot-check: read `configure.py:49–66` myself — confirmed decline-reuse sets
`reused=None` → `_create_export`, so the tightened `is_usable_export` correctly degrades to
fresh creation (now pinned by the new test).

Final test-only commit closes the two round-3 coverage MINORs; no further reviewer round is
warranted (test-only diff over already-reviewed code; two consecutive LOW-only rounds achieved).

## Success criteria status (after round 2)
1 PASS · 2 PASS · 3 PASS · 4 PASS (hardened) · 5 PASS · 6 PASS (cron quoting fixed) · 7 PASS.
## Challenge status (after round 2)
1 handled (is_usable_export tightened) · 2 handled (.csv.gz filter) · 3 handled (fallback-safe
scope + corrected comment; residual: real-layout validation deferred to first delivery) ·
4 handled (JSON degrade to {}) · 5 handled (SSO warned; cron command absolute + quoted).

## Remaining risks (accepted, non-blocking)
- The CSV read path (incl. the new BILLING_PERIOD scope and `.csv.gz` filter) is validated
  against hermetic fakes only — NOT against a real CUR 2.0 delivery (export first-delivers
  ~24h after configure). The scope is fallback-safe (reads ALL keys on an unfamiliar layout),
  so a wrong assumption degrades to the prior behavior, never to an empty read. Flagged in the
  PR body; close-out gated on first real delivery.

## Final summary
- PR #19 — **ready-for-human**. 3 rounds. Confirmed: 2 IMPORTANT + 4 MINOR + 3 NIT fixed,
  1 NIT rejected (false positive). Fix commits: c4e2bab (round 1), 5df181a (round 2),
  + a final test-only coverage commit (round 3).
- Tests 112 → 121 passing; ruff clean; bash -n clean; CI: no checks configured (verified).
- All 7 success criteria PASS; all 5 challenges handled (challenge 3 residual: real-CUR-2.0
  layout validation deferred to first delivery — read path is fallback-safe).
- Merge status: NOT MERGED — awaiting explicit user permission.
