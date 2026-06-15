"""`configure` — discovery-first CUR 2.0 (AWS Data Exports) setup.

Discovers existing data exports via read-only SDK calls, REUSES a usable CUR 2.0
CSV export when present, else creates one (pure planner → print plan → explicit
confirm → bcm-data-exports:CreateExport, us-east-1). The export's columns are
fixed by the SQL, so the column map is deterministic (no manifest read). Only the
create path mutates.
"""
from __future__ import annotations

from pathlib import Path

from .. import aws
from ..config import DEFAULT_ACUTE_BASE, Config, acute_base_from_domain, save_config
from ..cur_columns import DEFAULT_COLUMN_MAP, save_column_map
from ..report_definition import (
    build_data_export,
    build_data_export_bucket_policy,
    is_usable_export,
    validate_s3_prefix,
)


def _dest(export: dict) -> dict:
    return export.get("DestinationConfigurations", {}).get("S3Destination", {})


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
    sts, exports = clients["sts"], clients["exports"]

    account_id = aws.get_account_id(sts)
    ui.say(f"AWS account: {account_id}")

    usable = []
    for ref in aws.list_data_exports(exports):
        full = aws.get_export(exports, ref.get("ExportArn", ""))
        if is_usable_export(full):
            usable.append(full)

    reused = None
    if usable:
        names = [f"{e.get('Name')} (s3://{_dest(e).get('S3Bucket')}/{_dest(e).get('S3Prefix')})" for e in usable]
        idx = 0 if len(usable) == 1 else ui.choose("Existing usable CUR 2.0 export(s):", names)
        if ui.confirm(f"Reuse export '{usable[idx].get('Name')}'?"):
            reused = usable[idx]

    if reused is not None:
        d = _dest(reused)
        bucket, prefix = d["S3Bucket"], d["S3Prefix"]
        report_name, region = reused["Name"], d.get("S3Region", "us-east-1")
        ui.say(f"Reusing export '{report_name}' → s3://{bucket}/{prefix}")
    else:
        bucket, prefix, report_name, region = _create_export(
            clients, ui, account_id=account_id, dry_run=dry_run, out=out
        )
        if bucket is None:  # dry-run preview or abort
            return None

    if dry_run:
        ui.say("[dry-run] would save the column map and config — no files written.")
        return None

    save_column_map(dict(DEFAULT_COLUMN_MAP), Path(column_map_path))

    currency = ui.ask("Reporting currency", default="USD") or "USD"
    # acute is reachable at acute.<domain> (its own ingress), NOT the BFF at
    # api.<domain> — the BFF doesn't proxy cloud-billing.
    base_domain = ui.ask("Moolabs base domain (e.g. dev.moolabs.com)", default="")
    acute_base = acute_base_from_domain(base_domain) if base_domain.strip() else DEFAULT_ACUTE_BASE
    ui.say(f"Acute endpoint: {acute_base}")

    cfg = Config(
        bucket=bucket, prefix=prefix, report_name=report_name, region=region,
        acute_base=acute_base, reporting_currency=currency, aws_profile=aws_profile,
    )
    save_config(cfg, config_dir=config_dir)
    out("Saved config. Run `init` for the API key, then schedule `push`.")
    return cfg


def _select_or_create_bucket(s3, ui, *, dry_run, out):
    """Pick an existing delivery bucket or create a new one. Returns the bucket
    name, or None if the operator aborts."""
    existing = aws.list_bucket_names(s3)
    options = existing + ["➕ create a new bucket"]
    idx = ui.choose("Delivery bucket:", options)
    if idx == len(existing):  # the "create new" sentinel
        return _create_new_bucket(s3, ui, dry_run=dry_run, out=out)
    return existing[idx]


def _create_new_bucket(s3, ui, *, dry_run, out):
    name = (ui.ask("New bucket name (globally unique, us-east-1)", default="") or "").strip()
    if not name:
        ui.say("No bucket name entered — aborting.")
        return None
    if dry_run:
        ui.say(f"[dry-run] would create S3 bucket '{name}' (us-east-1).")
        return name
    if not ui.confirm(f"Create S3 bucket '{name}' in us-east-1?"):
        ui.say("Aborted — bucket not created.")
        return None
    aws.create_bucket(s3, name, region="us-east-1")
    out(f"Created bucket '{name}'.")
    return name


def _create_export(clients, ui, *, account_id, dry_run, out):
    s3, exports = clients["s3"], clients["exports"]
    bucket = _select_or_create_bucket(s3, ui, dry_run=dry_run, out=out)
    if bucket is None:
        return None, None, None, None

    while True:
        prefix_in = ui.ask("S3 prefix (e.g. cur2)", default="cur2")
        try:
            prefix = validate_s3_prefix(prefix_in)
            break
        except ValueError as exc:
            ui.say(f"Invalid prefix: {exc}")

    name = ui.ask("CUR 2.0 export name", default="moolabs-cur2") or "moolabs-cur2"
    export_def = build_data_export(name=name, s3_bucket=bucket, s3_prefix=prefix)
    policy = build_data_export_bucket_policy(bucket, account_id=account_id)

    ui.say("Planned CUR 2.0 export (Data Exports, CSV/GZIP, OVERWRITE):")
    ui.say(str(export_def))
    ui.say("Required S3 bucket policy (lets AWS Data Exports write the CUR):")
    ui.say(str(policy))
    ui.say(
        "Creating this needs bcm-data-exports:CreateExport + s3:PutBucketPolicy, and "
        "the account must have 'IAM access to Billing' enabled (else 403)."
    )

    if dry_run:
        ui.say("[dry-run] no changes applied.")
        return None, None, None, None

    if not ui.confirm("Apply the bucket policy and create this CUR 2.0 export (us-east-1)?"):
        ui.say("Aborted — nothing created.")
        return None, None, None, None
    # Policy MUST land before CreateExport — AWS validates delivery access at
    # creation, so a bucket without the policy (esp. a brand-new one) fails.
    aws.put_bucket_policy(s3, bucket, policy)
    out("Applied bucket policy.")
    aws.create_data_export(exports, export_def)
    out(f"Created CUR 2.0 export '{name}'.")
    return bucket, prefix, name, "us-east-1"
