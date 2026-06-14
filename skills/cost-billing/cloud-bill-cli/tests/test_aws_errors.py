from moo_cloud_bill import aws
from moo_cloud_bill.errors import MooCloudBillError


class TokenRetrievalError(Exception):  # mimics botocore's class by NAME
    pass


class _ClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


def test_expired_sso_token_maps_to_friendly_error():
    friendly = aws.as_friendly_error(TokenRetrievalError("Token has expired"))
    assert isinstance(friendly, MooCloudBillError)
    assert "aws sso login" in str(friendly)


def test_access_denied_code_maps_to_friendly_error():
    assert isinstance(aws.as_friendly_error(_ClientError("AccessDenied")), MooCloudBillError)


def test_unexpected_errors_are_not_swallowed():
    assert aws.as_friendly_error(KeyError("real bug")) is None
    assert aws.as_friendly_error(_ClientError("NoSuchKey")) is None  # not an auth code
