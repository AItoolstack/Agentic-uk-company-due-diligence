"""OpenSanctions connector authentication tests."""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.open_sanctions import OpenSanctionsConnector


def test_open_sanctions_uses_documented_authorization_header(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    connector = OpenSanctionsConnector()
    connector.use_mock = False

    def fake_post(
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        captured.update(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
            }
        )
        return {"responses": {}}

    monkeypatch.setattr(settings, "open_sanctions_api_key", "test-key")
    monkeypatch.setattr(connector, "_post", fake_post)

    result = connector.screen_entities("Example Ltd", ["Jane Example"])

    assert captured["headers"]["Authorization"] == "ApiKey test-key"
    assert "X-Api-Key" not in captured["headers"]
    assert result["screened_count"] == 2
