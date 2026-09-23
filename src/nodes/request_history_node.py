"""RequestHistoryNode — Read-only retrieval of event-scoped accessibility request records with PII anonymization."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json


# Per-run salt for deterministic but non-persistent reference codes
_RUN_SALT = uuid.uuid4().hex


def _make_ref_code(source_id: str) -> str:
    """Generate a stable per-run reference code for a source record.

    Codes are deterministic within a single run but change across runs,
    preventing cross-run reference-code linkage (privacy requirement).
    """
    digest = hashlib.sha256(f"{_RUN_SALT}:{source_id}".encode()).hexdigest()[:12]
    return f"REF-{digest.upper()}"


def _anonymize_record(record: dict[str, Any], pii_fields: list[str]) -> dict[str, Any]:
    """Replace PII fields with anonymized placeholders.

    Removes all declared PII fields and the disability description before
    any downstream (LLM or logging) processing. Returns only safe fields.
    """
    safe: dict[str, Any] = {}
    # Always include structural fields if present
    for key in ("id", "source_id", "channel", "booking_date", "request_type", "access_needs_category"):
        if key in record:
            safe[key] = record[key]

    # Build a safe access_needs_summary from category only — never raw description
    raw_category = record.get("access_needs_category") or record.get("request_type", "unspecified")
    safe["access_needs_summary"] = str(raw_category)

    # Explicitly drop all declared PII fields
    for pii_field in pii_fields:
        if pii_field in safe:
            del safe[pii_field]

    return safe


class RequestHistoryNode(FunctionNode):
    """Retrieve event-scoped accessibility request records from configured sources.

    Normalizes source metadata, calculates deterministic counts, creates stable
    per-run reference codes, and anonymizes all attendee PII and disability-description
    fields before returning data to the pipeline.

    Read-only — never writes to source systems. Never persists reference mappings across runs.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    @staticmethod
    def _resolve(state: dict[str, Any], key: str, default: str = "") -> str:
        """Read key from top-level state; fall back to state['input_context'][key]."""
        v = state.get(key, "")
        if not v:
            v = state.get("input_context", {}).get(key, default)
        return str(v)

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2: Enforce anonymization of declared PII fields before any LLM exposure."""
        pii_fields = from_json(state.get("pii_fields_json")) or state.get("pii_fields") or []
        # Verify no raw PII field names leak into validated_input
        validated_input = state.get("validated_input", "")
        for pii_field in pii_fields:
            if pii_field in str(validated_input):
                raise SecurityViolationError(
                    f"PII field name '{pii_field}' detected in validated_input — anonymization required"
                )
        return state

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Retrieve and anonymize accessibility request records for the event."""
        retrieval_scope = from_json(self._resolve(state, "retrieval_scope_json")) or state.get("retrieval_scope") or {}
        event_id = retrieval_scope.get("event_id") or self._resolve(state, "event_id")
        event_date = retrieval_scope.get("event_date") or self._resolve(state, "event_date")
        expected_channels = (
            retrieval_scope.get("expected_channels")
            or from_json(self._resolve(state, "expected_booking_channels_json"))
            or state.get("expected_booking_channels")
            or []
        )
        pii_fields = (
            retrieval_scope.get("pii_fields")
            or from_json(self._resolve(state, "pii_fields_json"))
            or state.get("pii_fields")
            or [
                "attendee_name",
                "attendee_email",
                "attendee_phone",
                "disability_description",
            ]
        )

        rh_source = (
            from_json(self._resolve(state, "request_history_source_json")) or state.get("request_history_source") or {}
        )
        source_endpoint = rh_source.get("endpoint", "")
        source_secret_key = rh_source.get("secret_key", "")

        if not event_id:
            emit_trace_event(
                "RequestHistoryNode_missing_event_id",
                {"event_id": event_id},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_message": "event_id is required for request history retrieval",
                "sanitized_requests_json": to_json([]),
                "total_request_count": 0,
                "no_requests_flag": True,
                "source_coverage_json": to_json({}),
                "coverage_warnings_json": to_json(["No event_id provided — retrieval skipped"]),
                "request_source_citations_json": to_json([]),
            }

        # Retrieve credentials for source access
        # STG_MOCK_MODE: skip live secret check — external connectors not wired in STG smoke runs.
        import os as _os

        if _os.environ.get("STG_MOCK_MODE"):
            api_key = "stg-mock-key" if source_secret_key else None
        else:
            ctx = InvocationContext.from_state(state)
            api_key = ctx.secrets.require(source_secret_key) if source_secret_key else None

        # Fetch raw records from the configured source
        raw_records, fetch_errors = _fetch_records(
            endpoint=source_endpoint,
            event_id=event_id,
            event_date=event_date,
            api_key=api_key,
        )

        # Build source coverage map
        source_coverage: dict[str, dict[str, Any]] = {}
        for ch in expected_channels:
            ch_records = [r for r in raw_records if r.get("channel") == ch]
            source_coverage[ch] = {"found": len(ch_records) > 0, "record_count": len(ch_records)}

        # Coverage warnings for missing channels
        coverage_warnings: list[str] = []
        for ch, info in source_coverage.items():
            if not info["found"]:
                coverage_warnings.append(
                    f"No records found for expected booking channel: '{ch}' — "
                    "data may be missing or channel not yet reported"
                )
        for err in fetch_errors:
            coverage_warnings.append(f"Source fetch error: {err}")

        # Anonymize and assign per-run reference codes
        sanitized_requests: list[dict[str, Any]] = []
        for raw in raw_records:
            source_id = str(raw.get("id") or raw.get("source_id") or id(raw))
            ref_code = _make_ref_code(source_id)
            safe = _anonymize_record(raw, pii_fields)
            safe["ref_code"] = ref_code
            safe["source_id"] = source_id
            sanitized_requests.append(safe)

        total = len(sanitized_requests)
        no_requests = total == 0

        citations = [
            {
                "source": "request_history_source",
                "endpoint": source_endpoint,
                "event_id": event_id,
                "record_count": total,
                "retrieved_at": _utc_now(),
            }
        ]

        emit_trace_event(
            "RequestHistoryNode_execute_complete",
            {
                "event_id": event_id,
                "total_request_count": total,
                "no_requests_flag": no_requests,
                "covered_channels": sum(1 for v in source_coverage.values() if v["found"]),
                "missing_channels": len(coverage_warnings),
            },
            state,
        )

        return {
            "sanitized_requests_json": to_json(sanitized_requests),
            "total_request_count": total,
            "no_requests_flag": no_requests,
            "source_coverage_json": to_json(source_coverage),
            "coverage_warnings_json": to_json(coverage_warnings),
            "request_source_citations_json": to_json(citations),
            "status": AgentStatus.SUCCESS.value,
        }


def _fetch_records(
    endpoint: str,
    event_id: str,
    event_date: str,
    api_key: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch raw records from the request-history source.

    Returns (records, errors). Records may contain raw PII — callers must
    anonymize before downstream use. This function does not modify source data.
    """
    import urllib.error
    import urllib.request

    if not endpoint:
        return [], ["No endpoint configured for request_history_source"]

    try:
        url = f"{endpoint.rstrip('/')}/events/{event_id}/accessibility-requests?date={event_date}"
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, headers=headers)  # noqa: S310
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            import json

            data = json.loads(resp.read().decode("utf-8"))
            records = data if isinstance(data, list) else data.get("records", [])
            return [r for r in records if isinstance(r, dict)], []
    except urllib.error.HTTPError as exc:
        return [], [f"HTTP {exc.code} from request_history_source"]
    except Exception as exc:  # noqa: BLE001
        return [], [f"Connection error: {type(exc).__name__}: {exc}"]


def _utc_now() -> str:
    """Return current UTC datetime as ISO 8601 string."""
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()
