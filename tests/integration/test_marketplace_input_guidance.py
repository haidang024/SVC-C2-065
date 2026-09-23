"""Regression coverage for readable Marketplace input guidance."""

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph


def test_invalid_marketplace_request_returns_readable_guidance() -> None:
    result = Graph().invoke(
        "Hello, hi",
        ctx=InvocationContext(caller_trust_level=TrustLevel.INTERNAL),
        input_context={"conversation_history": []},
    )

    assert result["status"] == "success"
    assert result["output"].startswith("Accessibility request-history request could not be processed.")
    assert "Reason:" in result["output"]
    assert "How to continue:" in result["output"]
