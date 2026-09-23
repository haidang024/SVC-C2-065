"""PostProcessNode — Generate internal Markdown matchday operations handoff document for SVC-C2-065."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json
from src.services.llm_runtime import provider_metadata, request_advisory

# PII / disability-description detection patterns for S-3 output gate
_PII_PATTERN = re.compile(
    r"\b("
    r"attendee[_\s]?name|attendee[_\s]?email|attendee[_\s]?phone|"
    r"disability[_\s]?description|contact[_\s]?address|"
    r"[A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)?@|\+\d{7,15}|"
    r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"
    r")",
    re.IGNORECASE,
)

_CREDENTIAL_PATTERN = re.compile(r"\b(api[_-]?key|password|bearer|jwt|secret)\b", re.IGNORECASE)


class PostProcessNode(FunctionNode):
    """Generate an internal human-review-ready Markdown handoff document.

    Synthesizes event summary, coverage/freshness notices, request-type summaries,
    zone/resource allocations, per-department action items, gap alerts, and citations.

    The generated document is review/distribution-ready but MUST NOT be auto-distributed.
    Extends S-3 output gate to reject any attendee-identifying or disability-description content.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm: object | None = None, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._llm = llm
        self._config = config or {}

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3: Reject output containing PII or credential-like patterns."""
        output = str(result.get("formatted_output", ""))

        if _PII_PATTERN.search(output):
            raise SecurityViolationError(
                "PII or disability-description content detected in handoff output — blocked by S-3 gate"
            )
        if _CREDENTIAL_PATTERN.search(output):
            raise SecurityViolationError("Credential-like content detected in handoff output — blocked by S-3 gate")
        return result

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build the Markdown handoff document from synthesized state."""
        if state.get("input_error_message"):
            message = str(state["input_error_message"])
            return {"formatted_output": message, "status": AgentStatus.SUCCESS.value, "input_error_message": message}
        request_advisory(
            state,
            "Review the safety of a deterministic accessibility handoff briefing.",
            self._llm,
            timeout_s=float(self._config.get("timeout_s", 30)),
            max_retry=int(self._config.get("max_retry", 3)),
        )
        event_id = state.get("event_id", "unknown")
        event_date = state.get("event_date", "unknown")
        event_start_time = state.get("event_start_time", "unknown")
        event_name = state.get("event_name") or "Sports Event"
        no_requests_flag = state.get("no_requests_flag", False)
        total_request_count = state.get("total_request_count", 0)
        action_items = from_json(state.get("action_items_json")) or state.get("action_items") or []
        resource_mappings = from_json(state.get("resource_mappings_json")) or state.get("resource_mappings") or []
        fulfillment_gaps = from_json(state.get("fulfillment_gaps_json")) or state.get("fulfillment_gaps") or []
        request_type_counts = from_json(state.get("request_type_counts_json")) or state.get("request_type_counts") or {}
        source_coverage = from_json(state.get("source_coverage_json")) or state.get("source_coverage") or {}
        request_source_citations = (
            from_json(state.get("request_source_citations_json")) or state.get("request_source_citations") or []
        )
        venue_resource_citations = (
            from_json(state.get("venue_resource_citations_json")) or state.get("venue_resource_citations") or []
        )
        venue_resource_stale = state.get("venue_resource_stale", False)
        venue_resource_version = state.get("venue_resource_version", "unknown")
        staleness_warnings = from_json(state.get("staleness_warnings_json")) or state.get("staleness_warnings") or []
        coverage_warnings = from_json(state.get("coverage_warnings_json")) or state.get("coverage_warnings") or []
        review_flags = from_json(state.get("review_flags_json")) or state.get("review_flags") or []

        lines: list[str] = []

        # ── Header ───────────────────────────────────────────────────────────
        lines.append("# Matchday Accessibility Operations Handoff")
        lines.append("")
        lines.append(f"**Event:** {event_name}  ")
        lines.append(f"**Event ID:** {event_id}  ")
        lines.append(f"**Date:** {event_date}  ")
        lines.append(f"**Start Time:** {event_start_time}  ")
        lines.append("")
        lines.append(
            "> **INTERNAL REVIEW DOCUMENT** — Not for distribution to attendees. "
            "Do not auto-distribute. Assign for human review before any operational use."
        )
        lines.append("")

        # ── Coverage and staleness notices ───────────────────────────────────
        if coverage_warnings or staleness_warnings:
            lines.append("## Data Quality Notices")
            lines.append("")
            for w in coverage_warnings:
                lines.append(f"- ⚠️ {w}")
            for w in staleness_warnings:
                lines.append(f"- ⚠️ {w}")
            lines.append("")

        # ── No-requests notice ───────────────────────────────────────────────
        if no_requests_flag:
            lines.append("## No Accessibility Requests Found")
            lines.append("")
            lines.append("**No accessibility requests were retrieved from any configured source for this event.**")
            lines.append("")
            lines.append("This result does NOT confirm that no accessibility support is required. " "Possible causes:")
            lines.append("- No requests have been submitted yet")
            lines.append("- One or more booking channels is not reporting")
            lines.append("- Source configuration is incomplete or inaccessible")
            lines.append("")
            lines.append(
                "**Action required:** Verify all booking channels are operating and "
                "confirm zero-request status with the Accessibility Coordination team before event day."
            )
            lines.append("")

        # ── Request summary ──────────────────────────────────────────────────
        if not no_requests_flag:
            lines.append("## Accessibility Request Summary")
            lines.append("")
            lines.append(f"**Total requests retrieved:** {total_request_count}")
            lines.append("")

            # Channel coverage
            lines.append("### Booking Channel Coverage")
            lines.append("")
            if source_coverage:
                lines.append("| Channel | Records Found | Count |")
                lines.append("|---------|--------------|-------|")
                for channel, info in source_coverage.items():
                    status = "✅" if info.get("found") else "❌ Missing"
                    lines.append(f"| {channel} | {status} | {info.get('record_count', 0)} |")
            else:
                lines.append("_No channel coverage information available._")
            lines.append("")

            # Request type breakdown
            lines.append("### Request Type Breakdown")
            lines.append("")
            if request_type_counts:
                lines.append("| Request Type | Count |")
                lines.append("|-------------|-------|")
                for rtype, count in sorted(request_type_counts.items()):
                    lines.append(f"| {rtype.replace('_', ' ').title()} | {count} |")
            else:
                lines.append("_No request type data available._")
            lines.append("")

        # ── Zone and resource allocations ────────────────────────────────────
        if resource_mappings:
            lines.append("## Zone and Resource Allocations")
            lines.append("")
            lines.append("| Resource Type | Zone | Needed | Available | Gap | Department |")
            lines.append("|--------------|------|--------|-----------|-----|------------|")
            for m in resource_mappings:
                gap = m.get("gap", 0)
                gap_str = f"**{gap}** ⚠️" if gap > 0 else str(gap)
                lines.append(
                    f"| {m.get('resource_type', '').replace('_', ' ').title()} "
                    f"| {m.get('zone', '')} "
                    f"| {m.get('count_needed', 0)} "
                    f"| {m.get('capacity_available', 0)} "
                    f"| {gap_str} "
                    f"| {m.get('department', '')} |"
                )
            lines.append("")

        # ── Fulfillment gap alerts ───────────────────────────────────────────
        if fulfillment_gaps:
            lines.append("## ⚠️ Fulfillment Gap Alerts")
            lines.append("")
            lines.append(
                "The following resource types have more requests than available capacity. "
                "Arrange additional resources or confirm adjusted allocation before event day."
            )
            lines.append("")
            for gap in fulfillment_gaps:
                lines.append(
                    f"- **{gap.get('resource_type', '').replace('_', ' ').title()}**: "
                    f"{gap.get('count_needed', 0)} needed, {gap.get('capacity_available', 0)} available, "
                    f"gap: **{gap.get('gap', 0)}** — zone: {gap.get('zone', 'unassigned')}, "
                    f"department: {gap.get('department', '')}"
                )
            lines.append("")

        # ── Per-department action items ──────────────────────────────────────
        lines.append("## Department Action Items")
        lines.append("")

        # Group action items by department
        dept_items: dict[str, list[dict[str, Any]]] = {}
        for item in action_items:
            dept = item.get("department", "General")
            dept_items.setdefault(dept, []).append(item)

        for dept in sorted(dept_items.keys()):
            lines.append(f"### {dept}")
            lines.append("")
            for item in sorted(dept_items[dept], key=lambda x: x.get("priority", "LOW")):
                priority = item.get("priority", "LOW")
                icon = "🔴" if priority == "HIGH" else "🟡" if priority == "MEDIUM" else "🟢"
                lines.append(f"- {icon} [{priority}] {item.get('action', '')}")
            lines.append("")

        # ── Review flags ─────────────────────────────────────────────────────
        if review_flags:
            lines.append("## Review Flags")
            lines.append("")
            lines.append("The following flags require human review before this handoff can be used operationally:")
            lines.append("")
            for flag in review_flags:
                lines.append(f"- **[{flag.get('flag_type', '').upper()}]** {flag.get('description', '')}")
            lines.append("")

        # ── Citations ────────────────────────────────────────────────────────
        lines.append("## Data Sources and Citations")
        lines.append("")
        lines.append(f"**Venue Resource Version:** {venue_resource_version}")
        if venue_resource_stale:
            lines.append("⚠️ Venue resource data may be stale — see Data Quality Notices above.")
        lines.append("")
        lines.append("### Request History Sources")
        for cit in request_source_citations:
            lines.append(
                f"- Source: `{cit.get('source', '')}` | Endpoint: `{cit.get('endpoint', '')}` | "
                f"Event: `{cit.get('event_id', '')}` | Records: {cit.get('record_count', 0)} | "
                f"Retrieved: {cit.get('retrieved_at', '')}"
            )
        lines.append("")
        lines.append("### Venue Resource Sources")
        for cit in venue_resource_citations:
            lines.append(
                f"- Source: `{cit.get('source', '')}` | Endpoint: `{cit.get('endpoint', '')}` | "
                f"Version: `{cit.get('version', '')}` | "
                f"Last Updated: {cit.get('last_updated', 'unknown')} | "
                f"Resources: {cit.get('resource_count', 0)}"
            )
        lines.append("")

        # ── Footer ───────────────────────────────────────────────────────────
        lines.append("---")
        lines.append("")
        lines.append(
            "_This document was generated by SVC-C2-065 Sports Ticketing Accessibility Request History Agent. "
            "It is for internal operational planning only. Do not distribute to attendees. "
            "Leave unassigned for human self-claim and review._"
        )

        handoff_markdown = "\n".join(lines)

        emit_trace_event(
            "PostProcessNode_execute_complete",
            {
                "event_id": event_id,
                "total_request_count": total_request_count,
                "no_requests_flag": no_requests_flag,
                "action_items_count": len(action_items),
                "fulfillment_gaps_count": len(fulfillment_gaps),
                "output_chars": len(handoff_markdown),
            },
            state,
        )

        return {
            "handoff_output": handoff_markdown,
            "formatted_output": handoff_markdown,
            "status": AgentStatus.SUCCESS.value,
            **provider_metadata(state),
        }
