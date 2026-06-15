"""FCA Register connector authentication tests."""

from __future__ import annotations

from src.config import settings
from src.connectors.fca_register import FCARegisterConnector


def test_fca_headers_include_key_and_developer_email(monkeypatch) -> None:
    connector = FCARegisterConnector()

    monkeypatch.setattr(settings, "fca_api_key", "test-key")
    monkeypatch.setattr(settings, "fca_api_email", "developer@example.com")

    headers = connector._request_headers()

    assert headers["X-Auth-Key"] == "test-key"
    assert headers["X-Auth-Email"] == "developer@example.com"
