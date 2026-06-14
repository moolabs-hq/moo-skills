"""Covers the real CUR read pipeline: S3 list → parquet read → aggregate → push,
using a fake S3 that serves actual Parquet bytes (no boto3, no network).
"""
import io

import pyarrow as pa
import pyarrow.parquet as pq

from moo_cloud_bill.commands.push import read_cur_rows, run_push
from moo_cloud_bill.config import Config
from moo_cloud_bill.cur_columns import DEFAULT_COLUMN_MAP

from ._fakes import RecordingAcuteClient

CM = DEFAULT_COLUMN_MAP


def _parquet_bytes(rows):
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3Objects:
    def __init__(self, key, data):
        self.key = key
        self.data = data

    def list_objects_v2(self, **kwargs):
        return {"Contents": [{"Key": self.key}, {"Key": "ignore/Manifest.json"}]}

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": _Body(self.data)}


def _config():
    return Config(bucket="b", prefix="cost/hourly", report_name="r", acute_base="https://x")


def _rows():
    return [
        {CM["service_name"]: "AmazonBedrock", CM["resource_id"]: "r1",
         CM["region"]: "us-east-1", CM["usage_type"]: "Invoke",
         CM["cost"]: "1.00", CM["currency"]: "USD",
         CM["usage_start"]: "2026-05-14T03:00:00Z"},
        {CM["service_name"]: "AmazonBedrock", CM["resource_id"]: "r1",
         CM["region"]: "us-east-1", CM["usage_type"]: "Invoke",
         CM["cost"]: "2.50", CM["currency"]: "USD",
         CM["usage_start"]: "2026-05-14T09:00:00Z"},
    ]


def test_read_cur_rows_from_parquet():
    s3 = FakeS3Objects("cost/hourly/r/data.parquet", _parquet_bytes(_rows()))
    rows = read_cur_rows(_config(), {"s3": s3})
    assert len(rows) == 2
    assert rows[0][CM["service_name"]] == "AmazonBedrock"


def test_run_push_end_to_end_aggregates_and_posts():
    s3 = FakeS3Objects("cost/hourly/r/data.parquet", _parquet_bytes(_rows()))
    client = RecordingAcuteClient()
    rc = run_push(_config(), "mlk_key", clients={"s3": s3}, column_map=CM, client=client)
    assert rc == 0
    assert len(client.import_calls) == 1            # one day
    assert len(client.import_calls[0].rows) == 1    # aggregated to one grain
    assert str(client.import_calls[0].rows[0].cost) == "3.50"
