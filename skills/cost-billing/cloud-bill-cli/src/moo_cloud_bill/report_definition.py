"""Pure CUR 2.0 (AWS Data Exports / bcm-data-exports) planning logic — no AWS calls.

We use CUR 2.0 via Data Exports, CSV + GZIP, with OVERWRITE_REPORT (one current
file set, no retained stale assemblies → no double-count, no manifest dedup
needed). The export's QueryStatement selects exactly the columns the mapper
needs, so the CSV header is deterministic. The bcm-data-exports / CUR API is
us-east-1 only.
"""
from __future__ import annotations

CUR_API_REGION = "us-east-1"
TABLE = "COST_AND_USAGE_REPORT"

# Columns the export selects, in order — these ARE the CSV header. CUR 2.0 uses
# `product_region_code` (legacy used `product_region`) and a single
# `resource_tags` map column (legacy had `resource_tags_user_*`).
EXPORT_COLUMNS = [
    "line_item_usage_start_date",
    "line_item_product_code",
    "line_item_resource_id",
    "product_region_code",
    "line_item_usage_type",
    "line_item_unblended_cost",
    "line_item_currency_code",
    "resource_tags",
]


def validate_s3_prefix(prefix: str) -> str:
    """AWS rejects an empty prefix, one starting with ``/``, or ending with ``/``.
    Mid-path ``/`` is allowed (e.g. ``cur2``). Returns the prefix or raises."""
    if not prefix or not prefix.strip():
        raise ValueError("S3 prefix must not be empty")
    p = prefix.strip()
    if p.startswith("/"):
        raise ValueError("S3 prefix must not start with '/'")
    if p.endswith("/"):
        raise ValueError("S3 prefix must not end with '/'")
    return p


def build_query_statement(columns=EXPORT_COLUMNS) -> str:
    return f"SELECT {', '.join(columns)} FROM {TABLE}"


def build_data_export(
    *,
    name: str,
    s3_bucket: str,
    s3_prefix: str,
    s3_region: str = CUR_API_REGION,
    columns=EXPORT_COLUMNS,
) -> dict:
    """Construct a CUR 2.0 ``Export`` (the arg to bcm-data-exports create_export)."""
    if not name or not name.strip():
        raise ValueError("export name must not be empty")
    if not s3_bucket or not s3_bucket.strip():
        raise ValueError("s3_bucket must not be empty")
    prefix = validate_s3_prefix(s3_prefix)
    return {
        "Name": name.strip(),
        "Description": "CUR 2.0 CSV export for moo-cloud-bill",
        "DataQuery": {
            "QueryStatement": build_query_statement(columns),
            "TableConfigurations": {TABLE: {"TIME_GRANULARITY": "HOURLY", "INCLUDE_RESOURCES": "TRUE"}},
        },
        "DestinationConfigurations": {
            "S3Destination": {
                "S3Bucket": s3_bucket.strip(),
                "S3Prefix": prefix,
                "S3Region": s3_region,
                "S3OutputConfigurations": {
                    "OutputType": "CUSTOM",
                    "Format": "TEXT_OR_CSV",
                    "Compression": "GZIP",
                    "Overwrite": "OVERWRITE_REPORT",
                },
            }
        },
        "RefreshCadence": {"Frequency": "SYNCHRONOUS"},
    }


def is_usable_export(export: dict) -> bool:
    """A Data Export is reusable if it's CUR 2.0, hourly, CSV, to S3."""
    tc = export.get("DataQuery", {}).get("TableConfigurations", {}).get(TABLE, {})
    out = (export.get("DestinationConfigurations", {})
           .get("S3Destination", {}).get("S3OutputConfigurations", {}))
    return tc.get("TIME_GRANULARITY") == "HOURLY" and out.get("Format") == "TEXT_OR_CSV"


def build_data_export_bucket_policy(bucket: str, *, account_id: str | None = None,
                                    region: str = "us-east-1") -> dict:
    """Bucket policy allowing AWS Data Exports (and legacy CUR) to write. Covers
    both the `billingreports` and `bcm-data-exports` service principals."""
    arns = [f"arn:aws:bcm-data-exports:{region}:{account_id or '*'}:export/*",
            f"arn:aws:cur:{region}:{account_id or '*'}:definition/*"]
    condition: dict = {"StringLike": {"aws:SourceArn": arns}}
    if account_id:
        condition["StringLike"]["aws:SourceAccount"] = account_id
    return {
        "Version": "2008-10-17",
        "Statement": [
            {
                "Sid": "EnableAWSDataExportsToWriteToS3AndCheckPolicy",
                "Effect": "Allow",
                "Principal": {"Service": ["billingreports.amazonaws.com", "bcm-data-exports.amazonaws.com"]},
                "Action": ["s3:PutObject", "s3:GetBucketPolicy"],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                "Condition": condition,
            }
        ],
    }
