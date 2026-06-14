"""`configure` — discovery-first Legacy CUR setup.

Discovers existing state via read-only SDK calls, REUSES an existing usable CUR
when present, else creates one (pure planner → print plan → explicit confirm →
put_report_definition, us-east-1). Reads the delivered manifest to auto-fill the
column map (resolves PRD OQ-2). Only the create path mutates.
"""
from __future__ import annotations

from pathlib import Path

from .. import aws
from ..config import DEFAULT_ACUTE_BASE, Config, save_config
from ..cur_columns import DEFAULT_COLUMN_MAP, build_column_map_from_manifest, save_column_map
from ..report_definition import (
    build_bucket_policy,
    build_report_definition,
    is_usable_cur,
    validate_s3_prefix,
)


def run_configure(
    clients: dict,
    ui,
    *,
    config_dir: Path | None = None,
    column_map_path: Path,
    aws_profile: str | None = None,
    dry_run: bool = False,
    out=print,
) -> Config | None:
    sts, cur, s3 = clients["sts"], clients["cur"], clients["s3"]

    account_id = aws.get_account_id(sts)
    ui.say(f"AWS account: {account_id}")

    existing = aws.describe_report_definitions(cur)
    usable = [r for r in existing if is_usable_cur(r)]

    reused = None
    if usable:
        names = [f"{r.get('ReportName')} (s3://{r.get('S3Bucket')}/{r.get('S3Prefix')})" for r in usable]
        idx = 0 if len(usable) == 1 else ui.choose("Existing usable CUR(s):", names)
        if ui.confirm(f"Reuse CUR '{usable[idx].get('ReportName')}'?"):
            reused = usable[idx]

    if reused is not None:
        bucket = reused["S3Bucket"]
        prefix = reused["S3Prefix"]
        report_name = reused["ReportName"]
        region = reused.get("S3Region", "us-east-1")
        ui.say(f"Reusing CUR '{report_name}' → s3://{bucket}/{prefix}")
    else:
        bucket, prefix, report_name, region = _create_cur(
            clients, ui, account_id=account_id, dry_run=dry_run, out=out
        )
        if bucket is None:  # dry-run preview, nothing applied
            return None

    if dry_run:
        # Reuse path reaches here on dry-run (create path already returned None).
        # Writing the column map / config is a mutation; a preview must not.
        ui.say("[dry-run] would auto-fill the column map and save config — no files written.")
        return None

    column_map = _resolve_column_map(s3, bucket, prefix, report_name, column_map_path, out=out)
    save_column_map(column_map, Path(column_map_path))

    currency = ui.ask("Reporting currency", default="USD") or "USD"
    acute_base = ui.ask("Acute base URL", default=DEFAULT_ACUTE_BASE) or DEFAULT_ACUTE_BASE

    cfg = Config(
        bucket=bucket, prefix=prefix, report_name=report_name, region=region,
        acute_base=acute_base, reporting_currency=currency, aws_profile=aws_profile,
    )
    save_config(cfg, config_dir=config_dir)
    out("Saved config. Run `init` for the API key, then schedule `push`.")
    return cfg


def _create_cur(clients, ui, *, account_id, dry_run, out):
    s3, cur = clients["s3"], clients["cur"]
    buckets = aws.list_bucket_names(s3)
    if not buckets:
        ui.say("No S3 buckets found — create one for CUR delivery first.")
        return None, None, None, None
    bucket = buckets[0] if len(buckets) == 1 else buckets[ui.choose("Delivery bucket:", buckets)]

    while True:
        prefix_in = ui.ask("S3 prefix (e.g. cost/hourly)", default="cost/hourly")
        try:
            prefix = validate_s3_prefix(prefix_in)
            break
        except ValueError as exc:
            ui.say(f"Invalid prefix: {exc}")

    report_name = ui.ask("CUR report name", default="moolabs-cur") or "moolabs-cur"
    report_def = build_report_definition(report_name=report_name, s3_bucket=bucket, s3_prefix=prefix)
    policy = build_bucket_policy(bucket, account_id=account_id)

    ui.say("Planned ReportDefinition:")
    ui.say(str(report_def))
    ui.say("Required S3 bucket policy:")
    ui.say(str(policy))
    ui.say(
        "Creating this needs cur:PutReportDefinition + s3:PutBucketPolicy, and the "
        "account must have 'IAM access to Billing' enabled (else CUR calls 403)."
    )

    if dry_run:
        ui.say("[dry-run] no changes applied.")
        return None, None, None, None

    if not ui.confirm("Create this CUR via the AWS SDK (us-east-1)?"):
        ui.say("Aborted — nothing created.")
        return None, None, None, None
    aws.put_report_definition(cur, report_def)
    out(f"Created CUR '{report_name}'.")

    if ui.confirm("Apply the S3 bucket policy now (else apply it yourself)?"):
        aws.put_bucket_policy(s3, bucket, policy)
        out("Applied bucket policy.")

    return bucket, prefix, report_name, "us-east-1"


def _resolve_column_map(s3, bucket, prefix, report_name, column_map_path, *, out):
    try:
        manifest = aws.read_manifest(s3, bucket, prefix, report_name)
        out("Read CUR manifest → auto-filled column map.")
        return build_column_map_from_manifest(manifest)
    except Exception as exc:
        # Only "manifest not delivered yet" should fall back to defaults. A real
        # error (AccessDenied, wrong bucket) must surface, not be masked as
        # "pre-first-delivery" — which would silently default the column map.
        if not aws.is_missing_manifest(exc):
            raise
        out("No CUR manifest yet (pre-first-delivery) — using documented defaults.")
        return dict(DEFAULT_COLUMN_MAP)
