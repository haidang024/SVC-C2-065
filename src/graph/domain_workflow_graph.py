"""DomainWorkflowGraph — Inner 4-step accessibility request history workflow for SVC-C2-065."""

from __future__ import annotations

from typing import Any

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from langgraph.graph import END, START

from src.nodes.request_classification_node import RequestClassificationNode
from src.nodes.request_history_node import RequestHistoryNode
from src.nodes.synthesis_node import SynthesisNode
from src.nodes.venue_resource_node import VenueResourceNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner accessibility workflow graph for SVC-C2-065.

    Pipeline (linear — all nodes read-only, no source writes):
        START → request_history → venue_resource → request_classification → synthesis → END

    All inner nodes carry TrustLevel.ANONYMOUS (trust verified at outer PreProcessNode boundary).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        super().__init__(config)

    @property
    def name(self) -> str:
        return "svc_c2_065_accessibility_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """No mandatory config — outer pre_process validates all sources."""

    def register_nodes(self) -> None:
        """Register inner domain nodes. No super() call (BaseGraph abstract)."""
        self._nodes["request_history"] = RequestHistoryNode()
        self._nodes["venue_resource"] = VenueResourceNode()
        self._nodes["request_classification"] = RequestClassificationNode()
        self._nodes["synthesis"] = SynthesisNode()

    def add_edges(self) -> None:
        """Wire the linear accessibility workflow topology."""
        self._sg.add_edge(START, "request_history")
        self._sg.add_edge("request_history", "venue_resource")
        self._sg.add_edge("venue_resource", "request_classification")
        self._sg.add_edge("request_classification", "synthesis")
        self._sg.add_edge("synthesis", END)

    def route(self, state: AgentState) -> str:
        """Required by BaseGraph ABC. Linear topology — not called unless conditional edges added."""
        return str(END) if state.get("status") == AgentStatus.ERROR.value else "synthesis"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape sub_result returned to AccessibilityWorkflowGraphNode.merge_output()."""
        return {
            "sanitized_requests_json": state.get("sanitized_requests_json"),
            "total_request_count": state.get("total_request_count", 0),
            "no_requests_flag": state.get("no_requests_flag", False),
            "source_coverage_json": state.get("source_coverage_json"),
            "coverage_warnings_json": state.get("coverage_warnings_json"),
            "request_source_citations_json": state.get("request_source_citations_json"),
            "venue_resources_json": state.get("venue_resources_json"),
            "venue_resource_version": state.get("venue_resource_version"),
            "venue_resource_last_updated": state.get("venue_resource_last_updated"),
            "venue_resource_stale": state.get("venue_resource_stale", False),
            "staleness_warnings_json": state.get("staleness_warnings_json"),
            "venue_resource_citations_json": state.get("venue_resource_citations_json"),
            "request_type_counts_json": state.get("request_type_counts_json"),
            "resource_mappings_json": state.get("resource_mappings_json"),
            "fulfillment_gaps_json": state.get("fulfillment_gaps_json"),
            "department_coordination_json": state.get("department_coordination_json"),
            "operational_warnings_json": state.get("operational_warnings_json"),
            "action_items_json": state.get("action_items_json"),
            "review_flags_json": state.get("review_flags_json"),
            "status": state.get("status", AgentStatus.SUCCESS.value),
            "error_message": state.get("error_message"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
