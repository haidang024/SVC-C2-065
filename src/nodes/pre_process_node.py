"""PreProcessNode — Validate event details, approved sources, channels, and configuration for SVC-C2-065."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

_GREETING_ONLY = re.compile(r"^(?:hello|hi|hey|hello[,. ]*hi|xin chào|chào|こんにちは)[!. ,]*$", re.IGNORECASE)
_INPUT_GUIDANCE = [
    "Send JSON with event_id, event_date, event_start_time, request_history_source, and venue_resource_source.",
    "Each source object must contain type, endpoint, and secret_key reference fields.",
    "Optionally provide expected_booking_channels, staleness_threshold_days, and pii_fields.",
]


def _input_error(message: str) -> dict[str, Any]:
    return {
        "status": AgentStatus.SUCCESS.value,
        "validated_input": "",
        "input_error_message": message,
        "input_error_guidance": "\n".join(_INPUT_GUIDANCE),
    }


class PreProcessNode(FunctionNode):
    """Validate event details, approved request-history and venue-resource sources,
    expected booking channels, resource freshness threshold, and declared sensitive fields.

    Refuses pipeline execution if required configuration is missing or inaccessible.
    Creates a normalized retrieval scope for downstream nodes. Does not access attendee
    data or perform any write-back to source systems.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    _REQUIRED_SOURCE_FIELDS = ("type", "endpoint", "secret_key")
    _MAX_INPUT_LENGTH = 8192

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2: Reject oversized inputs and injection patterns; block credential fields."""
        user_input = state.get("user_input", "")
        raw = json.dumps(user_input, ensure_ascii=False) if isinstance(user_input, dict) else str(user_input)

        if len(raw) > self._MAX_INPUT_LENGTH:
            raise SecurityViolationError(f"Input exceeds max length of {self._MAX_INPUT_LENGTH} characters")

        lowered = raw.lower()
        for blocked in ["<script", "javascript:", "../", "..\\"]:
            if blocked in lowered:
                raise SecurityViolationError(f"Injection pattern detected: {blocked!r}")

        # Reject credentials in state payload
        for cred_key in ("api_key", "token", "password", "jwt"):
            if cred_key in state:
                raise SecurityViolationError("Credentials must not be passed in state payload")

        return state

    def _get_source(
        self,
        state: dict[str, Any],
        key_json: str,
        key_raw: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve a source config from _json-encoded state or raw dict fallback."""
        raw = state.get(key_json) or state.get(key_raw) or payload.get(key_raw)
        if raw is None:
            return None
        if isinstance(raw, str):
            parsed = from_json(raw)
            return parsed if isinstance(parsed, dict) else None
        return raw if isinstance(raw, dict) else None

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Validate all configuration and produce retrieval_scope for downstream nodes."""
        user_input = state.get("user_input", "")
        if isinstance(user_input, str) and user_input.strip():
            try:
                payload = json.loads(user_input)
                if not isinstance(payload, dict):
                    payload = {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
        elif isinstance(user_input, dict):
            payload = user_input
        else:
            payload = {}

        # Resolve event fields — prefer direct state, fall back to user_input payload
        event_id = state.get("event_id") or payload.get("event_id", "")
        event_date = state.get("event_date") or payload.get("event_date", "")
        event_start_time = state.get("event_start_time") or payload.get("event_start_time", "")
        event_name = state.get("event_name") or payload.get("event_name", "")

        errors: list[str] = []

        if not str(event_id).strip():
            errors.append("event_id is required")
        if not str(event_date).strip():
            errors.append("event_date is required")
        if not str(event_start_time).strip():
            errors.append("event_start_time is required")

        # Validate request_history_source
        rh_source = self._get_source(state, "request_history_source_json", "request_history_source", payload)
        if not rh_source:
            errors.append("request_history_source configuration is required")
        elif not isinstance(rh_source, dict):
            errors.append("request_history_source must be a dict")
        else:
            missing_rh = [f for f in self._REQUIRED_SOURCE_FIELDS if not rh_source.get(f)]
            if missing_rh:
                errors.append(f"request_history_source missing fields: {missing_rh}")

        # Validate venue_resource_source
        vr_source = self._get_source(state, "venue_resource_source_json", "venue_resource_source", payload)
        if not vr_source:
            errors.append("venue_resource_source configuration is required")
        elif not isinstance(vr_source, dict):
            errors.append("venue_resource_source must be a dict")
        else:
            missing_vr = [f for f in self._REQUIRED_SOURCE_FIELDS if not vr_source.get(f)]
            if missing_vr:
                errors.append(f"venue_resource_source missing fields: {missing_vr}")

        # Validate booking channels
        raw_channels = (
            state.get("expected_booking_channels_json")
            or state.get("expected_booking_channels")
            or payload.get("expected_booking_channels")
        )
        if isinstance(raw_channels, str):
            expected_channels = from_json(raw_channels) or ["online", "phone", "in_person", "third_party"]
        elif isinstance(raw_channels, list):
            expected_channels = raw_channels
        else:
            expected_channels = ["online", "phone", "in_person", "third_party"]

        if not isinstance(expected_channels, list) or len(expected_channels) == 0:
            errors.append("expected_booking_channels must be a non-empty list")

        # Validate staleness threshold
        raw_staleness = state.get("staleness_threshold_days") or payload.get("staleness_threshold_days") or 30
        try:
            staleness_days = int(raw_staleness)
            if staleness_days < 1:
                errors.append("staleness_threshold_days must be >= 1")
        except (TypeError, ValueError):
            errors.append("staleness_threshold_days must be an integer")
            staleness_days = 30

        # Validate pii_fields
        raw_pii = state.get("pii_fields_json") or state.get("pii_fields") or payload.get("pii_fields")
        if isinstance(raw_pii, str):
            pii_fields = from_json(raw_pii) or [
                "attendee_name",
                "attendee_email",
                "attendee_phone",
                "disability_description",
            ]
        elif isinstance(raw_pii, list):
            pii_fields = raw_pii
        else:
            pii_fields = ["attendee_name", "attendee_email", "attendee_phone", "disability_description"]

        if not isinstance(pii_fields, list):
            errors.append("pii_fields must be a list")
            pii_fields = ["attendee_name", "attendee_email", "attendee_phone", "disability_description"]

        if errors:
            emit_trace_event(
                "PreProcessNode_validation_failed",
                {"errors": errors, "event_id": str(event_id)},
                state,
            )
            raw_text = user_input.strip() if isinstance(user_input, str) else ""
            if _GREETING_ONLY.fullmatch(raw_text):
                return _input_error("The message contains only a greeting and no accessibility-history request.")
            return _input_error(f"Configuration validation failed: {'; '.join(errors)}")

        assert rh_source is not None
        assert vr_source is not None

        # Verify source credentials are accessible
        # STG_MOCK_MODE: skip live secret check — external connectors not wired in STG smoke runs.
        import os as _os

        if not _os.environ.get("STG_MOCK_MODE"):
            ctx = InvocationContext.from_state(state)
            for source_cfg, source_name in [
                (rh_source, "request_history_source"),
                (vr_source, "venue_resource_source"),
            ]:
                secret_key = source_cfg.get("secret_key", "")
                if secret_key:
                    try:
                        ctx.secrets.require(secret_key)
                    except Exception as exc:  # noqa: BLE001
                        emit_trace_event(
                            "PreProcessNode_secret_unavailable",
                            {"source": source_name, "secret_key": secret_key},
                            state,
                        )
                        # Harness H0/H6: an unprovisioned secret is fixable by an
                        # operator, so the reason must reach them. The Marketplace
                        # runner drops `output` for any status != success, so
                        # returning ERROR here shows a bare RuntimeError instead.
                        # H3: report the key name and source only — never the raw
                        # exception text, which carries provider/namespace internals.
                        del exc
                        return _input_error(
                            f"The credential required to read {source_name} "
                            f"('{secret_key}') is not configured in this environment. "
                            "Ask an administrator to provision it, then retry."
                        )

        event_id_str = str(event_id).strip()
        event_date_str = str(event_date).strip()

        retrieval_scope = {
            "event_id": event_id_str,
            "event_date": event_date_str,
            "event_start_time": str(event_start_time).strip(),
            "event_name": str(event_name).strip() if event_name else "",
            "expected_channels": expected_channels,
            "staleness_threshold_days": staleness_days,
            "pii_fields": pii_fields,
        }

        emit_trace_event(
            "PreProcessNode_execute_complete",
            {
                "event_id": event_id_str,
                "event_date": event_date_str,
                "channel_count": len(expected_channels),
                "staleness_threshold_days": staleness_days,
                "pii_field_count": len(pii_fields),
            },
            state,
        )

        return {
            "event_id": event_id_str,
            "event_date": event_date_str,
            "event_start_time": retrieval_scope["event_start_time"],
            "event_name": retrieval_scope["event_name"],
            "request_history_source_json": to_json(rh_source),
            "venue_resource_source_json": to_json(vr_source),
            "expected_booking_channels_json": to_json(expected_channels),
            "pii_fields_json": to_json(pii_fields),
            "staleness_threshold_days": staleness_days,
            "retrieval_scope_json": to_json(retrieval_scope),
            "validated_input": json.dumps(
                {"event_id": event_id_str, "event_date": event_date_str},
                ensure_ascii=False,
            ),
            "status": AgentStatus.SUCCESS.value,
            "error_message": None,
        }
