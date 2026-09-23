"""VenueResourceNode — Read-only retrieval and normalization of venue accessibility resource configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json

_REQUIRED_RESOURCE_FIELDS = ("zone", "resource_type", "capacity", "department")


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _days_since(iso_datetime: str) -> int | None:
    """Return days elapsed since the given ISO 8601 datetime, or None if unparseable."""
    try:
        dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
        return (datetime.now(tz=timezone.utc) - dt).days
    except (ValueError, AttributeError):
        return None


def _normalize_resource(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a raw venue resource record. Returns None if required fields are missing."""
    for field in _REQUIRED_RESOURCE_FIELDS:
        if not raw.get(field) and raw.get(field) != 0:
            return None

    try:
        capacity = int(raw["capacity"])
    except (TypeError, ValueError):
        return None

    return {
        "zone": str(raw["zone"]).strip(),
        "resource_type": str(raw["resource_type"]).strip(),
        "capacity": capacity,
        "department": str(raw["department"]).strip(),
        "version": str(raw.get("version", "unknown")).strip(),
        "last_updated": str(raw.get("last_updated", "")).strip(),
    }


class VenueResourceNode(FunctionNode):
    """Retrieve and normalize the configured venue accessibility resource reference.

    Evaluates configurable staleness against the resource's last_updated timestamp.
    Provides deterministic capacity inputs to downstream classification. Never modifies
    the source system.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    @staticmethod
    def _resolve(state: dict[str, Any], key: str, default: str = "") -> str:
        """Read key from top-level state; fall back to state['input_context'][key]."""
        v = state.get(key, "")
        if not v:
            v = state.get("input_context", {}).get(key, default)
        return str(v)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Retrieve venue resources and evaluate staleness."""
        retrieval_scope = from_json(self._resolve(state, "retrieval_scope_json")) or state.get("retrieval_scope") or {}
        staleness_threshold_days = (
            retrieval_scope.get("staleness_threshold_days") or state.get("staleness_threshold_days") or 30
        )

        vr_source = (
            from_json(self._resolve(state, "venue_resource_source_json")) or state.get("venue_resource_source") or {}
        )
        source_endpoint = vr_source.get("endpoint", "")
        source_secret_key = vr_source.get("secret_key", "")
        event_id = retrieval_scope.get("event_id") or self._resolve(state, "event_id")

        if not event_id:
            emit_trace_event(
                "VenueResourceNode_missing_event_id",
                {"event_id": event_id},
                state,
            )
            return {
                "venue_resources_json": to_json([]),
                "venue_resource_version": None,
                "venue_resource_last_updated": None,
                "venue_resource_stale": True,
                "staleness_warnings_json": to_json(["No event_id provided — venue resource retrieval skipped"]),
                "venue_resource_citations_json": to_json([]),
                "status": AgentStatus.ERROR.value,
                "error_message": "event_id is required for venue resource retrieval",
            }

        # STG_MOCK_MODE: skip live secret check — external connectors not wired in STG smoke runs.
        import os as _os

        if _os.environ.get("STG_MOCK_MODE"):
            api_key = "stg-mock-key" if source_secret_key else None
        else:
            ctx = InvocationContext.from_state(state)
            api_key = ctx.secrets.require(source_secret_key) if source_secret_key else None

        raw_resources, fetch_errors, meta = _fetch_venue_resources(
            endpoint=source_endpoint,
            event_id=event_id,
            api_key=api_key,
        )

        staleness_warnings: list[str] = []
        for err in fetch_errors:
            staleness_warnings.append(f"Venue resource fetch error: {err}")

        # Normalize records
        venue_resources: list[dict[str, Any]] = []
        malformed_count = 0
        for raw in raw_resources:
            normalized = _normalize_resource(raw)
            if normalized is None:
                malformed_count += 1
            else:
                venue_resources.append(normalized)

        if malformed_count > 0:
            staleness_warnings.append(f"{malformed_count} venue resource record(s) were malformed and excluded")

        # Evaluate staleness
        resource_version = meta.get("version", "unknown")
        last_updated = meta.get("last_updated", "")
        stale = True  # default to stale if unknown

        if last_updated:
            days_old = _days_since(last_updated)
            if days_old is not None:
                stale = days_old > staleness_threshold_days
                if stale:
                    staleness_warnings.append(
                        f"Venue resource data is {days_old} day(s) old "
                        f"(threshold: {staleness_threshold_days} days) — "
                        "resource configuration may not reflect current venue setup"
                    )
        else:
            staleness_warnings.append(
                "Venue resource last_updated timestamp is unavailable — "
                "resource freshness cannot be determined; treat as potentially stale"
            )

        if not venue_resources and not fetch_errors:
            staleness_warnings.append(
                "No venue accessibility resources found for this event — "
                "capacity planning cannot proceed without resource configuration"
            )

        citations = [
            {
                "source": "venue_resource_source",
                "endpoint": source_endpoint,
                "event_id": event_id,
                "version": resource_version,
                "last_updated": last_updated,
                "resource_count": len(venue_resources),
                "retrieved_at": _utc_now(),
            }
        ]

        emit_trace_event(
            "VenueResourceNode_execute_complete",
            {
                "event_id": event_id,
                "resource_count": len(venue_resources),
                "malformed_count": malformed_count,
                "stale": stale,
                "staleness_warning_count": len(staleness_warnings),
                "version": resource_version,
            },
            state,
        )

        return {
            "venue_resources_json": to_json(venue_resources),
            "venue_resource_version": resource_version,
            "venue_resource_last_updated": last_updated,
            "venue_resource_stale": stale,
            "staleness_warnings_json": to_json(staleness_warnings),
            "venue_resource_citations_json": to_json(citations),
            "status": AgentStatus.SUCCESS.value,
        }


def _fetch_venue_resources(
    endpoint: str,
    event_id: str,
    api_key: str | None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Fetch venue resource records from the configured source.

    Returns (records, errors, metadata). Read-only — never modifies source.
    """
    import json
    import urllib.error
    import urllib.request

    if not endpoint:
        return [], ["No endpoint configured for venue_resource_source"], {}

    try:
        url = f"{endpoint.rstrip('/')}/venues/{event_id}/accessibility-resources"
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, headers=headers)  # noqa: S310
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
            records = data.get("resources", data) if isinstance(data, dict) else data
            meta = {
                "version": data.get("version", "unknown") if isinstance(data, dict) else "unknown",
                "last_updated": data.get("last_updated", "") if isinstance(data, dict) else "",
            }
            return [r for r in (records if isinstance(records, list) else []) if isinstance(r, dict)], [], meta
    except urllib.error.HTTPError as exc:
        return [], [f"HTTP {exc.code} from venue_resource_source"], {}
    except Exception as exc:  # noqa: BLE001
        return [], [f"Connection error: {type(exc).__name__}: {exc}"], {}
