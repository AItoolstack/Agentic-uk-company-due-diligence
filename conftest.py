"""
conftest.py
Root-level pytest configuration.
Adds the project root to sys.path so all tests can import from src.*.
"""
import sys
from pathlib import Path

# Ensure project root (containing src/) is importable
sys.path.insert(0, str(Path(__file__).parent))
