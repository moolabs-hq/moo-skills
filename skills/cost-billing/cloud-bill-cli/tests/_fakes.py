"""Hermetic fakes — no boto3/httpx/network in tests."""
from __future__ import annotations

from moo_cloud_bill.acute_client import Result


class RecordingAcuteClient:
    """Stands in for AcuteClient at the command level."""

    def __init__(self, status: int = 201):
        self.status = status
        self.import_calls: list = []
        self.map_calls: list = []

    def import_batch(self, batch):
        self.import_calls.append(batch)
        return Result(self.status, {})

    def upsert_resource_map(self, body):
        self.map_calls.append(body)
        return Result(self.status, {})


class FakeSTS:
    def __init__(self, account="123456789012"):
        self.account = account

    def get_caller_identity(self):
        return {"Account": self.account}


class FakeExports:
    """CUR 2.0 / bcm-data-exports fake. `exports` is a list of full Export dicts."""

    def __init__(self, exports=None):
        self.exports = exports or []
        self.created: list = []

    def list_exports(self, **kwargs):
        return {"Exports": [{"ExportArn": f"arn:{i}", "ExportName": e.get("Name")}
                            for i, e in enumerate(self.exports)]}

    def get_export(self, ExportArn):  # noqa: N803
        # ARNs here are intentionally minimal (`arn:<index>`); the index is the
        # last colon-segment. Keep that format — real ARNs have more colons.
        return {"Export": self.exports[int(ExportArn.split(":")[-1])]}

    def create_export(self, Export):  # noqa: N803
        self.created.append(Export)
        return {"ExportArn": "arn:new"}


class FakeS3:
    def __init__(self, buckets=None):
        self.buckets = buckets or []
        self.policy_calls: list = []
        self.created_buckets: list = []

    def list_buckets(self):
        return {"Buckets": [{"Name": b} for b in self.buckets]}

    def put_bucket_policy(self, Bucket, Policy):  # noqa: N803
        self.policy_calls.append((Bucket, Policy))

    def create_bucket(self, **kwargs):
        self.created_buckets.append(kwargs.get("Bucket"))
        return {}


def clients(*, account="123456789012", exports=None, buckets=None):
    return {
        "sts": FakeSTS(account),
        "exports": FakeExports(exports),
        "s3": FakeS3(buckets=buckets),
    }
