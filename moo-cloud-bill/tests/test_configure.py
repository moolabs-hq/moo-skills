from moo_cloud_bill.commands.configure import run_configure
from moo_cloud_bill.cur_columns import load_column_map
from moo_cloud_bill.ui import ScriptedUI

from ._fakes import clients

USABLE_CUR = {
    "ReportName": "existing-cur",
    "TimeUnit": "HOURLY",
    "AdditionalSchemaElements": ["RESOURCES"],
    "S3Bucket": "existing-bucket",
    "S3Prefix": "cost/hourly",
    "S3Region": "us-east-1",
}


def test_reuse_path_does_not_create(tmp_path):
    cl = clients(report_definitions=[USABLE_CUR])
    ui = ScriptedUI(confirms=[True], answers=["USD", ""])  # reuse yes; currency; acute default
    cfg = run_configure(cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml")
    assert cfg.bucket == "existing-bucket"
    assert cfg.report_name == "existing-cur"
    assert cl["cur"].put_calls == []  # nothing created


def test_create_path_calls_put_report_definition(tmp_path):
    cl = clients(report_definitions=[], buckets=["mybucket"])
    ui = ScriptedUI(
        confirms=[True, True],  # create yes; apply bucket policy yes
        answers=["cost/hourly", "moolabs-cur", "USD", ""],
    )
    cfg = run_configure(cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml")
    assert len(cl["cur"].put_calls) == 1
    assert cl["cur"].put_calls[0]["TimeUnit"] == "HOURLY"
    assert cfg.bucket == "mybucket"
    assert cl["s3"].policy_calls  # bucket policy applied


def test_dry_run_create_does_not_mutate(tmp_path):
    cl = clients(report_definitions=[], buckets=["mybucket"])
    ui = ScriptedUI(answers=["cost/hourly", "moolabs-cur"])
    result = run_configure(
        cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml", dry_run=True
    )
    assert result is None
    assert cl["cur"].put_calls == []


def test_manifest_autofills_column_map(tmp_path):
    manifest = {"columns": [
        {"category": "lineItem", "name": "ProductCode"},
        {"category": "lineItem", "name": "UnblendedCost"},
    ]}
    cl = clients(report_definitions=[USABLE_CUR], manifest=manifest)
    ui = ScriptedUI(confirms=[True], answers=["USD", ""])
    cm_path = tmp_path / "cm.yaml"
    run_configure(cl, ui, config_dir=tmp_path, column_map_path=cm_path)
    cmap = load_column_map(cm_path)
    assert cmap["service_name"] == "line_item_product_code"
    assert cmap["cost"] == "line_item_unblended_cost"
