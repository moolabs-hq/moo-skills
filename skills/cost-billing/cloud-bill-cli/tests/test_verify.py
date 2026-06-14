from moo_cloud_bill.acute_client import Result
from moo_cloud_bill.commands.verify import run_verify
from moo_cloud_bill.config import Config


class FakeAcute:
    def __init__(self, result):
        self._result = result

    def list_imports(self, provider="aws"):
        return self._result


def _cfg(**kw):
    base = dict(bucket="b", prefix="cost/hourly", report_name="r", acute_base="https://acute")
    base.update(kw)
    return Config(**base)


class _S3NoData:
    def get_object(self, Bucket, Key):  # noqa: N803 — manifest read → missing
        raise KeyError("no manifest")

    def list_objects_v2(self, **kwargs):
        return {"Contents": []}


def test_auth_failure_returns_nonzero():
    out = []
    rc = run_verify(_cfg(), "k", acute_client=FakeAcute(Result(401, {})), out=out.append)
    assert rc == 1
    assert "auth failed" in "\n".join(out)


def test_unreachable_acute_returns_nonzero():
    out = []
    rc = run_verify(_cfg(), "k", acute_client=FakeAcute(Result(0, {"error": "connect"})), out=out.append)
    assert rc == 1
    assert "Cannot reach Acute" in "\n".join(out)


def test_ok_but_no_cur_data_yet():
    out = []
    rc = run_verify(
        _cfg(), "k",
        acute_client=FakeAcute(Result(200, [])),
        clients={"s3": _S3NoData()},
        out=out.append,
    )
    assert rc == 0
    text = "\n".join(out)
    assert "Acute reachable" in text
    assert "not delivered yet" in text


def test_no_cur_configured_skips_data_check():
    out = []
    rc = run_verify(
        Config(acute_base="https://acute"),  # no bucket/report
        "k", acute_client=FakeAcute(Result(200, [])), out=out.append,
    )
    assert rc == 0
    assert "No CUR configured yet" in "\n".join(out)
