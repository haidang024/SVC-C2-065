"""Unit tests for PostProcessNode — SVC-C2-065."""

from __future__ import annotations

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import from_json, to_json


def _base_state(**overrides) -> dict:
    state = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "test-post",
        "node_history": [],
        "error_log": [],
        "event_id": "EVT-001",
        "event_date": "2026-08-15",
        "event_start_time": "19:30:00",
        "event_name": "Summer Concert",
        "no_requests_flag": False,
        "total_request_count": 3,
        "operational_warnings_json": to_json([]),
        "action_items_json": to_json([
            {"department": "Accessibility Coordination", "action": "Confirm allocation", "priority": "LOW", "gap_details": ""},
        ]),
        "resource_mappings_json": to_json([
            {"request_type": "wheelchair_space", "zone": "A1", "count_needed": 3,
             "capacity_available": 5, "gap": 0, "department": "Accessibility Coordination",
             "request_citations": [], "venue_citations": []},
        ]),
        "fulfillment_gaps_json": to_json([]),
        "request_type_counts_json": to_json({"wheelchair_space": 3}),
        "source_coverage_json": to_json({"online": {"found": True, "record_count": 3}}),
        "request_source_citations_json": to_json([{
            "source": "request_history_source",
            "endpoint": "https://example.com",
            "event_id": "EVT-001",
            "record_count": 3,
            "retrieved_at": "2026-07-21T00:00:00+00:00",
        }]),
        "venue_resource_citations_json": to_json([{
            "source": "venue_resource_source",
            "endpoint": "https://venue.example.com",
            "version": "v1",
            "last_updated": "2026-07-01",
            "resource_count": 1,
        }]),
        "venue_resource_stale": False,
        "venue_resource_version": "v1",
        "staleness_warnings_json": to_json([]),
        "coverage_warnings_json": to_json([]),
        "review_flags_json": to_json([]),
    }
    state.update(overrides)
    return state


class TestPostProcessNode:

    def setup_method(self):
        self.node = PostProcessNode()

    def test_successful_handoff_generation(self):
        """BL-01: Valid state produces Markdown handoff."""
        state = _base_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        assert "formatted_output" in result
        output = result["formatted_output"]
        assert "Matchday Accessibility Operations Handoff" in output
        assert "EVT-001" in output
        assert "Summer Concert" in output

    def test_no_requests_notice_in_handoff(self):
        """BL-06: No-requests flag produces explicit notice (not empty output)."""
        state = _base_state(
            no_requests_flag=True,
            total_request_count=0,
            resource_mappings_json=to_json([]),
            request_type_counts_json=to_json({}),
            source_coverage_json=to_json({}),
        )
        result = self.node(state)

        output = result["formatted_output"]
        assert "No Accessibility Requests Found" in output
        assert "INTERNAL REVIEW DOCUMENT" in output

    def test_gap_alert_in_handoff(self):
        """BL-07: Fulfillment gap appears in handoff gap alert section."""
        gap = {
            "request_type": "wheelchair_space",
            "zone": "A1",
            "count_needed": 6,
            "capacity_available": 4,
            "gap": 2,
            "department": "Accessibility Coordination",
            "request_citations": [],
            "venue_citations": [],
        }
        state = _base_state(fulfillment_gaps_json=to_json([gap]))
        result = self.node(state)

        output = result["formatted_output"]
        assert "Fulfillment Gap Alerts" in output
        assert "wheelchair" in output.lower() or "Wheelchair" in output

    def test_s3_rejects_pii_pattern_in_output(self):
        """BL-11: S-3 gate blocks PII pattern in handoff output."""
        node = PostProcessNode()
        state = _base_state()
        state["review_flags_json"] = to_json([{"flag_type": "test", "description": "attendee_name: John Smith"}])
        result = node(state)
        assert result["status"] == AgentStatus.ERROR.value

    def test_formatted_output_key_present(self):
        """TC-09 / prompt checklist: formatted_output must appear in return dict."""
        state = _base_state()
        result = self.node(state)
        assert "formatted_output" in result
        assert result["formatted_output"] is not None
        assert len(result["formatted_output"]) > 0

    def test_handoff_output_matches_formatted_output(self):
        """handoff_output and formatted_output must be identical."""
        state = _base_state()
        result = self.node(state)
        assert result["handoff_output"] == result["formatted_output"]

    def test_citations_section_present(self):
        """Handoff includes data source citations."""
        state = _base_state()
        result = self.node(state)
        output = result["formatted_output"]
        assert "Data Sources and Citations" in output

    def test_internal_review_disclaimer_present(self):
        """Handoff includes internal review disclaimer."""
        state = _base_state()
        result = self.node(state)
        output = result["formatted_output"]
        assert "INTERNAL REVIEW DOCUMENT" in output or "not for distribution" in output.lower()

    def test_required_trust_level_is_verified_external(self):
        """Outer PostProcessNode must be VERIFIED_EXTERNAL."""
        assert PostProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_coverage_warning_section_present_when_warnings(self):
        """BL-04: Coverage warnings appear in Data Quality Notices section."""
        state = _base_state(coverage_warnings_json=to_json(["No records for 'phone'"]))
        result = self.node(state)
        output = result["formatted_output"]
        assert "Data Quality Notices" in output
        assert "phone" in output
