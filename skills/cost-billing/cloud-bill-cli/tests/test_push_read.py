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
        if kwargs.get("Delimiter"):
            return {"CommonPrefixes": []}  # no period folders → triggers the glob fallback
        return {"Contents": [{"Key": self.key}, {"Key": "ignore/Manifest.json"}]}

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key.endswith("-Manifest.json"):
            raise KeyError("no manifest yet")  # → glob fallback (these tests cover that path)
        return {"Body": _Body(self.data)}


class FakeS3Manifest:
    """Models the real CUR layout: a period folder, a period-level manifest, and
    parquet objects (incl. a stale assembly the manifest's reportKeys excludes)."""

    def __init__(self, manifest, objects, period="cost/hourly/r/20260601-20260701/"):
        self.manifest = manifest
        self.objects = objects
        self.period = period

    def get_object(self, Bucket, Key):  # noqa: N803
        import json
        if Key.endswith("-Manifest.json"):
            return {"Body": _Body(json.dumps(self.manifest).encode())}
        return {"Body": _Body(self.objects[Key])}

    def list_objects_v2(self, **kwargs):
        if kwargs.get("Delimiter"):
            return {"CommonPrefixes": [{"Prefix": self.period}]}
        return {"Contents": [{"Key": k} for k in self.objects]}


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


def test_manifest_reportkeys_dedups_stale_assemblies():
    # CREATE_NEW_REPORT retains old assemblies; reading both double-counts cost.
    # The manifest's reportKeys names only the current assembly.
    one_row = [{
        CM["service_name"]: "AmazonS3", CM["resource_id"]: "r1",
        CM["region"]: "us-east-1", CM["usage_type"]: "DataTransfer",
        CM["cost"]: "5.00", CM["currency"]: "USD",
        CM["usage_start"]: "2026-05-14T03:00:00Z",
    }]
    pq_bytes = _parquet_bytes(one_row)
    objects = {  # stale asm1 + current asm2, same row
        "cost/hourly/r/asm1/data.parquet": pq_bytes,
        "cost/hourly/r/asm2/data.parquet": pq_bytes,
    }
    manifest = {"reportKeys": ["cost/hourly/r/asm2/data.parquet"]}
    s3 = FakeS3Manifest(manifest, objects)

    rows = read_cur_rows(_config(), {"s3": s3})
    assert len(rows) == 1  # current assembly only, not both

    client = RecordingAcuteClient()
    run_push(_config(), "k", clients={"s3": s3}, column_map=CM, client=client)
    assert client.import_calls[0].rows[0].cost == __import__("decimal").Decimal("5.00")  # not 10.00


def test_access_denied_on_manifest_does_not_silently_glob():
    # A real S3 error must propagate, not fall back to the (double-counting) glob.
    import pytest

    class _AccessDenied(Exception):
        def __init__(self):
            self.response = {"Error": {"Code": "AccessDenied"}}

    class FakeS3Denied:
        def get_object(self, Bucket, Key):  # noqa: N803
            raise _AccessDenied()  # manifest read is denied

        def list_objects_v2(self, **kwargs):
            # A period folder exists, so we attempt the manifest read (which 403s).
            if kwargs.get("Delimiter"):
                return {"CommonPrefixes": [{"Prefix": "cost/hourly/r/20260601-20260701/"}]}
            return {"Contents": [{"Key": "cost/hourly/r/asm1/data.parquet"}]}

    with pytest.raises(_AccessDenied):
        read_cur_rows(_config(), {"s3": FakeS3Denied()})


def test_run_push_end_to_end_aggregates_and_posts():
    s3 = FakeS3Objects("cost/hourly/r/data.parquet", _parquet_bytes(_rows()))
    client = RecordingAcuteClient()
    rc = run_push(_config(), "mlk_key", clients={"s3": s3}, column_map=CM, client=client)
    assert rc == 0
    assert len(client.import_calls) == 1            # one day
    assert len(client.import_calls[0].rows) == 1    # aggregated to one grain
    assert str(client.import_calls[0].rows[0].cost) == "3.50"
