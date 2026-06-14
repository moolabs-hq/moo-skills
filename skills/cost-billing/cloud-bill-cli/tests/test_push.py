from moo_cloud_bill.commands.push import push_batches
from moo_cloud_bill.cur_columns import DEFAULT_COLUMN_MAP
from moo_cloud_bill.mapper import build_daily_batches

from ._fakes import RecordingAcuteClient

CM = DEFAULT_COLUMN_MAP


def _rows():
    return [
        {CM["service_name"]: "AmazonBedrock", CM["resource_id"]: "r1",
         CM["region"]: "us-east-1", CM["usage_type"]: "Invoke",
         CM["cost"]: "1.00", CM["currency"]: "USD",
         CM["usage_start"]: "2026-05-14T03:00:00Z"},
        {CM["service_name"]: "AmazonBedrock", CM["resource_id"]: "r1",
         CM["region"]: "us-east-1", CM["usage_type"]: "Invoke",
         CM["cost"]: "2.00", CM["currency"]: "USD",
         CM["usage_start"]: "2026-05-15T03:00:00Z"},
    ]


def test_push_posts_one_batch_per_day():
    batches, credits = build_daily_batches(_rows(), CM)
    client = RecordingAcuteClient()
    summary = push_batches(batches, credits, client)
    assert summary.ok == 2
    assert summary.failed == 0
    assert len(client.import_calls) == 2
    assert summary.exit_code == 0


def test_dry_run_sends_nothing():
    batches, credits = build_daily_batches(_rows(), CM)
    client = RecordingAcuteClient()
    summary = push_batches(batches, credits, client, dry_run=True)
    assert client.import_calls == []
    assert summary.dry_run


def test_failed_day_sets_exit_code():
    batches, credits = build_daily_batches(_rows(), CM)
    summary = push_batches(batches, credits, RecordingAcuteClient(status=500))
    assert summary.failed == 2
    assert summary.exit_code == 1


def test_transport_error_on_one_day_does_not_skip_others():
    from moo_cloud_bill.acute_client import Result

    class FlakyClient:
        def __init__(self):
            self.calls = 0

        def import_batch(self, batch):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("down")  # retries already exhausted upstream
            return Result(201, {})

    batches, credits = build_daily_batches(_rows(), CM)  # two days
    client = FlakyClient()
    summary = push_batches(batches, credits, client)
    assert client.calls == 2          # second day still attempted
    assert summary.ok == 1
    assert summary.failed == 1
    assert summary.exit_code == 1
