"""Unit tests for VenueResourceNode — SVC-C2-065."""

from __future__ import annotations

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.venue_resource_node import VenueResourceNode
from src.schemas.state import from_json, to_json


def _base_state(**overrides) -> dict:
    state = {
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "correlation_id": "test-vr",
        "node_history": [],
        "error_log": [],
        "event_id": "EVT-001",
        "retrieval_scope_json": to_json({
            "event_id": "EVT-001",
            "staleness_threshold_days": 30,
        }),
        "venue_resource_source_json": to_json({
            "type": "rest_api",
            "endpoint": "https://venue.example.com",
            "secret_key": "SVC_C2_065_VENUE_RESOURCE_API_KEY",
        }),
        "staleness_threshold_days": 30,
    }
    state.update(overrides)
    return state


def _mock_ctx(monkeypatch):
    import src.nodes.venue_resource_node as vrn_module

    class MockCtx:
        class secrets:
            @staticmethod
            def require(key):
                return f"mock-{key}"

        @classmethod
        def from_state(cls, state):
            return cls()

    monkeypatch.setattr(vrn_module, "InvocationContext", MockCtx)


class TestVenueResourceNode:

    def setup_method(self):
        self.node = VenueResourceNode()

    def test_successful_resource_retrieval(self, monkeypatch):
        """BL-01: Venue resources retrieved and normalized."""
        _mock_ctx(monkeypatch)

        from datetime import datetime, timezone
        recent = datetime.now(tz=timezone.utc).isoformat()
        mock_resources = [
            {"zone": "A1", "resource_type": "wheelchair_space", "capacity": 4, "department": "Accessibility Coordination", "version": "v2", "last_updated": recent},
            {"zone": "B1", "resource_type": "hearing_loop", "capacity": 5, "department": "Stewards", "version": "v2", "last_updated": recent},
        ]
        meta = {"version": "v2", "last_updated": recent}

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: (mock_resources, [], meta))

        state = _base_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        resources = from_json(result["venue_resources_json"])
        assert len(resources) == 2
        assert result["venue_resource_stale"] is False

    def test_stale_resource_sets_stale_flag(self, monkeypatch):
        """BL-05: Resources older than threshold sets venue_resource_stale=True."""
        _mock_ctx(monkeypatch)

        from datetime import datetime, timedelta, timezone
        old_date = (datetime.now(tz=timezone.utc) - timedelta(days=45)).isoformat()
        mock_resources = [
            {"zone": "A1", "resource_type": "wheelchair_space", "capacity": 4,
             "department": "Accessibility Coordination", "version": "v1", "last_updated": old_date}
        ]
        meta = {"version": "v1", "last_updated": old_date}

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: (mock_resources, [], meta))

        state = _base_state()
        result = self.node(state)

        assert result["venue_resource_stale"] is True
        warnings = from_json(result["staleness_warnings_json"]) or []
        assert len(warnings) > 0

    def test_empty_resources_produces_warning(self, monkeypatch):
        """Empty venue resources produces a staleness/availability warning."""
        _mock_ctx(monkeypatch)

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: ([], [], {}))

        state = _base_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        assert from_json(result["venue_resources_json"]) == []
        warnings = from_json(result["staleness_warnings_json"]) or []
        assert len(warnings) > 0

    def test_malformed_resource_excluded(self, monkeypatch):
        """Malformed records (missing required fields) are excluded with warning."""
        _mock_ctx(monkeypatch)

        from datetime import datetime, timezone
        recent = datetime.now(tz=timezone.utc).isoformat()
        mock_resources = [
            {"zone": "A1", "resource_type": "wheelchair_space", "capacity": 4,
             "department": "Accessibility Coordination", "version": "v1", "last_updated": recent},
            {"zone": "B2"},  # malformed — missing resource_type and capacity
        ]
        meta = {"version": "v1", "last_updated": recent}

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: (mock_resources, [], meta))

        state = _base_state()
        result = self.node(state)

        assert len(from_json(result["venue_resources_json"])) == 1
        warnings = from_json(result["staleness_warnings_json"]) or []
        assert any("malformed" in w for w in warnings)

    def test_fetch_error_produces_warning(self, monkeypatch):
        """BL-08: Fetch error produces staleness warning."""
        _mock_ctx(monkeypatch)

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: ([], ["HTTP 500"], {}))

        state = _base_state()
        result = self.node(state)

        warnings = from_json(result["staleness_warnings_json"]) or []
        assert any("HTTP 500" in w or "error" in w.lower() for w in warnings)

    def test_citations_present(self, monkeypatch):
        """Venue resource citations are included in result."""
        _mock_ctx(monkeypatch)

        from datetime import datetime, timezone
        recent = datetime.now(tz=timezone.utc).isoformat()
        mock_resources = [
            {"zone": "A1", "resource_type": "wheelchair_space", "capacity": 4,
             "department": "Accessibility Coordination", "version": "v1", "last_updated": recent}
        ]
        meta = {"version": "v1", "last_updated": recent}

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: (mock_resources, [], meta))

        state = _base_state()
        result = self.node(state)

        citations = from_json(result["venue_resource_citations_json"]) or []
        assert len(citations) > 0
        assert citations[0]["source"] == "venue_resource_source"

    def test_output_fields_are_json_strings(self, monkeypatch):
        """All _json-suffixed output fields must be JSON strings."""
        _mock_ctx(monkeypatch)

        import src.nodes.venue_resource_node as vrn_module
        monkeypatch.setattr(vrn_module, "_fetch_venue_resources", lambda **kwargs: ([], [], {}))

        state = _base_state()
        result = self.node(state)

        for key in ("venue_resources_json", "staleness_warnings_json", "venue_resource_citations_json"):
            assert isinstance(result[key], str), f"{key} must be a JSON string"

    def test_required_trust_level_is_anonymous(self):
        """Inner node must be ANONYMOUS (Cat 2 trust-trap prevention)."""
        assert VenueResourceNode.required_trust_level == TrustLevel.ANONYMOUS
