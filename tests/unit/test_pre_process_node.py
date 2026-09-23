"""Unit tests for PreProcessNode — SVC-C2-065."""

from __future__ import annotations

import json

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import from_json, to_json


def _valid_state(**overrides) -> dict:
    """Build a minimal valid state for PreProcessNode."""
    state = {
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "correlation_id": "test-pre-process",
        "node_history": [],
        "error_log": [],
        "event_id": "EVT-001",
        "event_date": "2026-08-15",
        "event_start_time": "19:30:00",
        "event_name": "Summer Concert",
        "request_history_source_json": to_json({
            "type": "rest_api",
            "endpoint": "https://ticketing.example.com/api",
            "secret_key": "SVC_C2_065_REQUEST_HISTORY_API_KEY",
        }),
        "venue_resource_source_json": to_json({
            "type": "rest_api",
            "endpoint": "https://venue.example.com/api",
            "secret_key": "SVC_C2_065_VENUE_RESOURCE_API_KEY",
        }),
        "expected_booking_channels_json": to_json(["online", "phone"]),
        "pii_fields_json": to_json(["attendee_name", "attendee_email", "disability_description"]),
        "staleness_threshold_days": 30,
    }
    state.update(overrides)
    return state


class TestPreProcessNodeValidation:
    """TC-02, BL-02, BL-03: Validation and error paths."""

    def setup_method(self):
        self.node = PreProcessNode()

    def test_missing_event_id_returns_error(self):
        """BL-02: Missing event_id stops pipeline with structured error."""
        state = _valid_state(event_id="")
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "event_id" in result["input_error_message"]

    def test_missing_request_history_source_returns_error(self):
        """BL-03: Missing request_history_source stops pipeline at PreProcessNode."""
        state = _valid_state()
        state.pop("request_history_source_json")
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "request_history_source" in result["input_error_message"]

    def test_missing_venue_resource_source_returns_error(self):
        state = _valid_state()
        state.pop("venue_resource_source_json")
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "venue_resource_source" in result["input_error_message"]

    def test_missing_event_date_returns_error(self):
        state = _valid_state(event_date="")
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "event_date" in result["input_error_message"]

    def test_source_missing_endpoint_returns_error(self):
        state = _valid_state()
        state["request_history_source_json"] = to_json({"type": "rest_api", "secret_key": "KEY"})
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "endpoint" in result["input_error_message"]

    def test_injection_pattern_raises_security_violation(self):
        """TC-02: Injection pattern rejected by _extra_security_gate_input."""
        state = _valid_state(user_input="<script>alert(1)</script>")
        with pytest.raises(SecurityViolationError):
            self.node._extra_security_gate_input(state)

    def test_oversized_input_raises_security_violation(self):
        """TC-02: Oversized input rejected by _extra_security_gate_input."""
        state = _valid_state(user_input="x" * 9000)
        with pytest.raises(SecurityViolationError):
            self.node._extra_security_gate_input(state)

    def test_credential_field_in_state_raises_security_violation(self):
        """TC-02: Credential field in state raises SecurityViolationError."""
        state = _valid_state()
        state["api_key"] = "secret-value"
        with pytest.raises(SecurityViolationError):
            self.node._extra_security_gate_input(state)


class TestPreProcessNodeSuccess:
    """BL-01: Valid configuration produces retrieval_scope_json."""

    def setup_method(self):
        self.node = PreProcessNode()

    def test_valid_config_produces_retrieval_scope(self, monkeypatch):
        """Valid input produces normalized retrieval scope and SUCCESS status."""
        import src.nodes.pre_process_node as ppn_module

        class MockSecrets:
            def require(self, key):
                return f"mock-value-for-{key}"

        class MockCtx:
            secrets = MockSecrets()

            @classmethod
            def from_state(cls, state):
                return cls()

        monkeypatch.setattr(ppn_module, "InvocationContext", MockCtx)

        state = _valid_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        assert result["event_id"] == "EVT-001"
        assert result["event_date"] == "2026-08-15"
        assert "retrieval_scope_json" in result
        scope = from_json(result["retrieval_scope_json"])
        assert scope["event_id"] == "EVT-001"
        assert scope["expected_channels"] == ["online", "phone"]
        assert result["validated_input"] is not None

    def test_output_fields_are_json_strings(self, monkeypatch):
        """All _json-suffixed output fields must be JSON strings."""
        import src.nodes.pre_process_node as ppn_module

        class MockCtx:
            class secrets:
                @staticmethod
                def require(key):
                    return "mock"

            @classmethod
            def from_state(cls, state):
                return cls()

        monkeypatch.setattr(ppn_module, "InvocationContext", MockCtx)

        state = _valid_state()
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        for key in (
            "request_history_source_json",
            "venue_resource_source_json",
            "expected_booking_channels_json",
            "pii_fields_json",
            "retrieval_scope_json",
        ):
            assert isinstance(result[key], str), f"{key} must be a JSON string"

    def test_default_pii_fields_used_when_not_provided(self, monkeypatch):
        """PreProcessNode uses default pii_fields when not configured."""
        import src.nodes.pre_process_node as ppn_module

        class MockCtx:
            class secrets:
                @staticmethod
                def require(key):
                    return "mock"

            @classmethod
            def from_state(cls, state):
                return cls()

        monkeypatch.setattr(ppn_module, "InvocationContext", MockCtx)

        state = _valid_state()
        state.pop("pii_fields_json")
        result = self.node(state)

        assert result["status"] == AgentStatus.SUCCESS
        pii = from_json(result["pii_fields_json"])
        assert "attendee_name" in pii

    def test_user_input_json_payload_parsed(self, monkeypatch):
        """PreProcessNode parses JSON from user_input field."""
        import src.nodes.pre_process_node as ppn_module

        class MockCtx:
            class secrets:
                @staticmethod
                def require(key):
                    return "mock"

            @classmethod
            def from_state(cls, state):
                return cls()

        monkeypatch.setattr(ppn_module, "InvocationContext", MockCtx)

        payload = {
            "event_id": "EVT-JSON",
            "event_date": "2026-09-01",
            "event_start_time": "14:00:00",
            "request_history_source": {
                "type": "rest_api",
                "endpoint": "https://example.com",
                "secret_key": "KEY_RH",
            },
            "venue_resource_source": {
                "type": "rest_api",
                "endpoint": "https://venue.example.com",
                "secret_key": "KEY_VR",
            },
        }
        state = {
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "correlation_id": "test",
            "node_history": [],
            "error_log": [],
            "user_input": json.dumps(payload),
        }
        result = self.node(state)
        assert result["status"] == AgentStatus.SUCCESS
        assert result["event_id"] == "EVT-JSON"


class TestPreProcessNodeContract:
    """Node contract: execute() method contract."""

    def test_execute_method_signature(self):
        """execute(self, state) — correct parameter signature."""
        import inspect

        sig = inspect.signature(PreProcessNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2
        assert params[1] == "state"

    def test_required_trust_level_is_verified_external(self):
        """TC-08: PreProcessNode requires VERIFIED_EXTERNAL trust."""
        assert PreProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_s1_rejects_anonymous_caller(self):
        """TC-08: ANONYMOUS caller is rejected by PreProcessNode S-1 gate."""
        node = PreProcessNode()
        state = _valid_state(caller_trust_level=TrustLevel.ANONYMOUS.value)
        result = node(state)
        assert result["status"] == AgentStatus.ERROR.value

    def test_no_execute_direct_call_needed(self):
        """All test invocations must use node(state), not node.execute(state)."""
        assert hasattr(PreProcessNode, "__call__")
