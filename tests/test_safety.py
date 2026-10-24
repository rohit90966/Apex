import pytest
from app.safety import validate_url_safety, SafetyViolation


def test_allowed_localhost():
    allowed = ["localhost", "127.0.0.1"]
    ok, host = validate_url_safety("http://127.0.0.1:8001/login", allowed)
    assert ok is True
    assert host == "127.0.0.1"


def test_reject_ftp_protocol():
    with pytest.raises(SafetyViolation, match="Only HTTP and HTTPS"):
        validate_url_safety("ftp://127.0.0.1:8001/file", ["127.0.0.1"])


def test_reject_credentials_in_url():
    with pytest.raises(SafetyViolation, match="embedded username or password"):
        validate_url_safety("http://admin:secret@127.0.0.1:8001/", ["127.0.0.1"])


def test_reject_unapproved_host():
    with pytest.raises(SafetyViolation, match="not in the configured allowed hosts"):
        validate_url_safety("https://example.com/test", ["127.0.0.1", "localhost"])


def test_reject_cloud_metadata():
    with pytest.raises(SafetyViolation):
        # 169.254.169.254 is link-local / AWS metadata
        validate_url_safety("http://169.254.169.254/latest/meta-data/", ["169.254.169.254"])
