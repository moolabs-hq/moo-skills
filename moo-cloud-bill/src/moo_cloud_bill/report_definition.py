"""Pure Legacy CUR planning logic (no AWS calls) — testable in isolation.

Settings verified against the Datadog CCM AWS reference: Hourly, Parquet,
CREATE_NEW_REPORT, RESOURCES + SPLIT_COST_ALLOCATION_DATA, RefreshClosedReports.
The CUR API is us-east-1 only.
"""
from __future__ import annotations

CUR_API_REGION = "us-east-1"
REQUIRED_SCHEMA_ELEMENTS = ["RESOURCES", "SPLIT_COST_ALLOCATION_DATA"]


def validate_s3_prefix(prefix: str) -> str:
    """AWS rejects an empty prefix, one starting with ``/``, or ending with ``/``.
    Mid-path ``/`` is allowed (e.g. ``cost/hourly``). Returns the prefix or raises.
    """
    if not prefix or not prefix.strip():
        raise ValueError("S3 prefix must not be empty")
    p = prefix.strip()
    if p.startswith("/"):
        raise ValueError("S3 prefix must not start with '/'")
    if p.endswith("/"):
        raise ValueError("S3 prefix must not end with '/'")
    return p


def build_report_definition(
    *,
    report_name: str,
    s3_bucket: str,
    s3_prefix: str,
    s3_region: str = CUR_API_REGION,
) -> dict:
    """Construct a Legacy CUR ``ReportDefinition`` (the arg to put_report_definition)."""
    if not report_name or not report_name.strip():
        raise ValueError("report_name must not be empty")
    if not s3_bucket or not s3_bucket.strip():
        raise ValueError("s3_bucket must not be empty")
    prefix = validate_s3_prefix(s3_prefix)
    return {
        "ReportName": report_name.strip(),
        "TimeUnit": "HOURLY",
        "Format": "Parquet",
        "Compression": "Parquet",
        "AdditionalSchemaElements": list(REQUIRED_SCHEMA_ELEMENTS),
        "S3Bucket": s3_bucket.strip(),
        "S3Prefix": prefix,
        "S3Region": s3_region,
        "AdditionalArtifacts": [],
        "RefreshClosedReports": True,
        "ReportVersioning": "CREATE_NEW_REPORT",
    }


def is_usable_cur(report_def: dict) -> bool:
    """A CUR is reusable if it's hourly and includes resource IDs."""
    elements = report_def.get("AdditionalSchemaElements") or []
    return report_def.get("TimeUnit") == "HOURLY" and "RESOURCES" in elements


def build_bucket_policy(bucket: str, *, account_id: str | None = None) -> dict:
    """Minimum S3 bucket policy allowing the AWS billing service to write the CUR.

    The operator applies this (via configure's separate confirmation) or pastes
    it manually. account_id, when known, tightens the SourceAccount condition.
    """
    condition: dict = {}
    if account_id:
        condition = {"StringEquals": {"aws:SourceAccount": account_id}}
    return {
        "Version": "2008-10-17",
        "Statement": [
            {
                "Sid": "EnableAWSDataExportsToWriteCUR",
                "Effect": "Allow",
                "Principal": {"Service": "billingreports.amazonaws.com"},
                "Action": ["s3:GetBucketAcl", "s3:GetBucketPolicy", "s3:PutObject"],
                "Resource": [
                    f"arn:aws:s3:::{bucket}",
                    f"arn:aws:s3:::{bucket}/*",
                ],
                **({"Condition": condition} if condition else {}),
            }
        ],
    }
