"""
connectors/base.py
Abstract base class for all data source connectors.

Connectors handle raw data access only -- no business logic.
They check settings.use_mock_data and fall back to local JSON fixtures
when True, allowing the pipeline to run without real API keys.

Subclass responsibilities:
  - Implement fetch_* / search_* / screen_* methods for each endpoint.
  - Use self._load_mock(filename) for mock fallback.
  - Use self._get(url, ...) / self._post(url, ...) for HTTP calls.
  - Never raise unhandled exceptions -- catch and re-raise as ConnectorError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import settings
from src.utils import load_json_file, mock_data_path


class ConnectorError(Exception):
    """Raised when a connector cannot retrieve data."""


class BaseConnector:
    """Base class for all data source connectors."""

    mock_filename: str = ""

    def __init__(self) -> None:
        self.use_mock = settings.use_mock_data
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _load_mock(self, filename: str) -> dict[str, Any]:
        path = mock_data_path(filename)
        try:
            return load_json_file(path)
        except FileNotFoundError as e:
            raise ConnectorError(f"Mock file not found: {path}") from e

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated GET request and return parsed JSON."""
        try:
            response = self._session.get(
                url, params=params, auth=auth, headers=headers, timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise ConnectorError(f"HTTP error {e.response.status_code}: {url}") from e
        except requests.exceptions.RequestException as e:
            raise ConnectorError(f"Request failed: {url} -- {e}") from e

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform a POST request with a JSON body and return parsed JSON."""
        try:
            response = self._session.post(
                url,
                json=payload,
                params=params,
                headers=headers,
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise ConnectorError(f"HTTP error {e.response.status_code}: {url}") from e
        except requests.exceptions.RequestException as e:
            raise ConnectorError(f"Request failed: {url} -- {e}") from e
