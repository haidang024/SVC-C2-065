"""conftest.py — Framework stubs for SVC-C2-065 test harness.

This file provides framework-level fixtures and stubs required for running
tests without a live AgentCore environment. Must exist at the project root.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=False)
def mock_secrets_provider(monkeypatch):
    """Provide a mock secrets provider that returns placeholder values for any key."""
    class MockSecretsProvider:
        def require(self, key: str) -> str:
            return f"mock-secret-for-{key}"

        def get(self, key: str, default: str | None = None) -> str | None:
            return f"mock-secret-for-{key}"

    return MockSecretsProvider()
