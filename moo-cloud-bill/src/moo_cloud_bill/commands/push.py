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
    failed_days: list = field(default_factory=list)
    dry_run: bool = False

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


def push_batches(batches, credits, client, *, dry_run=False, out=print) -> PushSummary:
    summary = PushSummary(skipped_credits=len(credits), dry_run=dry_run)
    for batch in batches:
        day = batch.billing_period_start.date().isoformat()
        if dry_run:
            out(f"[dry-run] would POST {day}: {len(batch.rows)} row(s)")
            continue
        result = client.import_batch(batch)
        if result.ok:
            summary.ok += 1
            out(f"{day}: {result.status_code} ({len(batch.rows)} row(s))")
        else:
            summary.failed += 1
            summary.failed_days.append((day, result.status_code))
            out(f"{day}: FAILED {result.status_code} — {result.body}")
    if credits:
        out(f"Skipped {len(credits)} credit line(s) (cost<0; Acute rejects negatives).")
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
