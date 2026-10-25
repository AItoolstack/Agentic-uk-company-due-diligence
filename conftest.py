"""
conftest.py
Root-level pytest configuration.
Adds the project root to sys.path so all tests can import from src.*.
"""
import sys
from pathlib import Path

# Ensure project root (containing src/) is importable
sys.path.insert(0, str(Path(__file__).parent))

import os
import tempfile

# Tests that save reports must not write into ./output: a reader who runs the tests before the app would
# otherwise see the tests' cases in the app. Set before anything imports the store.
os.environ.setdefault("PRESCREEN_DATA_DIR", tempfile.mkdtemp(prefix="prescreen-tests-"))

import pytest


@pytest.fixture(autouse=True)
def _reset_prescreen_store():
    """Each test starts with the default store; tests that need one set it themselves."""
    yield
    from src.prescreen.store import set_store
    set_store(None)


@pytest.fixture(autouse=True)
def _reset_run_limit():
    """The hourly pre-screen limit is process-wide (API and MCP share it); tests mustn't use each other's budget."""
    from src.api import prescreen_routes
    prescreen_routes._recent.clear()
    yield
    prescreen_routes._recent.clear()
