"""
Probe F192E.1 conftest — shared fixtures for E2E benchmark suite.

Edit ONLY these files:
- hledac/universal/tests/probe_sprint_benchmark/conftest.py
"""

import pytest

pytest_plugins = []


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "benchmark_e2e: E2E benchmark tests for canonical sprint path",
    )
    config.addinivalue_line(
        "markers",
        "memory_budget: memory ceiling tests for M1 8GB bounded runs",
    )