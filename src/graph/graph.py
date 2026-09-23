"""Graph — Outer AgentBaseGraph for SVC-C2-065 Sports Ticketing Accessibility Request History Agent."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class AccessibilityWorkflowGraphNode(GraphNode):
    """GraphNode wrapping the inner accessibility request history workflow.

    Assigned to the `main` slot of the outer AgentBaseGraph. The inner graph
    runs: request_history → venue_resource → request_classification → synthesis.
    Passes all required state fields (event details, source config, retrieval scope)
    into the inner graph and merges all domain outputs back into the outer state.
    """

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any] | None = None, llm: Any = None) -> None:
        super().__init__()
        self._config = dict(config or {})
        self._llm = llm
        self._config["llm"] = llm

    def get_subgraph(self) -> Any:
        """Instantiate the inner domain workflow graph."""
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return validated_input as the inner graph invocation trigger string."""
        return str(state.get("validated_input", state.get("user_input", "")))

    def execute(self, state: AgentState) -> dict[str, Any]:
        """Override to pass domain fields as input_context to the inner subgraph.

        The framework's GraphNode.execute() calls subgraph.invoke(user_input, ctx=ctx)
        without input_context. Inner nodes need retrieval_scope_json,
        request_history_source_json, venue_resource_source_json, etc. from outer state.
        """
        if state.get("input_error_message"):
            return {"status": AgentStatus.SUCCESS.value}
        subgraph_hitl_allowed = self.propagate_hitl and state.get("hitl_allowed", True)
        ctx = replace(InvocationContext.from_state(state), hitl_allowed=subgraph_hitl_allowed)
        subgraph = self.get_subgraph()
        user_input = self.extract_input(state)

        # Forward all domain fields so inner nodes can read them from state.
        input_context = {
            k: v
            for k, v in {
                "event_id": state.get("event_id", ""),
                "event_date": state.get("event_date", ""),
                "event_start_time": state.get("event_start_time", ""),
                "event_name": state.get("event_name", ""),
                "retrieval_scope_json": state.get("retrieval_scope_json", ""),
                "request_history_source_json": state.get("request_history_source_json", ""),
                "venue_resource_source_json": state.get("venue_resource_source_json", ""),
                "expected_booking_channels_json": state.get("expected_booking_channels_json", ""),
                "pii_fields_json": state.get("pii_fields_json", ""),
                "staleness_threshold_days": str(state.get("staleness_threshold_days", "")),
            }.items()
            if v
        }

        try:
            sub_result = subgraph.invoke(
                user_input,
                session_id=ctx.session_id,
                ctx=ctx,
                input_context=input_context,
            )
        except Exception as exc:
            return cast(dict[str, Any], self._handle_call_error(subgraph, exc, state))

        if sub_result.get("status") == AgentStatus.ERROR.value:
            from framework.nodes.graph_node import SubgraphError

            error = SubgraphError(
                agent_name=subgraph.name,
                error_log=sub_result.get("error_log", []),
                trace_id=sub_result.get("trace_id", ""),
            )
            if self.error_strategy == "propagate":
                raise error
            return cast(dict[str, Any], self.on_subgraph_error(state, error))

        return self.merge_output(state, sub_result)

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map all inner graph outputs back into the outer state.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys — never the full state.
        """
        del state  # not used; output comes entirely from sub_result
        return {
            "sanitized_requests_json": sub_result.get("sanitized_requests_json"),
            "total_request_count": sub_result.get("total_request_count", 0),
            "no_requests_flag": sub_result.get("no_requests_flag", False),
            "source_coverage_json": sub_result.get("source_coverage_json"),
            "coverage_warnings_json": sub_result.get("coverage_warnings_json"),
            "request_source_citations_json": sub_result.get("request_source_citations_json"),
            "venue_resources_json": sub_result.get("venue_resources_json"),
            "venue_resource_version": sub_result.get("venue_resource_version"),
            "venue_resource_last_updated": sub_result.get("venue_resource_last_updated"),
            "venue_resource_stale": sub_result.get("venue_resource_stale", False),
            "staleness_warnings_json": sub_result.get("staleness_warnings_json"),
            "venue_resource_citations_json": sub_result.get("venue_resource_citations_json"),
            "request_type_counts_json": sub_result.get("request_type_counts_json"),
            "resource_mappings_json": sub_result.get("resource_mappings_json"),
            "fulfillment_gaps_json": sub_result.get("fulfillment_gaps_json"),
            "department_coordination_json": sub_result.get("department_coordination_json"),
            "operational_warnings_json": sub_result.get("operational_warnings_json"),
            "action_items_json": sub_result.get("action_items_json"),
            "review_flags_json": sub_result.get("review_flags_json"),
            "status": sub_result.get("status", AgentStatus.SUCCESS.value),
            "error_message": sub_result.get("error_message"),
        }

    def _parent_config(self) -> dict[str, Any]:
        """Forward relevant config keys to the inner graph."""
        return self._config


class Graph(AgentBaseGraph):
    """Outer Cat 2 AgentBaseGraph for SVC-C2-065 SportsTicketingAccessibilityRequestHistoryAgent.

    Backbone: initialize → pre_process → main (AccessibilityWorkflowGraphNode) → post_process → finalize.
    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        super().__init__(config=self._config)

    @property
    def name(self) -> str:
        return "svc_c2_065"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = AccessibilityWorkflowGraphNode(
            config=self.config,
            llm=self.config.get("llm"),
        )
        self._nodes["post_process"] = PostProcessNode(
            llm=self.config.get("llm"),
            config=self.config,
        )

    def get_output(self, state: AgentState) -> dict[str, Any]:
        output = {
            "output": state.get("formatted_output") or state.get("result", ""),
            "result": state.get("result", ""),
            "status": state.get("status", AgentStatus.ERROR.value),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "generation_mode": state.get("generation_mode"),
            "provider_error_message": state.get("provider_error_message"),
        }
        _set_marketplace_guidance(output, state, "Accessibility request-history request")
        return output


def _set_marketplace_guidance(output: dict[str, Any], state: AgentState, subject: str) -> None:
    context = state.get("input_context")
    message = state.get("input_error_message")
    if not (isinstance(context, dict) and "conversation_history" in context and message):
        return
    lines = [f"{subject} could not be processed.", "", f"Reason: {message}"]
    guidance = state.get("input_error_guidance")
    if isinstance(guidance, str) and guidance:
        lines.extend(["", "How to continue:"])
        lines.extend(f"- {item}" for item in guidance.splitlines())
    output["output"] = "\n".join(lines)

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
