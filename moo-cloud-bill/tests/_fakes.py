"""Hermetic fakes — no boto3/httpx/network in tests."""
from __future__ import annotations

import json

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


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class FakeSTS:
    def __init__(self, account="123456789012"):
        self.account = account

    def get_caller_identity(self):
        return {"Account": self.account}


class FakeCUR:
    def __init__(self, report_definitions=None):
        self.report_definitions = report_definitions or []
        self.put_calls: list = []

    def describe_report_definitions(self):
        return {"ReportDefinitions": self.report_definitions}

    def put_report_definition(self, ReportDefinition):  # noqa: N803 (boto3 arg name)
        self.put_calls.append(ReportDefinition)
        return {}


class FakeS3:
    def __init__(self, buckets=None, manifest=None):
        self.buckets = buckets or []
        self.manifest = manifest
        self.policy_calls: list = []

    def list_buckets(self):
        return {"Buckets": [{"Name": b} for b in self.buckets]}

    def put_bucket_policy(self, Bucket, Policy):  # noqa: N803
        self.policy_calls.append((Bucket, Policy))

    def get_object(self, Bucket, Key):  # noqa: N803
        if self.manifest is None:
            raise KeyError("no manifest")
        return {"Body": _Body(json.dumps(self.manifest).encode())}


def clients(*, account="123456789012", report_definitions=None, buckets=None, manifest=None):
    return {
        "sts": FakeSTS(account),
        "cur": FakeCUR(report_definitions),
        "s3": FakeS3(buckets=buckets, manifest=manifest),
    }
