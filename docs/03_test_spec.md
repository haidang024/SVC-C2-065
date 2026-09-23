# Test Specification

## Test Strategy
- Coverage target: 90%
- Test types: Unit (node-level), Proof-of-Boundary (framework contracts)

---

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict, no Pydantic/dataclass | `State` inherits `AgentState`; all fields are primitives/JSON-serializable; AST scan finds no `BaseModel` or `dataclass` | PASS |
| TC-02 | SecurityViolationError fires on oversized input (PreProcessNode) | `SecurityViolationError` raised before `execute()` when input exceeds max length | PASS |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations; no field named `token`, `api_key`, `password`, `jwt` in State | PASS |
| TC-04 | InvocationContext constructed only via `from_state()` inside nodes; the authenticated HTTP adapter is exempt | Credentials are resolved at execution time and never stored in State | PASS |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from all `execute()` bodies | 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `@final` enforcement; no override present in any node file | 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `@final` enforcement; no override present in any node file | 0 overrides |
| TC-08 | `required_trust_level` enforced through `node(state)` with an always-present privileged fixture | Insufficient trust is refused before `execute()` and the test proves execution did not occur | PASS |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial on PreProcessNode and RequestHistoryNode | PreProcessNode rejects injection patterns, oversized inputs, credential field names; RequestHistoryNode blocks PII field name leak | Hook body non-trivial |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial on PostProcessNode | PostProcessNode rejects PII patterns and credential patterns in Markdown handoff | Hook body non-trivial |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | All 6 nodes (PreProcessNode, RequestHistoryNode, VenueResourceNode, RequestClassificationNode, SynthesisNode, PostProcessNode) emit ≥1 domain event | ≥1 per node |

---

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path across all inner and outer nodes | No silent failures; each `node(state)` call produces ≥1 domain event | PASS |
| PB-2 | State serialization | Post-invoke State contains only primitives (str, int, bool, list, dict, None) | `json.dumps(state)` succeeds; no Pydantic/dataclass in output | PASS |
| PB-3 | L1 external-source boundary | RequestHistoryNode and VenueResourceNode call configured REST endpoints (mocked in tests) | Data retrieved from mock; no write operations on source; `_fetch_records` / `_fetch_venue_resources` never POST/PUT/DELETE | PASS |
| PB-4 | Import isolation | No `agenticstar` or platform Level 0 imports in `src/` | AST scan: 0 violations | PASS |
| PB-5 | Checkpoint safety *(conditional)* | When checkpointing and AgentCore ingress hooks are enabled, inspect checkpoint payload, metadata, and pending writes for unsafe or raw ingress | Auto-waived because this template enables neither memory nor HITL |
| PB-6 | Invoke execution order | `node.__call__()` enforces S-1 → `node_start` → S-2 → `execute()` → S-3 → `node_complete`; an always-present fixture proves insufficient trust is denied before execution | Order and negative boundary verified | PASS |
| PB-7 | HITL interrupt propagation *(conditional)* | Runtime `config/config.yaml` sets `hitl.enabled: false` | **Auto-waived — non-HITL** | N/A |

> PB-1 through PB-6 are mandatory. PB-7 is auto-waived because runtime `config/config.yaml` does not enable HITL.

---

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Successful handoff generation | Valid event config, mock source with 5 wheelchair + 2 hearing loop requests, venue capacity 4 wheelchair / 5 hearing loop | Handoff Markdown has event summary, gap alert for wheelchair (gap=1), within-capacity for hearing loop, action items for all departments | PASS |
| BL-02 | Invalid config — missing event_id | State missing `event_id` | `status=ERROR`, `error_message` contains "event_id is required", pipeline stops at PreProcessNode | PASS |
| BL-03 | Invalid config — missing request_history_source | State with event fields but no `request_history_source` | `status=ERROR`, pipeline stops at PreProcessNode with structured error | PASS |
| BL-04 | Incomplete channels — missing "phone" | Source returns records for "online" only; "phone" in `expected_booking_channels` | `coverage_warnings` contains "phone" missing; handoff data quality section present | PASS |
| BL-05 | Stale venue resource data | `venue_resource_last_updated` 45 days ago, threshold 30 | `venue_resource_stale=True`; staleness warning in handoff; HIGH priority action item if requests present | PASS |
| BL-06 | Zero accessibility requests | Source returns empty list | `no_requests_flag=True`, `total_request_count=0`; handoff contains explicit no-requests notice (not empty document) | PASS |
| BL-07 | Capacity exceeded — multiple gaps | 6 wheelchair (capacity 4), 3 hearing loop (capacity 2) | `fulfillment_gaps` has 2 entries; both in handoff gap section; HIGH priority action items | PASS |
| BL-08 | Source fetch error | Source endpoint raises `URLError` (mocked) | `coverage_warnings` contains connection error; `no_requests_flag=True`; handoff warns of source error | PASS |
| BL-09 | Citations present in mappings | 3 wheelchair requests from "online" | Each `resource_mappings` entry for `wheelchair_space` has ≥1 `request_citations` (with `ref_code`) and ≥1 `venue_citations` | PASS |
| BL-10 | Anonymization — no raw PII in sanitized_requests | Source record has `attendee_name="John Smith"`, `disability_description="uses manual wheelchair"` | `sanitized_requests` has no `attendee_name` or `disability_description`; `access_needs_summary` is category string only | PASS |
| BL-11 | S-3 gate rejects PII in handoff output | PostProcessNode receives state where handoff contains email pattern | `SecurityViolationError` raised by `_extra_security_gate_output()`; output blocked | PASS |
| BL-12 | Reference codes are per-run only | Two calls to RequestHistoryNode with same source record ID (different module instances) | Reference codes differ between instantiations (per-run salt changes at module import) | PASS |
| BL-13 | All standard departments receive action items | Any valid run with ≥1 request | `action_items` contains entries for Accessibility Coordination, Stewards, and Medical/Welfare | PASS |
| BL-14 | No-resource-configured gap | 3 sensory room requests, no `sensory_room` in venue resources | `department_coordination` contains "NO RESOURCE CONFIGURED" notice; `fulfillment_gaps` entry with `capacity_available=0` | PASS |

---

## Test Execution Summary
- Execution date: 2026-08-18
- Total collected tests: 82
- Pass: 79 / Fail: 0 / Skip: 3 (two non-HITL PB-7 cases and PB-5 checkpointing are auto-waived)
- Coverage: Not measured by `check-local.sh`
