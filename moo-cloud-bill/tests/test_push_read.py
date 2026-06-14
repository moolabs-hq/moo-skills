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


def test_typed_parquet_columns_float64_and_datetime():
    """Real CUR Parquet has float64 cost + timestamp columns, so to_pylist()
    yields float and datetime (not str). Exercise that production seam."""
    from datetime import datetime, timezone
    from decimal import Decimal

    from moo_cloud_bill.mapper import build_daily_batches

    table = pa.table({
        CM["service_name"]: pa.array(["AmazonS3"]),
        CM["resource_id"]: pa.array(["r1"]),
        CM["region"]: pa.array(["us-east-1"]),
        CM["usage_type"]: pa.array(["DataTransfer"]),
        CM["cost"]: pa.array([0.00015], type=pa.float64()),
        CM["currency"]: pa.array(["USD"]),
        CM["usage_start"]: pa.array(
            [datetime(2026, 5, 14, 23, 30, tzinfo=timezone.utc)],
            type=pa.timestamp("ms", tz="UTC"),
        ),
    })
    buf = io.BytesIO()
    pq.write_table(table, buf)
    s3 = FakeS3Objects("cost/hourly/r/data.parquet", buf.getvalue())

    rows = read_cur_rows(_config(), {"s3": s3})
    assert isinstance(rows[0][CM["cost"]], float)          # pyarrow gave us a float
    assert isinstance(rows[0][CM["usage_start"]], datetime)  # ...and a datetime

    batches, _ = build_daily_batches(rows, CM)
    assert batches[0].rows[0].cost == Decimal("0.00015")    # Decimal preserved, no float drift
    assert batches[0].billing_period_start.isoformat() == "2026-05-14T00:00:00+00:00"


def test_run_push_end_to_end_aggregates_and_posts():
    s3 = FakeS3Objects("cost/hourly/r/data.parquet", _parquet_bytes(_rows()))
    client = RecordingAcuteClient()
    rc = run_push(_config(), "mlk_key", clients={"s3": s3}, column_map=CM, client=client)
    assert rc == 0
    assert len(client.import_calls) == 1            # one day
    assert len(client.import_calls[0].rows) == 1    # aggregated to one grain
    assert str(client.import_calls[0].rows[0].cost) == "3.50"
