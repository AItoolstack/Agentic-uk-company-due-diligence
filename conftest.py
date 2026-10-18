"""
conftest.py
Root-level pytest configuration.
Adds the project root to sys.path so all tests can import from src.*.
"""
import sys
from pathlib import Path

# Ensure project root (containing src/) is importable
sys.path.insert(0, str(Path(__file__).parent))

import pytest


@pytest.fixture(autouse=True)
def _reset_prescreen_store():
    """Each test starts with the default store; tests that need one set it themselves."""
    yield
    from src.prescreen.store import set_store
    set_store(None)
