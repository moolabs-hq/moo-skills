from moo_cloud_bill.commands.detect import detect_aws


def test_detects_boto3_in_python(tmp_path):
    (tmp_path / "app.py").write_text("import boto3\nclient = boto3.client('s3')\n")
    result = detect_aws(tmp_path)
    assert result.detected
    assert any(token == "boto3" for _, _, token in result.evidence)


def test_detects_aws_sdk_in_typescript(tmp_path):
    (tmp_path / "svc.ts").write_text("import { S3 } from '@aws-sdk/client-s3';\n")
    result = detect_aws(tmp_path)
    assert result.detected


def test_no_detection_in_clean_repo(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    assert detect_aws(tmp_path).detected is False


def test_skips_vendored_dirs(tmp_path):
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "index.js").write_text("require('aws-sdk')\n")
    assert detect_aws(tmp_path).detected is False
