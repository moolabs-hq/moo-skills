from datetime import datetime, timezone
from decimal import Decimal

from moo_cloud_bill.acute_client import IMPORT_PATH, RESOURCE_MAP_PATH, AcuteClient
from moo_cloud_bill.models import CloudCostRow, ImportBatch


def _batch():
    return ImportBatch(
        cloud_provider="aws",
        billing_period_start=datetime(2026, 5, 14, tzinfo=timezone.utc),
        billing_period_end=datetime(2026, 5, 15, tzinfo=timezone.utc),
        rows=[CloudCostRow(service_name="AmazonBedrock", cost=Decimal("3.75"))],
    )


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, json_body):
        self.calls.append((url, headers, json_body))
        return self.responses.pop(0)


def test_import_uses_bearer_and_correct_url_and_no_tenant_id():
    rec = Recorder([(201, {})])
    client = AcuteClient("https://acute.example/", "mlk_key", post=rec)
    res = client.import_batch(_batch())
    url, headers, body = rec.calls[0]
    assert res.status_code == 201
    assert url == "https://acute.example" + IMPORT_PATH
    assert headers["Authorization"] == "Bearer mlk_key"
    assert "tenant_id" not in body
    assert body["cloud_provider"] == "aws"


def test_retries_on_5xx_then_succeeds():
    rec = Recorder([(503, {}), (201, {})])
    client = AcuteClient("https://acute.example", "k", post=rec, sleep=lambda *_: None)
    res = client.import_batch(_batch())
    assert res.status_code == 201
    assert len(rec.calls) == 2


def test_retries_on_transport_error_then_succeeds():
    calls = {"n": 0}

    def flaky_post(url, headers, json_body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("connection reset")
        return (201, {})

    client = AcuteClient("https://acute.example", "k", post=flaky_post, sleep=lambda *_: None)
    res = client.import_batch(_batch())
    assert res.status_code == 201
    assert calls["n"] == 2


def test_transport_error_surfaces_after_max_retries():
    import pytest

    def always_fails(url, headers, json_body):
        raise ConnectionError("down")

    client = AcuteClient(
        "https://acute.example", "k", post=always_fails, max_retries=2, sleep=lambda *_: None
    )
    with pytest.raises(ConnectionError):
        client.import_batch(_batch())


def test_does_not_retry_on_4xx():
    rec = Recorder([(422, {"detail": "bad"})])
    client = AcuteClient("https://acute.example", "k", post=rec, sleep=lambda *_: None)
    res = client.import_batch(_batch())
    assert res.status_code == 422
    assert len(rec.calls) == 1


def test_resource_map_url_and_no_tenant():
    rec = Recorder([(201, {})])
    client = AcuteClient("https://acute.example", "k", post=rec)
    client.upsert_resource_map({"cloud_provider": "aws", "resource_id": "r", "service_name": "ai"})
    url, _, body = rec.calls[0]
    assert url.endswith(RESOURCE_MAP_PATH)
    assert "tenant_id" not in body


def test_list_imports_get_url_and_bearer():
    captured = {}

    def fake_get(url, headers):
        captured["url"], captured["headers"] = url, headers
        return (200, [])

    res = AcuteClient("https://acute.example", "k", get=fake_get).list_imports("aws")
    assert res.status_code == 200
    assert "/api/v1/cloud-billing/imports?cloud_provider=aws" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer k"


def test_list_imports_transport_error_returns_status_0():
    def boom(url, headers):
        raise ConnectionError("down")

    res = AcuteClient("https://acute.example", "k", get=boom).list_imports()
    assert res.status_code == 0
    assert "down" in res.body["error"]
