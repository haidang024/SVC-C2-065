"""Unit tests for RequestClassificationNode — SVC-C2-065."""

from __future__ import annotations

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.request_classification_node import RequestClassificationNode
from src.schemas.state import from_json, to_json


def _make_request(ref_code: str, request_type: str, channel: str = "online") -> dict:
    return {
        "ref_code": ref_code,
        "request_type": request_type,
        "channel": channel,
        "source_id": f"src-{ref_code}",
        "access_needs_summary": request_type,
    }


def _make_venue_resource(resource_type: str, zone: str, capacity: int, department: str = "Accessibility Coordination") -> dict:
    return {
        "zone": zone,
        "resource_type": resource_type,
        "capacity": capacity,
        "department": department,
        "version": "v1",
        "last_updated": "2026-07-01T00:00:00+00:00",
    }


def _base_state(**overrides) -> dict:
    state = {
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "correlation_id": "test-classify",
        "node_history": [],
        "error_log": [],
        "sanitized_requests_json": to_json([]),
        "venue_resources_json": to_json([]),
        "venue_resource_citations_json": to_json([]),
        "request_source_citations_json": to_json([]),
    }
    state.update(overrides)
    return state


class TestRequestClassificationNode:

    def setup_method(self):
        self.node = RequestClassificationNode()

    def test_wheelchair_gap_detected(self):
        """BL-07: Wheelchair gap (6 requested, 4 capacity) = gap 2."""
        requests = [_make_request(f"REF-{i:04X}", "wheelchair") for i in range(6)]
        resources = [_make_venue_resource("wheelchair_space", "Zone-A", 4)]
        state = _base_state(
            sanitized_requests_json=to_json(requests),
            venue_resources_json=to_json(resources),
        )
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        gaps = from_json(result["fulfillment_gaps_json"])
        wheelchair_gap = next((g for g in gaps if g["request_type"] == "wheelchair_space"), None)
        assert wheelchair_gap is not None
        assert wheelchair_gap["gap"] == 2
        assert wheelchair_gap["count_needed"] == 6
        assert wheelchair_gap["capacity_available"] == 4

    def test_within_capacity_no_gap(self):
        """BL-01: Hearing loop within capacity — no gap."""
        requests = [_make_request(f"REF-H{i}", "hearing_loop") for i in range(2)]
        resources = [_make_venue_resource("hearing_loop", "Zone-B", 5, "Stewards")]
        state = _base_state(
            sanitized_requests_json=to_json(requests),
            venue_resources_json=to_json(resources),
        )
        result = self.node(state)

        gaps = from_json(result["fulfillment_gaps_json"])
        assert not any(g["request_type"] == "hearing_loop" for g in gaps)

    def test_no_resource_configured(self):
        """BL-14: No venue resource for sensory_room — gap with capacity_available=0."""
        requests = [_make_request("REF-S1", "sensory"), _make_request("REF-S2", "sensory"), _make_request("REF-S3", "sensory")]
        state = _base_state(
            sanitized_requests_json=to_json(requests),
            venue_resources_json=to_json([]),
        )
        result = self.node(state)

        gaps = from_json(result["fulfillment_gaps_json"])
        sensory_gap = next((g for g in gaps if g["resource_type"] == "sensory_room"), None)
        assert sensory_gap is not None
        assert sensory_gap["capacity_available"] == 0
        assert sensory_gap["gap"] == 3

        dept_coord = from_json(result["department_coordination_json"])
        has_no_resource_notice = any(
            "NO RESOURCE CONFIGURED" in action
            for actions in dept_coord.values()
            for action in actions
        )
        assert has_no_resource_notice

    def test_multiple_request_types_classified(self):
        """BL-01: Multiple request types produce correct counts."""
        requests = (
            [_make_request(f"REF-W{i}", "wheelchair") for i in range(3)]
            + [_make_request(f"REF-C{i}", "companion_seat") for i in range(2)]
        )
        resources = [
            _make_venue_resource("wheelchair_space", "A1", 5),
            _make_venue_resource("companion_seat", "A2", 3),
        ]
        state = _base_state(
            sanitized_requests_json=to_json(requests),
            venue_resources_json=to_json(resources),
        )
        result = self.node(state)

        counts = from_json(result["request_type_counts_json"])
        assert counts.get("wheelchair_space") == 3
        assert counts.get("companion_seat") == 2

    def test_request_citations_included_in_mappings(self):
        """BL-09: Mappings include request_citations with ref_code."""
        requests = [_make_request("REF-A1B2", "wheelchair")]
        resources = [_make_venue_resource("wheelchair_space", "A1", 5)]
        state = _base_state(
            sanitized_requests_json=to_json(requests),
            venue_resources_json=to_json(resources),
        )
        result = self.node(state)

        mappings = from_json(result["resource_mappings_json"])
        wc_mapping = next((m for m in mappings if m["resource_type"] == "wheelchair_space"), None)
        assert wc_mapping is not None
        assert len(wc_mapping["request_citations"]) > 0
        assert wc_mapping["request_citations"][0]["ref_code"] == "REF-A1B2"

    def test_empty_requests_no_mappings(self):
        """Zero requests produce empty resource_mappings."""
        state = _base_state()
        result = self.node(state)
        assert from_json(result["resource_mappings_json"]) == []
        assert from_json(result["fulfillment_gaps_json"]) == []

    def test_department_coordination_non_empty(self):
        """BL-13: Department coordination produced for all mapped types."""
        requests = [_make_request("REF-001", "wheelchair")]
        resources = [_make_venue_resource("wheelchair_space", "A1", 2)]
        state = _base_state(
            sanitized_requests_json=to_json(requests),
            venue_resources_json=to_json(resources),
        )
        result = self.node(state)
        assert len(from_json(result["department_coordination_json"])) > 0

    def test_all_output_fields_are_json_strings(self):
        """All _json-suffixed output fields must be JSON strings."""
        state = _base_state()
        result = self.node(state)
        for key in ("request_type_counts_json", "resource_mappings_json", "fulfillment_gaps_json", "department_coordination_json"):
            assert isinstance(result[key], str), f"{key} must be a JSON string"

    def test_required_trust_level_is_anonymous(self):
        """Inner node must be ANONYMOUS."""
        assert RequestClassificationNode.required_trust_level == TrustLevel.ANONYMOUS
