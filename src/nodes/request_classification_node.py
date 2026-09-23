"""RequestClassificationNode — Classify sanitized requests, map to venue resources, and calculate fulfillment gaps."""

from __future__ import annotations

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

# Canonical request types and their associated venue resource types
_REQUEST_TYPE_MAP: dict[str, str] = {
    "wheelchair": "wheelchair_space",
    "wheelchair_space": "wheelchair_space",
    "companion": "companion_seat",
    "companion_seat": "companion_seat",
    "hearing_loop": "hearing_loop",
    "hearing": "hearing_loop",
    "ambulant": "ambulant_accessible_seat",
    "ambulant_accessible": "ambulant_accessible_seat",
    "visual": "visual_support_area",
    "visual_support": "visual_support_area",
    "guide_dog": "guide_dog_relief_area",
    "guide_dog_area": "guide_dog_relief_area",
    "sensory": "sensory_room",
    "sensory_room": "sensory_room",
    "other": "general_accessible_area",
}

_STANDARD_TYPES = (
    "wheelchair_space",
    "companion_seat",
    "hearing_loop",
    "ambulant_accessible_seat",
    "visual_support_area",
    "guide_dog_relief_area",
    "sensory_room",
    "general_accessible_area",
)

# Department responsible by resource type
_RESOURCE_DEPARTMENT_MAP: dict[str, str] = {
    "wheelchair_space": "Accessibility Coordination",
    "companion_seat": "Accessibility Coordination",
    "hearing_loop": "Stewards",
    "ambulant_accessible_seat": "Stewards",
    "visual_support_area": "Accessibility Coordination",
    "guide_dog_relief_area": "Medical/Welfare",
    "sensory_room": "Medical/Welfare",
    "general_accessible_area": "Accessibility Coordination",
}


def _classify_request_type(request: dict[str, Any]) -> str:
    """Map raw request_type or access_needs_summary to a canonical resource type."""
    raw_type = (
        str(request.get("request_type", "")).lower().strip()
        or str(request.get("access_needs_summary", "")).lower().strip()
    )
    # Direct match
    if raw_type in _REQUEST_TYPE_MAP:
        return _REQUEST_TYPE_MAP[raw_type]
    # Substring match
    for key, canonical in _REQUEST_TYPE_MAP.items():
        if key in raw_type:
            return canonical
    return "general_accessible_area"


def _find_venue_resource(venue_resources: list[dict[str, Any]], resource_type: str) -> dict[str, Any] | None:
    """Find the venue resource entry for the given resource_type."""
    for res in venue_resources:
        if res.get("resource_type") == resource_type:
            return res
    return None


class RequestClassificationNode(FunctionNode):
    """Classify sanitized accessibility request types and map to venue resources.

    Uses only anonymized sanitized_requests — no raw PII enters this node.
    Calculates deterministic volume and fulfillment gaps against venue capacity.
    Each mapping retains at least one sanitized request citation and one venue citation.
    Never approves fulfillment or invents capacity not present in venue_resources.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Classify requests, map to resources, calculate gaps, identify departments."""
        sanitized_requests = from_json(state.get("sanitized_requests_json")) or state.get("sanitized_requests") or []
        venue_resources = from_json(state.get("venue_resources_json")) or state.get("venue_resources") or []
        venue_citations = (
            from_json(state.get("venue_resource_citations_json")) or state.get("venue_resource_citations") or []
        )

        # Count requests by canonical resource type
        request_type_counts: dict[str, int] = {}
        type_to_requests: dict[str, list[dict[str, Any]]] = {}  # for citations

        for req in sanitized_requests:
            canonical = _classify_request_type(req)
            request_type_counts[canonical] = request_type_counts.get(canonical, 0) + 1
            type_to_requests.setdefault(canonical, []).append(req)

        # Build resource mappings with gap analysis
        resource_mappings: list[dict[str, Any]] = []
        fulfillment_gaps: list[dict[str, Any]] = []
        department_coordination: dict[str, list[str]] = {}

        # Include all standard types (even if count is 0) for completeness
        all_types = set(request_type_counts.keys()) | set(_STANDARD_TYPES)

        for resource_type in sorted(all_types):
            count_needed = request_type_counts.get(resource_type, 0)
            if count_needed == 0:
                continue  # skip types with no requests

            venue_res = _find_venue_resource(venue_resources, resource_type)
            capacity_available = venue_res["capacity"] if venue_res else 0
            gap = max(0, count_needed - capacity_available)
            zone = venue_res["zone"] if venue_res else "unassigned"
            department = (venue_res.get("department") if venue_res else None) or _RESOURCE_DEPARTMENT_MAP.get(
                resource_type, "Accessibility Coordination"
            )

            # Build citations (at least one from each source when available)
            req_citations_for_type = [
                {
                    "ref_code": r.get("ref_code", ""),
                    "channel": r.get("channel", ""),
                    "source_id": r.get("source_id", ""),
                }
                for r in type_to_requests.get(resource_type, [])[:3]  # cite up to 3
            ]
            venue_citations_for_type = [
                {"source": c.get("source", ""), "endpoint": c.get("endpoint", ""), "version": c.get("version", "")}
                for c in venue_citations[:1]  # cite source once
            ]
            if venue_res:
                venue_citations_for_type.append(
                    {
                        "resource_type": resource_type,
                        "zone": zone,
                        "capacity": capacity_available,
                        "version": venue_res.get("version", ""),
                    }
                )

            mapping: dict[str, Any] = {
                "request_type": resource_type,
                "zone": zone,
                "resource_type": resource_type,
                "count_needed": count_needed,
                "capacity_available": capacity_available,
                "gap": gap,
                "department": department,
                "request_citations": req_citations_for_type,
                "venue_citations": venue_citations_for_type,
            }
            resource_mappings.append(mapping)

            if gap > 0:
                fulfillment_gaps.append(mapping.copy())

            # Department coordination
            dept_actions = department_coordination.setdefault(department, [])
            if venue_res is None:
                dept_actions.append(
                    f"NO RESOURCE CONFIGURED: {resource_type} has {count_needed} requests "
                    "but no venue resource record found — coordinate to establish capacity"
                )
            elif gap > 0:
                dept_actions.append(
                    f"CAPACITY GAP: {resource_type} needs {count_needed}, "
                    f"capacity {capacity_available}, gap {gap} in zone '{zone}'"
                )
            else:
                dept_actions.append(
                    f"WITHIN CAPACITY: {resource_type} needs {count_needed} of {capacity_available} "
                    f"in zone '{zone}' — confirm allocation"
                )

        emit_trace_event(
            "RequestClassificationNode_execute_complete",
            {
                "total_request_types": len(request_type_counts),
                "resource_mappings_count": len(resource_mappings),
                "fulfillment_gaps_count": len(fulfillment_gaps),
                "departments_involved": list(department_coordination.keys()),
            },
            state,
        )

        return {
            "request_type_counts_json": to_json(request_type_counts),
            "resource_mappings_json": to_json(resource_mappings),
            "fulfillment_gaps_json": to_json(fulfillment_gaps),
            "department_coordination_json": to_json(department_coordination),
            "status": AgentStatus.SUCCESS.value,
        }
