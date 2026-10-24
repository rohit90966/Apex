import pytest
from app.investigation.url_normalizer import normalize_url, is_same_origin


def test_url_normalizer_standardizes_paths():
    base = "http://127.0.0.1:8001/app/"
    assert normalize_url("http://127.0.0.1:8001/login") == "http://127.0.0.1:8001/login"
    assert normalize_url("http://127.0.0.1:8001/login/") == "http://127.0.0.1:8001/login"
    assert normalize_url("http://127.0.0.1:8001/") == "http://127.0.0.1:8001/"
    assert normalize_url("/search", base_url=base) == "http://127.0.0.1:8001/search"


def test_url_normalizer_removes_fragments():
    url = "http://127.0.0.1:8001/login#section-1"
    assert normalize_url(url) == "http://127.0.0.1:8001/login"


def test_url_normalizer_redacts_sensitive_params():
    url = "http://127.0.0.1:8001/auth?token=my_secret_token_123&user=john"
    normalized = normalize_url(url)
    assert "%5BREDACTED%5D" in normalized or "[REDACTED]" in normalized
    assert "user=john" in normalized


def test_url_normalizer_rejects_non_http_and_static_files():
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url("mailto:test@example.com") is None
    assert normalize_url("http://127.0.0.1:8001/style.css") is None
    assert normalize_url("http://127.0.0.1:8001/image.png") is None


def test_is_same_origin():
    assert is_same_origin("http://127.0.0.1:8001/a", "http://127.0.0.1:8001/b") is True
    assert is_same_origin("http://127.0.0.1:8001/a", "http://127.0.0.1:8002/a") is False
    assert is_same_origin("http://127.0.0.1:8001/a", "https://127.0.0.1:8001/a") is False
    assert is_same_origin("http://127.0.0.1:8001/a", "http://localhost:8001/a") is False
