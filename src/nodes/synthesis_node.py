"""SynthesisNode — Synthesize operational warnings and actionable departmental coordination items."""

from __future__ import annotations

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

_PRIORITY_HIGH = "HIGH"
_PRIORITY_MEDIUM = "MEDIUM"
_PRIORITY_LOW = "LOW"

# Departments in standard accessibility coordination scope
_STANDARD_DEPARTMENTS = (
    "Accessibility Coordination",
    "Stewards",
    "Medical/Welfare",
)


class SynthesisNode(FunctionNode):
    """Synthesize operational warnings and actionable departmental coordination items.

    Combines request-source coverage, no-requests status, resource staleness, capacity
    gaps, and mapping confidence into explicit review flags for internal human review.

    A missing configured source or zero-record result is never represented as a
    clean fulfillment decision — all data-quality issues produce explicit warnings.
    Never communicates with attendees, modifies bookings, or writes to source systems.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Combine all signals into operational_warnings, action_items, and review_flags."""
        coverage_warnings = from_json(state.get("coverage_warnings_json")) or state.get("coverage_warnings") or []
        staleness_warnings = from_json(state.get("staleness_warnings_json")) or state.get("staleness_warnings") or []
        fulfillment_gaps = from_json(state.get("fulfillment_gaps_json")) or state.get("fulfillment_gaps") or []
        department_coordination = (
            from_json(state.get("department_coordination_json")) or state.get("department_coordination") or {}
        )
        no_requests_flag = state.get("no_requests_flag", False)
        venue_resource_stale = state.get("venue_resource_stale", False)

        operational_warnings: list[str] = []
        action_items: list[dict[str, Any]] = []
        review_flags: list[dict[str, Any]] = []

        # ── Data quality warnings ────────────────────────────────────────────
        # Coverage warnings (missing channels, fetch errors)
        for w in coverage_warnings:
            operational_warnings.append(f"[DATA QUALITY] {w}")
            review_flags.append({"flag_type": "coverage_gap", "description": w})

        # Staleness warnings
        for w in staleness_warnings:
            operational_warnings.append(f"[RESOURCE STALENESS] {w}")
            review_flags.append({"flag_type": "stale_resource", "description": w})

        # ── No-requests status ───────────────────────────────────────────────
        if no_requests_flag:
            msg = (
                "No accessibility requests found for this event. "
                "This may indicate: (a) no requests have been submitted, "
                "(b) one or more booking channels is not reporting, or "
                "(c) source configuration is incomplete. "
                "Do not interpret zero requests as confirmation that no accessibility support is needed."
            )
            operational_warnings.append(f"[NO REQUESTS] {msg}")
            review_flags.append({"flag_type": "no_requests", "description": msg})
            action_items.append(
                {
                    "department": "Accessibility Coordination",
                    "action": "Verify all booking channels are reporting correctly and confirm zero-request status",
                    "priority": _PRIORITY_HIGH,
                    "gap_details": msg,
                }
            )

        # ── Capacity gap action items ────────────────────────────────────────
        for gap in fulfillment_gaps:
            resource_type = gap.get("resource_type") or gap.get("request_type", "unknown")
            zone = gap.get("zone", "unassigned")
            count_needed = gap.get("count_needed", 0)
            capacity = gap.get("capacity_available", 0)
            gap_count = gap.get("gap", 0)
            department = gap.get("department", "Accessibility Coordination")

            gap_msg = (
                f"{resource_type}: {count_needed} requested, {capacity} available "
                f"(gap: {gap_count}) in zone '{zone}'"
            )
            operational_warnings.append(f"[CAPACITY GAP] {gap_msg}")
            review_flags.append({"flag_type": "capacity_gap", "description": gap_msg})

            action_items.append(
                {
                    "department": department,
                    "action": (
                        f"Address capacity gap for {resource_type}: "
                        f"arrange {gap_count} additional unit(s) or confirm adjusted allocation"
                    ),
                    "priority": _PRIORITY_HIGH if gap_count >= 2 else _PRIORITY_MEDIUM,
                    "gap_details": gap_msg,
                }
            )

        # ── Department-specific action items from coordination dict ──────────
        for dept, dept_actions in department_coordination.items():
            for action_desc in dept_actions:
                # Avoid duplicating gap items already added above
                if "CAPACITY GAP" in action_desc:
                    continue
                if "NO RESOURCE CONFIGURED" in action_desc:
                    action_items.append(
                        {
                            "department": dept,
                            "action": action_desc,
                            "priority": _PRIORITY_HIGH,
                            "gap_details": "No venue resource record found for this request type",
                        }
                    )
                elif "WITHIN CAPACITY" in action_desc:
                    action_items.append(
                        {
                            "department": dept,
                            "action": action_desc,
                            "priority": _PRIORITY_LOW,
                            "gap_details": "Within capacity — confirm allocation",
                        }
                    )

        # ── Venue resource staleness action ──────────────────────────────────
        if venue_resource_stale and not no_requests_flag:
            review_flags.append(
                {
                    "flag_type": "stale_resource_with_requests",
                    "description": (
                        "Venue resource data is stale and requests are present — "
                        "capacity figures may be inaccurate; review before distribution"
                    ),
                }
            )
            action_items.append(
                {
                    "department": "Accessibility Coordination",
                    "action": (
                        "Update venue accessibility resource configuration before using capacity figures "
                        "for this event — current data may not reflect actual venue setup"
                    ),
                    "priority": _PRIORITY_HIGH,
                    "gap_details": "Stale venue resource data with active requests",
                }
            )

        # ── Ensure all standard departments have at least a notice ──────────
        departments_with_items = {item["department"] for item in action_items}
        for dept in _STANDARD_DEPARTMENTS:
            if dept not in departments_with_items:
                action_items.append(
                    {
                        "department": dept,
                        "action": (
                            f"Review accessibility handoff for {dept} — "
                            "no specific gaps identified; confirm standard readiness"
                        ),
                        "priority": _PRIORITY_LOW,
                        "gap_details": "No capacity gaps or coverage issues identified for this department",
                    }
                )

        emit_trace_event(
            "SynthesisNode_execute_complete",
            {
                "operational_warning_count": len(operational_warnings),
                "action_item_count": len(action_items),
                "review_flag_count": len(review_flags),
                "no_requests_flag": no_requests_flag,
                "venue_resource_stale": venue_resource_stale,
                "fulfillment_gaps_count": len(fulfillment_gaps),
            },
            state,
        )

        return {
            "operational_warnings_json": to_json(operational_warnings),
            "action_items_json": to_json(action_items),
            "review_flags_json": to_json(review_flags),
            "status": AgentStatus.SUCCESS.value,
        }
