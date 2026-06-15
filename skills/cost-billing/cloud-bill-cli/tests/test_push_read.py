"""Covers the real CUR 2.0 read pipeline: S3 list → gzipped-CSV read → aggregate
→ push, using a fake S3 that serves actual gzip(CSV) bytes (no boto3, no network).

CUR 2.0 uses OVERWRITE_REPORT (one current file set, no stale assemblies), so the
read path is a recursive glob of `.csv.gz` — no manifest, no dedup.
"""
import csv
import gzip
import io
from decimal import Decimal

from moo_cloud_bill.commands.push import read_cur_rows, run_push
from moo_cloud_bill.config import Config
from moo_cloud_bill.cur_columns import DEFAULT_COLUMN_MAP

from ._fakes import RecordingAcuteClient

CM = DEFAULT_COLUMN_MAP


def _csv_gz_bytes(rows: list[dict]) -> bytes:
    """Serialize row dicts to gzipped CSV, exactly as AWS Data Exports delivers."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return gzip.compress(buf.getvalue().encode("utf-8"))


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3Objects:
    """Serves one .csv.gz object under the export prefix; ignores non-.gz keys."""

    def __init__(self, key, data):
        self.key = key
        self.data = data

    def list_objects_v2(self, **kwargs):
        # A non-.gz sibling (e.g. a metadata/manifest file) must be filtered out.
        return {"Contents": [{"Key": self.key}, {"Key": "cost/hourly/r/metadata.json"}]}

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


def test_read_cur_rows_from_csv_gz():
    s3 = FakeS3Objects("cost/hourly/r/data.csv.gz", _csv_gz_bytes(_rows()))
    rows = read_cur_rows(_config(), {"s3": s3})
    assert len(rows) == 2
    assert rows[0][CM["service_name"]] == "AmazonBedrock"


def test_non_gz_keys_are_filtered():
    # The glob must only read .csv.gz; a metadata/manifest sibling must be skipped.
    from moo_cloud_bill.aws import list_data_object_keys

    s3 = FakeS3Objects("cost/hourly/r/data.csv.gz", _csv_gz_bytes(_rows()))
    keys = list_data_object_keys(s3, "b", "cost/hourly", "r")
    assert keys == ["cost/hourly/r/data.csv.gz"]


def test_csv_string_cost_becomes_decimal_without_drift():
    """CSV columns are always strings; the mapper must convert the cost string to a
    Decimal with no float rounding, and bucket by UTC day from the timestamp string.
    """
    from moo_cloud_bill.mapper import build_daily_batches

    rows = [{
        CM["service_name"]: "AmazonS3", CM["resource_id"]: "r1",
        CM["region"]: "us-east-1", CM["usage_type"]: "DataTransfer",
        CM["cost"]: "0.00015", CM["currency"]: "USD",
        CM["usage_start"]: "2026-05-14T23:30:00Z",
    }]
    s3 = FakeS3Objects("cost/hourly/r/data.csv.gz", _csv_gz_bytes(rows))

    read = read_cur_rows(_config(), {"s3": s3})
    assert isinstance(read[0][CM["cost"]], str)  # CSV yields strings, always

    batches, _ = build_daily_batches(read, CM)
    assert batches[0].rows[0].cost == Decimal("0.00015")  # exact Decimal, no float drift
    assert batches[0].billing_period_start.isoformat() == "2026-05-14T00:00:00+00:00"


def test_run_push_end_to_end_aggregates_and_posts():
    s3 = FakeS3Objects("cost/hourly/r/data.csv.gz", _csv_gz_bytes(_rows()))
    client = RecordingAcuteClient()
    rc = run_push(_config(), "mlk_key", clients={"s3": s3}, column_map=CM, client=client)
    assert rc == 0
    assert len(client.import_calls) == 1            # one day
    assert len(client.import_calls[0].rows) == 1    # aggregated to one grain
    assert str(client.import_calls[0].rows[0].cost) == "3.50"
