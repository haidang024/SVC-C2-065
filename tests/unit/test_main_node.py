"""SVC-C2-065 — Unit Tests: Cat 2 node contract verification.

In Cat 2, the `main` slot is filled by AccessibilityWorkflowGraphNode (a GraphNode),
not by MainNode. This file verifies the node contract for the Cat 2 architecture.
"""

from __future__ import annotations

import inspect

import pytest

from src.nodes.main_node import MainNode
from framework.schemas.trust_level import TrustLevel


class TestMainNodeContract:
    """Verify MainNode placeholder contract for Cat 2."""

    def test_execute_method_signature(self):
        """Node contract: MainNode must implement execute(state)."""
        assert hasattr(MainNode, "execute"), "MainNode must implement execute()"

        sig = inspect.signature(MainNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2, f"execute() must accept (self, state), got: {params}"
        assert params[1] == "state", f"Second parameter must be 'state', got '{params[1]}'"

        assert "_invoke_impl" not in MainNode.__dict__, (
            "_invoke_impl() must not be defined in MainNode — use execute() instead"
        )

    def test_required_trust_level_declared(self):
        """required_trust_level must be declared with ClassVar[TrustLevel]."""
        assert hasattr(MainNode, "required_trust_level")
        assert isinstance(MainNode.required_trust_level, TrustLevel)

    def test_no_invoke_impl_override(self):
        """MainNode must not define _invoke_impl."""
        assert "_invoke_impl" not in MainNode.__dict__
