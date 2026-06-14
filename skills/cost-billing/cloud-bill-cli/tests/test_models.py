from decimal import Decimal

from moo_cloud_bill.models import CloudCostRow


def test_to_body_cost_is_fixed_point_not_scientific():
    body = CloudCostRow(service_name="AWSLambda", cost=Decimal("5E-7")).to_body()
    assert body["cost"] == "0.0000005"   # never "5E-7"
    assert "tenant_id" not in body


def test_to_body_preserves_decimal_precision():
    body = CloudCostRow(service_name="AmazonS3", cost=Decimal("1234.567890")).to_body()
    assert body["cost"] == "1234.567890"
