"""`push` — read the CUR, aggregate to daily batches, POST to Acute. Cron'd, so
non-interactive. Re-running a day is safe (Acute supersedes). Non-zero exit on
any failed day so cron alerts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import aws
from ..acute_client import AcuteClient
from ..mapper import build_daily_batches


@dataclass
class PushSummary:
    ok: int = 0
    failed: int = 0
    skipped_credits: int = 0
    skipped_conflicts: int = 0
    failed_days: list[tuple[str, int]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


def push_batches(batches, credits, client, *, dry_run=False, out=print) -> PushSummary:
    # `credits` carries two distinct kinds of excluded line; report them
    # separately so dropped spend (currency conflict) isn't hidden as a "credit".
    def _is_conflict(c):
        return str(c.get("reason", "")).startswith("currency_conflict")

    conflicts = [c for c in credits if _is_conflict(c)]
    negatives = [c for c in credits if not _is_conflict(c)]
    summary = PushSummary(
        skipped_credits=len(negatives), skipped_conflicts=len(conflicts), dry_run=dry_run
    )
    for batch in batches:
        day = batch.billing_period_start.date().isoformat()
        if dry_run:
            out(f"[dry-run] would POST {day}: {len(batch.rows)} row(s)")
            continue
        try:
            result = client.import_batch(batch)
        except Exception as exc:
            # Transport failure exhausted retries. Record this day failed and keep
            # going so one bad day doesn't skip the rest of the month (symmetry
            # with the 5xx path); non-zero exit still alerts cron.
            summary.failed += 1
            summary.failed_days.append((day, -1))
            out(f"{day}: FAILED transport — {exc}")
            continue
        if result.ok:
            summary.ok += 1
            out(f"{day}: {result.status_code} ({len(batch.rows)} row(s))")
        else:
            summary.failed += 1
            summary.failed_days.append((day, result.status_code))
            out(f"{day}: FAILED {result.status_code} — {result.body}")
    if negatives:
        out(f"Skipped {len(negatives)} credit line(s) (cost<0; Acute rejects negatives).")
    if conflicts:
        out(
            f"WARNING: dropped {len(conflicts)} line(s) on currency conflict — "
            f"spend may be under-reported. Reasons: "
            + "; ".join(c["reason"] for c in conflicts[:5])
        )
    return summary


def read_cur_rows(config, clients) -> list[dict]:
    """List CUR Parquet objects under the report prefix and read all rows.

    NOTE (OQ-3, memory bound): this materializes the whole CUR in memory
    (`to_pylist()` per object). Fine for typical exports; a multi-GB monthly CUR
    on a very large account needs streaming (`pq.ParquetFile(...).iter_batches()`)
    + per-day chunking — tracked as PRD OQ-3.
    """
    s3 = clients["s3"]
    keys = _list_cur_object_keys(s3, config.bucket, config.prefix, config.report_name)
    raw_rows: list[dict] = []
    for key in keys:
        raw_rows.extend(aws.iter_cur_rows(s3, config.bucket, key))
    return raw_rows


def run_push(config, api_key, *, clients=None, column_map, client=None, dry_run=False, out=print) -> int:
    """Read CUR objects from S3 → aggregate → push. ``clients``/``client`` injectable for tests."""
    clients = clients or aws.make_clients(profile=config.aws_profile, region=config.region)
    raw_rows = read_cur_rows(config, clients)

    batches, credits = build_daily_batches(
        raw_rows, column_map, reporting_currency=config.reporting_currency, cloud_provider="aws"
    )
    client = client or AcuteClient(config.acute_base, api_key)
    summary = push_batches(batches, credits, client, dry_run=dry_run, out=out)
    return summary.exit_code


def _list_cur_object_keys(s3, bucket, prefix, report_name) -> list[str]:
    """Parquet keys for the CURRENT CUR assembly only.

    CREATE_NEW_REPORT retains prior assembly folders in S3; a blind glob would read
    stale assemblies and DOUBLE-COUNT line-items (the mapper sums by grain with no
    row-identity dedup). The manifest's `reportKeys` lists exactly the current
    assembly's files — the authoritative dedup handle. Glob only as a pre-first-
    delivery fallback (single assembly, so no double-count).
    """
    try:
        manifest = aws.read_manifest(s3, bucket, prefix, report_name)
    except Exception as exc:
        # Only "manifest not delivered yet" may fall back to the glob. A real error
        # (AccessDenied, throttle, connection) must surface — silently globbing
        # would re-introduce the stale-assembly double-count with no alert.
        if not aws.is_missing_manifest(exc):
            raise
        manifest = None
    report_keys = [k for k in (manifest or {}).get("reportKeys", []) if str(k).endswith(".parquet")]
    if report_keys:
        return report_keys
    return _glob_parquet_keys(s3, bucket, prefix, report_name)


def _glob_parquet_keys(s3, bucket, prefix, report_name) -> list[str]:
    token = None
    keys: list[str] = []
    base = f"{prefix}/{report_name}/"
    while True:
        kwargs = {"Bucket": bucket, "Prefix": base}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith(".parquet"):
                keys.append(obj["Key"])
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return keys
