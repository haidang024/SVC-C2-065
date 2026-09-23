"""MainNode — Not used directly in Cat 2; domain logic runs through DomainWorkflowGraphNode in graph.py."""

# Cat 2 note: The `main` slot in the outer AgentBaseGraph is filled by
# AccessibilityWorkflowGraphNode (a GraphNode subclass) defined in graph.py.
# This file is retained for scaffold compatibility but not instantiated.

from __future__ import annotations

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel


class MainNode(FunctionNode):
    """Placeholder — not used in Cat 2 outer graph. See graph.py for AccessibilityWorkflowGraphNode."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    _s4_audit_exempt: ClassVar[str] = "scaffold placeholder, never instantiated in the Cat 2 outer graph"

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"status": AgentStatus.ERROR.value, "error_message": "MainNode is not used in Cat 2 pipeline"}
