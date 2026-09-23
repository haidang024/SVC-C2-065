"""Unit tests for SynthesisNode — SVC-C2-065."""

from __future__ import annotations

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.synthesis_node import SynthesisNode
from src.schemas.state import from_json, to_json


def _base_state(**overrides) -> dict:
    state = {
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "correlation_id": "test-synthesis",
        "node_history": [],
        "error_log": [],
        "coverage_warnings_json": to_json([]),
        "staleness_warnings_json": to_json([]),
        "fulfillment_gaps_json": to_json([]),
        "resource_mappings_json": to_json([]),
        "department_coordination_json": to_json({}),
        "no_requests_flag": False,
        "venue_resource_stale": False,
        "total_request_count": 0,
    }
    state.update(overrides)
    return state


class TestSynthesisNode:

    def setup_method(self):
        self.node = SynthesisNode()

    def test_no_requests_produces_explicit_warning(self):
        """BL-06: Zero requests produces explicit no-requests warning and action item."""
        state = _base_state(no_requests_flag=True)
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        warnings = from_json(result["operational_warnings_json"]) or []
        assert any("NO REQUESTS" in w or "no accessibility requests" in w.lower() for w in warnings)
        items = from_json(result["action_items_json"]) or []
        has_no_requests_action = any(
            "channel" in item["action"].lower() or "verify" in item["action"].lower()
            for item in items
        )
        assert has_no_requests_action

    def test_coverage_warnings_appear_in_operational_warnings(self):
        """BL-04: Coverage warnings flow through to operational_warnings."""
        state = _base_state(coverage_warnings_json=to_json(["No records found for expected booking channel: 'phone'"]))
        result = self.node(state)
        warnings = from_json(result["operational_warnings_json"]) or []
        assert any("phone" in w for w in warnings)

    def test_staleness_warning_appears(self):
        """BL-05: Staleness warnings appear in operational_warnings."""
        state = _base_state(staleness_warnings_json=to_json(["Venue resource data is 45 day(s) old"]))
        result = self.node(state)
        warnings = from_json(result["operational_warnings_json"]) or []
        assert any("45" in w for w in warnings)

    def test_gap_produces_high_priority_action_item(self):
        """BL-07: Capacity gap produces HIGH priority action item."""
        gap = {
            "request_type": "wheelchair_space",
            "zone": "A1",
            "count_needed": 6,
            "capacity_available": 4,
            "gap": 2,
            "department": "Accessibility Coordination",
        }
        state = _base_state(fulfillment_gaps_json=to_json([gap]), total_request_count=6)
        result = self.node(state)

        items = from_json(result["action_items_json"]) or []
        high_items = [i for i in items if i["priority"] == "HIGH" and "wheelchair" in i["action"]]
        assert len(high_items) >= 1

    def test_large_gap_is_high_priority(self):
        """Gap >= 3 produces HIGH priority."""
        gap = {
            "request_type": "wheelchair_space",
            "zone": "A1",
            "count_needed": 8,
            "capacity_available": 4,
            "gap": 4,
            "department": "Accessibility Coordination",
        }
        state = _base_state(fulfillment_gaps_json=to_json([gap]))
        result = self.node(state)
        items = from_json(result["action_items_json"]) or []
        gap_item = next((i for i in items if "wheelchair" in i["action"]), None)
        assert gap_item is not None
        assert gap_item["priority"] == "HIGH"

    def test_all_standard_departments_have_action_items(self):
        """BL-13: All standard departments receive at least one action item."""
        state = _base_state()
        result = self.node(state)

        items = from_json(result["action_items_json"]) or []
        departments = {item["department"] for item in items}
        assert "Accessibility Coordination" in departments
        assert "Stewards" in departments
        assert "Medical/Welfare" in departments

    def test_stale_resources_with_requests_generates_review_flag(self):
        """BL-05: Stale resources with active requests generates stale_resource_with_requests flag."""
        state = _base_state(venue_resource_stale=True, total_request_count=3, no_requests_flag=False)
        result = self.node(state)

        flags = from_json(result["review_flags_json"]) or []
        has_stale_flag = any("stale" in f.get("flag_type", "") for f in flags)
        assert has_stale_flag

    def test_review_flags_for_coverage_gap(self):
        """Coverage warnings generate review flags."""
        state = _base_state(coverage_warnings_json=to_json(["No records for 'phone'"]))
        result = self.node(state)

        flags = from_json(result["review_flags_json"]) or []
        assert any("coverage" in f.get("flag_type", "") for f in flags)

    def test_all_output_fields_are_json_strings(self):
        """All _json-suffixed output fields must be JSON strings."""
        state = _base_state()
        result = self.node(state)
        for key in ("operational_warnings_json", "action_items_json", "review_flags_json"):
            assert isinstance(result[key], str), f"{key} must be a JSON string"

    def test_required_trust_level_is_anonymous(self):
        """Inner node must be ANONYMOUS."""
        assert SynthesisNode.required_trust_level == TrustLevel.ANONYMOUS
