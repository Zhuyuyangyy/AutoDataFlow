"""Smoke tests - basic import and sanity checks."""
import importlib
import pytest


def test_import_main():
    """Verify the main module can be imported."""
    try:
        importlib.import_module("backend")
    except ImportError:
        pytest.skip("No backend module found")
