"""
AutoDataFlow Test Configuration
================================
Shared fixtures and configuration for all tests.
"""

import sys
import os
import pytest

# Add backend to path for all test files
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
