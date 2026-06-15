"""Thin AWS SDK wrappers. boto3 is imported lazily so the rest of the package
(and most tests) load without it. Every wrapper takes the boto3 client as a
parameter, so tests inject fakes and never touch real AWS.
"""
from __future__ import annotations

import io
import json

from .errors import MooCloudBillError

# botocore exception class names that mean "credentials/connection problem, not a
# bug" — matched by name so we don't hard-import botocore here.
_AUTH_EXC_NAMES = {
    "NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError",
    "TokenRetrievalError", "SSOTokenLoadError", "UnauthorizedSSOTokenError",
    "ProfileNotFound", "EndpointConnectionError", "ConnectTimeoutError",
    "ReadTimeoutError", "SSOError",
}
_AUTH_ERR_CODES = {
    "AccessDenied", "UnauthorizedOperation", "ExpiredToken",
    "ExpiredTokenException", "InvalidClientTokenId", "AuthFailure",
    "RequestExpired", "InvalidAccessKeyId", "SignatureDoesNotMatch",
}


def as_friendly_error(exc: Exception) -> MooCloudBillError | None:
    """Map a common AWS auth/connection failure to a clean, actionable error.
    Returns None for anything genuinely unexpected (let it surface as a traceback)."""
    if type(exc).__name__ in _AUTH_EXC_NAMES:
        return MooCloudBillError(
            f"AWS credentials/connection problem ({type(exc).__name__}): {exc}\n"
            "  Fix: run `aws sso login` (or `aws configure` / set AWS_PROFILE), then retry."
        )
    resp = getattr(exc, "response", None)
    code = resp.get("Error", {}).get("Code") if isinstance(resp, dict) else None
    if code in _AUTH_ERR_CODES:
        return MooCloudBillError(
            f"AWS authorization problem ({code}): {exc}\n"
            "  Fix: refresh credentials (`aws sso login`) or check IAM permissions, then retry."
        )
    return None


def make_clients(*, profile: str | None = None, region: str = "us-east-1") -> dict:
    """Build real boto3 clients (lazy import). CUR API is us-east-1 only."""
    import boto3

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return {
        "sts": session.client("sts", region_name=region),
        # CUR 2.0 / Data Exports — us-east-1 only.
        "exports": session.client("bcm-data-exports", region_name="us-east-1"),
        "s3": session.client("s3", region_name=region),
    }


def get_account_id(sts) -> str:
    return sts.get_caller_identity()["Account"]


def list_data_exports(exports) -> list[dict]:
    """All CUR 2.0 data export references (ExportArn/ExportName/...), paginated."""
    out: list[dict] = []
    token = None
    while True:
        resp = exports.list_exports(**({"NextToken": token} if token else {}))
        out.extend(resp.get("Exports", []))
        token = resp.get("NextToken")
        if not token:
            break
    return out


def get_export(exports, export_arn: str) -> dict:
    """Full Export definition for one ARN."""
    return exports.get_export(ExportArn=export_arn).get("Export", {})


def create_data_export(exports, export_def: dict) -> str:
    """Create a CUR 2.0 data export; returns the ExportArn."""
    return exports.create_export(Export=export_def).get("ExportArn", "")


def list_bucket_names(s3) -> list[str]:
    return [b["Name"] for b in s3.list_buckets().get("Buckets", [])]


def put_bucket_policy(s3, bucket: str, policy: dict) -> None:
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))


def create_bucket(s3, bucket: str, *, region: str = "us-east-1") -> None:
    """Create an S3 bucket for CUR delivery. us-east-1 must NOT send a
    LocationConstraint (AWS rejects it); every other region must."""
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})


def iter_cur_rows(s3, bucket: str, key: str):
    """Yield CUR 2.0 rows (dicts keyed by the CSV header) from one gzipped-CSV
    S3 object."""
    import csv
    import gzip

    obj = s3.get_object(Bucket=bucket, Key=key)
    text = gzip.decompress(_read_body(obj["Body"])).decode("utf-8")
    yield from csv.DictReader(io.StringIO(text))


def list_data_object_keys(s3, bucket: str, prefix: str, report_name: str) -> list[str]:
    """All current CUR 2.0 data files (.csv.gz) under the export prefix. CUR 2.0
    uses OVERWRITE_REPORT — one current file set, no retained stale assemblies —
    so a recursive glob is correct (no manifest dedup needed)."""
    base = f"{prefix}/{report_name}/"
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": base}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith(".gz"):
                keys.append(obj["Key"])
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return keys


def _read_body(body) -> bytes:
    return body.read()
