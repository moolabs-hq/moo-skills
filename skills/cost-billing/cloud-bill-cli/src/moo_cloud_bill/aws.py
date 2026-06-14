"""Thin AWS SDK wrappers. boto3/pyarrow are imported lazily so the rest of the
package (and most tests) load without them. Every wrapper takes the boto3 client
as a parameter, so tests inject fakes and never touch real AWS.
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
        "cur": session.client("cur", region_name="us-east-1"),
        "s3": session.client("s3", region_name=region),
    }


def get_account_id(sts) -> str:
    return sts.get_caller_identity()["Account"]


def describe_report_definitions(cur) -> list[dict]:
    """All CUR report definitions, following NextToken pagination."""
    defs: list[dict] = []
    token = None
    while True:
        resp = cur.describe_report_definitions(**({"NextToken": token} if token else {}))
        defs.extend(resp.get("ReportDefinitions", []))
        token = resp.get("NextToken")
        if not token:
            break
    return defs


def put_report_definition(cur, report_def: dict) -> dict:
    return cur.put_report_definition(ReportDefinition=report_def)


def list_bucket_names(s3) -> list[str]:
    return [b["Name"] for b in s3.list_buckets().get("Buckets", [])]


def put_bucket_policy(s3, bucket: str, policy: dict) -> None:
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))


def read_manifest(s3, bucket: str, prefix: str, report_name: str) -> dict:
    """Read the top-level Legacy CUR manifest JSON listing the report columns."""
    key = f"{prefix}/{report_name}/{report_name}-Manifest.json"
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(_read_body(obj["Body"]))


def iter_cur_rows(s3, bucket: str, key: str):
    """Yield CUR Parquet rows (dicts) from one S3 object. Lazy pyarrow import."""
    import pyarrow.parquet as pq

    obj = s3.get_object(Bucket=bucket, Key=key)
    table = pq.read_table(io.BytesIO(_read_body(obj["Body"])))
    yield from table.to_pylist()


def _read_body(body) -> bytes:
    return body.read()


def is_missing_manifest(exc: Exception) -> bool:
    """True iff exc means 'CUR manifest not delivered yet' (vs a real error like
    AccessDenied / throttle / connection failure, which must NOT be swallowed).
    """
    if isinstance(exc, (KeyError, FileNotFoundError)):
        return True
    resp = getattr(exc, "response", None)
    code = resp.get("Error", {}).get("Code") if isinstance(resp, dict) else None
    return code in ("NoSuchKey", "404", "NotFound")
