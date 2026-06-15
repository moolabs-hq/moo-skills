from moo_cloud_bill.commands.configure import run_configure
from moo_cloud_bill.report_definition import build_data_export
from moo_cloud_bill.ui import ScriptedUI

from ._fakes import clients

# A reusable CUR 2.0 export (full Export dict).
USABLE_EXPORT = build_data_export(name="existing-cur2", s3_bucket="existing-bucket", s3_prefix="cur2")


def test_reuse_path_does_not_create(tmp_path):
    cl = clients(exports=[USABLE_EXPORT])
    ui = ScriptedUI(confirms=[True], answers=["USD", ""])  # reuse yes; currency; acute default
    cfg = run_configure(cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml")
    assert cfg.bucket == "existing-bucket"
    assert cfg.report_name == "existing-cur2"
    assert cl["exports"].created == []  # nothing created


def test_acute_base_formed_from_domain(tmp_path):
    cl = clients(exports=[USABLE_EXPORT])
    ui = ScriptedUI(confirms=[True], answers=["USD", "dev.moolabs.com"])
    cfg = run_configure(cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml")
    assert cfg.acute_base == "https://acute.dev.moolabs.com"


def test_create_path_calls_create_export(tmp_path):
    cl = clients(exports=[], buckets=["mybucket"])
    ui = ScriptedUI(
        choices=[0],            # pick existing bucket 'mybucket'
        confirms=[True],        # single confirm: apply policy + create export
        answers=["cur2", "moolabs-cur2", "USD", ""],
    )
    cfg = run_configure(cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml")
    assert len(cl["exports"].created) == 1
    created = cl["exports"].created[0]
    assert created["DataQuery"]["TableConfigurations"]["COST_AND_USAGE_REPORT"]["TIME_GRANULARITY"] == "HOURLY"
    assert cfg.bucket == "mybucket"
    assert cl["s3"].policy_calls  # bucket policy applied (before the export create)


def test_create_new_bucket_then_create_export(tmp_path):
    cl = clients(exports=[], buckets=["existing"])
    ui = ScriptedUI(
        choices=[1],            # "create a new bucket" (index == len(existing))
        confirms=[True, True],  # create-bucket yes; then apply-policy+create-export yes
        answers=["my-new-bucket", "cur2", "moolabs-cur2", "USD", ""],
    )
    cfg = run_configure(cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml")
    assert cl["s3"].created_buckets == ["my-new-bucket"]
    assert cfg.bucket == "my-new-bucket"
    assert len(cl["exports"].created) == 1
    assert cl["s3"].policy_calls  # policy applied to the new bucket before the export


def test_dry_run_create_does_not_mutate(tmp_path):
    cl = clients(exports=[], buckets=["mybucket"])
    ui = ScriptedUI(choices=[0], answers=["cur2", "moolabs-cur2"])
    result = run_configure(
        cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml", dry_run=True
    )
    assert result is None
    assert cl["exports"].created == []
    assert cl["s3"].created_buckets == []


def test_dry_run_reuse_writes_no_files(tmp_path):
    from moo_cloud_bill.config import config_path

    cl = clients(exports=[USABLE_EXPORT])
    ui = ScriptedUI(confirms=[True])  # reuse yes
    cm_path = tmp_path / "cm.yaml"
    result = run_configure(cl, ui, config_dir=tmp_path, column_map_path=cm_path, dry_run=True)
    assert result is None
    assert not cm_path.exists()
    assert not config_path(tmp_path).exists()


def test_aws_profile_is_persisted(tmp_path):
    from moo_cloud_bill.config import load_config

    cl = clients(exports=[USABLE_EXPORT])
    ui = ScriptedUI(confirms=[True], answers=["USD", ""])
    cfg = run_configure(
        cl, ui, config_dir=tmp_path, column_map_path=tmp_path / "cm.yaml", aws_profile="myprofile"
    )
    assert cfg.aws_profile == "myprofile"
    assert load_config(config_dir=tmp_path, env={}).aws_profile == "myprofile"


def test_reuse_saves_known_column_map(tmp_path):
    from moo_cloud_bill.cur_columns import load_column_map

    cl = clients(exports=[USABLE_EXPORT])
    ui = ScriptedUI(confirms=[True], answers=["USD", ""])
    cm_path = tmp_path / "cm.yaml"
    run_configure(cl, ui, config_dir=tmp_path, column_map_path=cm_path)
    cmap = load_column_map(cm_path)
    assert cmap["region"] == "product_region_code"      # CUR 2.0 column
    assert cmap["cost"] == "line_item_unblended_cost"
