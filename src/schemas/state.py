"""State — Flat TypedDict state contract for SVC-C2-065 Sports Ticketing Accessibility Request History Agent.

ADR-005: State must be a flat TypedDict (msgpack-safe).
All structured fields (dict/list) are JSON-string-encoded before storage.
No credentials, Pydantic models, dataclasses, or arbitrary Python objects.
Attendee PII and disability-description content must be anonymized before
entering any downstream field. Only sanitized/anonymized records appear here.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class State(AgentState):
    """Flat, JSON-safe state contract for SVC-C2-065 accessibility request history agent.

    Privacy note:
    - Raw attendee PII (name, contact, disability description) NEVER appears in State.
    - sanitized_requests_json contains anonymized records with per-run reference codes only.

    Field sensitivity:
    - event_id, event_date, event_start_time: LOW — public scheduling data
    - sanitized_requests_json: MEDIUM — anonymized; no direct PII
    - venue_resources_json: LOW — venue configuration data
    - handoff_output: LOW — internal review document; no PII

    Structured fields (dict/list) are stored as JSON strings (_json suffix).
    Plain scalar fields are stored as their native Python types.
    """

    # ── Event identification (required input) ────────────────────────────────
    event_id: str
    event_date: str  # ISO 8601 date string (e.g. "2026-08-15")
    event_start_time: str  # ISO 8601 time string (e.g. "19:30:00")
    event_name: Optional[str]

    # ── Source configuration (set during pre-process) ────────────────────────
    # request_history_source_json: JSON {"type": str, "endpoint": str, "secret_key": str}
    request_history_source_json: Optional[str]
    # venue_resource_source_json: JSON {"type": str, "endpoint": str, "secret_key": str}
    venue_resource_source_json: Optional[str]
    # expected_booking_channels_json: JSON list of channel names expected to be covered
    expected_booking_channels_json: Optional[str]
    # pii_fields_json: JSON list of field names in raw source records that contain PII
    pii_fields_json: Optional[str]
    # staleness_threshold_days: max age (days) for venue resource data — plain int
    staleness_threshold_days: Optional[int]
    # retrieval_scope_json: JSON normalized scope dict for downstream retrievals
    retrieval_scope_json: Optional[str]

    # ── Request retrieval output ─────────────────────────────────────────────
    # sanitized_requests_json: JSON list of anonymized request records (no raw PII)
    # Each record: {ref_code: str, request_type: str, channel: str,
    #               booking_date: str, access_needs_summary: str, source_id: str}
    sanitized_requests_json: Optional[str]
    # total_request_count: total number of accessibility requests found — plain int
    total_request_count: Optional[int]
    # no_requests_flag: True when zero requests returned from all sources — plain bool
    no_requests_flag: Optional[bool]
    # source_coverage_json: JSON {channel_name: {"found": bool, "record_count": int}}
    source_coverage_json: Optional[str]
    # coverage_warnings_json: JSON list of warning strings for missing/partial channels
    coverage_warnings_json: Optional[str]
    # request_source_citations_json: JSON list of source citation dicts
    request_source_citations_json: Optional[str]

    # ── Venue resource retrieval output ─────────────────────────────────────
    # venue_resources_json: JSON list of resource records
    # Each: {zone: str, resource_type: str, capacity: int, department: str,
    #        version: str, last_updated: str}
    venue_resources_json: Optional[str]
    # venue_resource_version: version tag of the venue resource snapshot — plain str
    venue_resource_version: Optional[str]
    # venue_resource_last_updated: ISO 8601 datetime of last resource update — plain str
    venue_resource_last_updated: Optional[str]
    # venue_resource_stale: True when resource data exceeds staleness threshold — plain bool
    venue_resource_stale: Optional[bool]
    # staleness_warnings_json: JSON list of staleness warning strings
    staleness_warnings_json: Optional[str]
    # venue_resource_citations_json: JSON list of venue source citation dicts
    venue_resource_citations_json: Optional[str]

    # ── Classification and mapping output (inner domain step) ────────────────
    # request_type_counts_json: JSON {request_type: int}
    request_type_counts_json: Optional[str]
    # resource_mappings_json: JSON list of mapping records
    # Each: {request_type: str, zone: str, resource_type: str, count_needed: int,
    #        capacity_available: int, gap: int, department: str,
    #        request_citations: list, venue_citations: list}
    resource_mappings_json: Optional[str]
    # fulfillment_gaps_json: JSON list of gap records (subset of resource_mappings where gap > 0)
    fulfillment_gaps_json: Optional[str]
    # department_coordination_json: JSON {department: list[str]} — action items per department
    department_coordination_json: Optional[str]

    # ── Synthesis output ────────────────────────────────────────────────────
    # operational_warnings_json: JSON list of combined data-quality and gap warning strings
    operational_warnings_json: Optional[str]
    # action_items_json: JSON list of actionable coordination items (structured)
    # Each: {department: str, action: str, priority: str, gap_details: str}
    action_items_json: Optional[str]
    # review_flags_json: JSON list of {flag_type: str, description: str}
    review_flags_json: Optional[str]

    # ── Post-process output ──────────────────────────────────────────────────
    # handoff_output: full Markdown handoff document (internal review only) — plain str
    handoff_output: Optional[str]

    # ── Status and error tracking ────────────────────────────────────────────
    error_message: Optional[str]
    validated_input: Optional[str]

    # User-correctable input guidance (newline-delimited for flat state compatibility)
    input_error_message: str
    input_error_guidance: str

    # Invocation-scoped provider observability (never contains secret details)
    generation_mode: str
    provider_error_message: str
