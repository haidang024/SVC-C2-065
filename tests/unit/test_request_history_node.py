"""Unit tests for RequestHistoryNode — SVC-C2-065."""

from __future__ import annotations

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.request_history_node import RequestHistoryNode, _anonymize_record, _make_ref_code
from src.schemas.state import from_json, to_json


def _base_state(**overrides) -> dict:
    state = {
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "correlation_id": "test-rh",
        "node_history": [],
        "error_log": [],
        "event_id": "EVT-001",
        "event_date": "2026-08-15",
        "retrieval_scope_json": to_json({
            "event_id": "EVT-001",
            "event_date": "2026-08-15",
            "expected_channels": ["online", "phone"],
            "pii_fields": ["attendee_name", "attendee_email", "disability_description"],
            "staleness_threshold_days": 30,
        }),
        "request_history_source_json": to_json({
            "type": "rest_api",
            "endpoint": "https://ticketing.example.com",
            "secret_key": "SVC_C2_065_REQUEST_HISTORY_API_KEY",
        }),
        "expected_booking_channels_json": to_json(["online", "phone"]),
        "pii_fields_json": to_json(["attendee_name", "attendee_email", "disability_description"]),
    }
    state.update(overrides)
    return state


class TestAnonymization:
    """BL-10: PII anonymization logic."""

    def test_pii_fields_removed(self):
        """BL-10: Raw PII fields are removed from anonymized record."""
        raw = {
            "id": "src-001",
            "channel": "online",
            "request_type": "wheelchair",
            "attendee_name": "John Smith",
            "attendee_email": "john@example.com",
            "disability_description": "uses manual wheelchair",
            "booking_date": "2026-07-01",
        }
        pii_fields = ["attendee_name", "attendee_email", "disability_description"]
        result = _anonymize_record(raw, pii_fields)

        assert "attendee_name" not in result
        assert "attendee_email" not in result
        assert "disability_description" not in result
        assert result["channel"] == "online"
        assert result["request_type"] == "wheelchair"

    def test_access_needs_summary_uses_category_only(self):
        """BL-10: access_needs_summary contains category, not disability description."""
        raw = {
            "id": "src-002",
            "request_type": "hearing_loop",
            "access_needs_category": "hearing",
            "disability_description": "severe hearing impairment since birth",
        }
        result = _anonymize_record(raw, ["disability_description"])
        assert "severe hearing impairment" not in result.get("access_needs_summary", "")
        assert result["access_needs_summary"] in ("hearing", "hearing_loop")

    def test_ref_codes_are_strings(self):
        """BL-09: Reference codes are non-empty strings."""
        code = _make_ref_code("source-record-001")
        assert isinstance(code, str)
        assert len(code) > 0
        assert code.startswith("REF-")


class TestRequestHistoryNode:
    """BL-01, BL-04, BL-06, BL-08, BL-09, BL-12: RequestHistoryNode integration."""

    def setup_method(self):
        self.node = RequestHistoryNode()

    def _mock_secrets(self, monkeypatch):
        import src.nodes.request_history_node as rhn_module

        class MockCtx:
            class secrets:
                @staticmethod
                def require(key):
                    return f"mock-{key}"

            @classmethod
            def from_state(cls, state):
                return cls()

        monkeypatch.setattr(rhn_module, "InvocationContext", MockCtx)

    def test_successful_retrieval_with_mock(self, monkeypatch):
        """BL-01: Successful retrieval with mock source."""
        self._mock_secrets(monkeypatch)

        mock_records = [
            {"id": "r1", "channel": "online", "request_type": "wheelchair", "booking_date": "2026-07-01"},
            {"id": "r2", "channel": "phone", "request_type": "hearing_loop", "booking_date": "2026-07-02"},
        ]

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: (mock_records, []))

        state = _base_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        assert result["total_request_count"] == 2
        assert result["no_requests_flag"] is False
        sanitized = from_json(result["sanitized_requests_json"])
        assert len(sanitized) == 2

    def test_sanitized_requests_have_no_pii(self, monkeypatch):
        """BL-10: Sanitized requests contain no raw PII."""
        self._mock_secrets(monkeypatch)

        mock_records = [
            {
                "id": "r1",
                "channel": "online",
                "request_type": "wheelchair",
                "attendee_name": "Jane Doe",
                "attendee_email": "jane@example.com",
                "disability_description": "mobility impairment",
            }
        ]

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: (mock_records, []))

        state = _base_state()
        result = self.node(state)

        for req in from_json(result["sanitized_requests_json"]):
            assert "attendee_name" not in req
            assert "attendee_email" not in req
            assert "disability_description" not in req

    def test_zero_requests_sets_no_requests_flag(self, monkeypatch):
        """BL-06: Zero requests sets no_requests_flag=True."""
        self._mock_secrets(monkeypatch)

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: ([], []))

        state = _base_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        assert result["no_requests_flag"] is True
        assert result["total_request_count"] == 0

    def test_missing_channel_generates_coverage_warning(self, monkeypatch):
        """BL-04: Missing expected channel produces coverage warning."""
        self._mock_secrets(monkeypatch)

        mock_records = [{"id": "r1", "channel": "online", "request_type": "wheelchair"}]

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: (mock_records, []))

        state = _base_state()
        result = self.node(state)

        warnings = from_json(result.get("coverage_warnings_json")) or []
        assert any("phone" in w for w in warnings)

    def test_fetch_error_produces_coverage_warning(self, monkeypatch):
        """BL-08: Source fetch error produces coverage_warning."""
        self._mock_secrets(monkeypatch)

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: ([], ["Connection error: URLError"]))

        state = _base_state()
        result = self.node(state)

        assert result["no_requests_flag"] is True
        warnings = from_json(result["coverage_warnings_json"]) or []
        assert any("Connection error" in w or "error" in w.lower() for w in warnings)

    def test_ref_codes_present_in_sanitized_requests(self, monkeypatch):
        """BL-09: Sanitized requests have ref_code for citations."""
        self._mock_secrets(monkeypatch)

        mock_records = [{"id": "r-abc", "channel": "online", "request_type": "companion_seat"}]

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: (mock_records, []))

        state = _base_state()
        result = self.node(state)

        sanitized = from_json(result["sanitized_requests_json"])
        assert sanitized[0]["ref_code"].startswith("REF-")

    def test_output_fields_are_json_strings(self, monkeypatch):
        """All _json-suffixed output fields must be JSON strings."""
        self._mock_secrets(monkeypatch)

        import src.nodes.request_history_node as rhn_module
        monkeypatch.setattr(rhn_module, "_fetch_records", lambda **kwargs: ([], []))

        state = _base_state()
        result = self.node(state)

        for key in ("sanitized_requests_json", "source_coverage_json", "coverage_warnings_json", "request_source_citations_json"):
            assert isinstance(result[key], str), f"{key} must be a JSON string"

    def test_missing_event_id_returns_error(self, monkeypatch):
        """Missing event_id returns ERROR status."""
        self._mock_secrets(monkeypatch)
        state = _base_state(event_id="")
        state["retrieval_scope_json"] = to_json({})
        result = self.node(state)
        assert result["status"] == AgentStatus.ERROR.value

    def test_required_trust_level_is_anonymous(self):
        """Inner node must be ANONYMOUS (Cat 2 trust-trap prevention)."""
        assert RequestHistoryNode.required_trust_level == TrustLevel.ANONYMOUS
