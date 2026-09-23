# Template Design Specification

## Position in AgentCore Architecture

- **Agent Class**: Graph (src/graph/graph.py)
- **L1 Base**: AgentBaseGraph (Cat 2 — outer) + BaseGraph (inner DomainWorkflowGraph)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; inner graph via GraphNode)

## Architecture Overview

### Node Configuration

#### Outer Graph (AgentBaseGraph)

| Node | Class | Responsibility | Trust Level | Inherits/Overrides |
|------|-------|---------------|-------------|-------------------|
| initialize | InitializeNode (default) | Session initialization | — | InitializeNode (framework default) |
| pre_process | PreProcessNode | Validate event details, sources, channels, config | VERIFIED_EXTERNAL | FunctionNode; implements `_extra_security_gate_input()` |
| main | AccessibilityWorkflowGraphNode | Wrap inner 4-step domain workflow | VERIFIED_EXTERNAL (GraphNode) | GraphNode; `get_subgraph()`, `extract_input()`, `merge_output()` |
| post_process | PostProcessNode | Generate Markdown handoff; S-3 PII output gate | VERIFIED_EXTERNAL | FunctionNode; implements `_extra_security_gate_output()` |
| finalize | FinalizeNode (default) | Session finalization | — | FinalizeNode (framework default) |

#### Inner Graph (DomainWorkflowGraph — BaseGraph)

| Node | Class | Responsibility | Trust Level |
|------|-------|---------------|-------------|
| request_history | RequestHistoryNode | Retrieve and anonymize request records; per-run ref codes | ANONYMOUS |
| venue_resource | VenueResourceNode | Retrieve venue resource config; evaluate staleness | ANONYMOUS |
| request_classification | RequestClassificationNode | Classify request types; map to resources; calculate gaps | ANONYMOUS |
| synthesis | SynthesisNode | Synthesize warnings and departmental action items | ANONYMOUS |

### Data Flow

```
Outer graph (AgentBaseGraph):
START → initialize → pre_process → main (AccessibilityWorkflowGraphNode) → {route} → post_process → finalize → END
                                                ↓ (retry on ERROR, max 3)
                                             pre_process

Inside main (DomainWorkflowGraph — BaseGraph):
  START → request_history → venue_resource → request_classification → synthesis → END
```

**Four-step inner data flow:**
1. **request_history**: Fetches raw records from configured ticketing/request-history source. Applies `_extra_security_gate_input()` PII block before any downstream use. Replaces attendee PII and disability-description with anonymized reference codes. Returns `sanitized_requests`, `source_coverage`, `coverage_warnings`, `request_source_citations`.
2. **venue_resource**: Fetches venue accessibility resource configuration (zone, type, capacity, department). Evaluates staleness against `staleness_threshold_days`. Returns `venue_resources`, `venue_resource_stale`, `staleness_warnings`, `venue_resource_citations`.
3. **request_classification**: Classifies sanitized request types (wheelchair, companion, hearing loop, ambulant, visual, guide dog, sensory, other). Maps each type to venue resources. Calculates fulfillment gaps deterministically. Returns `request_type_counts`, `resource_mappings`, `fulfillment_gaps`, `department_coordination`.
4. **synthesis**: Combines coverage warnings, staleness warnings, gap alerts, and no-requests status into `operational_warnings`, `action_items` (per department), and `review_flags`.

**Post-process (outer):** Generates Markdown handoff from synthesized state. Applies `_extra_security_gate_output()` to reject any PII or disability-description content.

### Transient Anonymized Reference Lifecycle

Per-run reference codes are generated in `RequestHistoryNode` using a per-process salt + SHA-256 of the source record ID. The salt is `uuid.uuid4().hex` at module import time, so:
- Codes are stable within a single agent run (traceable to source records)
- Codes change across runs (no cross-run linkage possible)
- The salt and mapping are never persisted to State or checkpoints

### No-Requests Behavior

When `total_request_count == 0`:
- `no_requests_flag = True` is set in State
- `SynthesisNode` generates an explicit no-requests warning action item
- `PostProcessNode` emits a clear no-requests notice in the handoff (not an empty "completed" document)
- The output explicitly states this does not confirm zero accessibility needs

### Source/Config Contracts

**request_history_source** (dict):
- `type`: connector type identifier (`rest_api`)
- `endpoint`: base URL of the request-history API
- `secret_key`: name of the secret in the secrets provider (e.g. `SVC_C2_065_REQUEST_HISTORY_API_KEY`)

**venue_resource_source** (dict):
- `type`: connector type identifier (`rest_api`)
- `endpoint`: base URL of the venue resource API
- `secret_key`: name of the secret in the secrets provider (e.g. `SVC_C2_065_VENUE_RESOURCE_API_KEY`)

### State Definition

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| event_id | str | Unique event identifier for scoped retrieval | ✅ |
| event_date | str | ISO 8601 date of the event | ✅ |
| event_start_time | str | ISO 8601 start time | ✅ |
| event_name | Optional[str] | Human-readable event name | ✗ |
| request_history_source | Optional[dict] | Source config for request retrieval | ✅ |
| venue_resource_source | Optional[dict] | Source config for venue resource retrieval | ✅ |
| expected_booking_channels | Optional[list] | Channel names expected to be covered | ✅ |
| pii_fields | Optional[list] | Field names in source records containing PII | ✅ |
| staleness_threshold_days | Optional[int] | Max age (days) for fresh venue resources | ✗ |
| retrieval_scope | Optional[dict] | Normalized scope produced by PreProcessNode | ✗ |
| sanitized_requests | Optional[list] | Anonymized request records (no raw PII) | ✗ |
| total_request_count | Optional[int] | Total requests retrieved | ✗ |
| no_requests_flag | Optional[bool] | True when zero requests returned | ✗ |
| source_coverage | Optional[dict] | Per-channel coverage status | ✗ |
| coverage_warnings | Optional[list] | Missing/partial channel warnings | ✗ |
| request_source_citations | Optional[list] | Source citation records | ✗ |
| venue_resources | Optional[list] | Normalized venue resource records | ✗ |
| venue_resource_version | Optional[str] | Version tag of venue resource snapshot | ✗ |
| venue_resource_last_updated | Optional[str] | ISO 8601 datetime of last resource update | ✗ |
| venue_resource_stale | Optional[bool] | True if resource exceeds staleness threshold | ✗ |
| staleness_warnings | Optional[list] | Staleness warning strings | ✗ |
| venue_resource_citations | Optional[list] | Venue source citation records | ✗ |
| request_type_counts | Optional[dict] | Count per canonical request type | ✗ |
| resource_mappings | Optional[list] | Request-to-resource mapping records | ✗ |
| fulfillment_gaps | Optional[list] | Mappings where gap > 0 | ✗ |
| department_coordination | Optional[dict] | Per-department action item lists | ✗ |
| operational_warnings | Optional[list] | Combined operational warning strings | ✗ |
| action_items | Optional[list] | Structured departmental action items | ✗ |
| review_flags | Optional[dict] | Review flags for human sign-off | ✗ |
| handoff_output | Optional[str] | Final Markdown handoff document | ✗ |
| formatted_output | Optional[str] | Same as handoff_output (framework convention) | ✗ |
| error_message | Optional[str] | Human-readable error description | ✗ |
| validated_input | Optional[str] | Normalized input summary from PreProcessNode | ✗ |

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (checkpoint DB leakage)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)
- No raw attendee PII or disability-description content at any point in State

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle) — used in PreProcessNode and RequestHistoryNode/VenueResourceNode via `InvocationContext.from_state(state)` and `ctx.secrets.require()`
- [ ] ConnectionPolicy (retry/timeout strategy) — not used; connector handles timeout
- [x] SecurityViolationError — raised in PreProcessNode `_extra_security_gate_input()` and PostProcessNode `_extra_security_gate_output()`
- [x] S-2: `_extra_security_gate_input()` — implemented on PreProcessNode (injection/size check) and RequestHistoryNode (PII field leak check)
- [x] S-3: `_extra_security_gate_output()` — implemented on PostProcessNode (PII pattern + credential pattern scan on Markdown output)
- [x] S-4: `emit_trace_event()` — at least one domain-specific event inside each `execute()` across all 6 nodes

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass → framework `@final` gate always runs automatically; extend via hooks only
> - `GraphNode` (AccessibilityWorkflowGraphNode) → deliberate no-op; upstream PreProcessNode gate already applied

### Composition Pattern

- **Pattern**: GraphNode (subgraph)
- **Composition target**: DomainWorkflowGraph (inner BaseGraph, 4 domain nodes)
- **Error propagation strategy**: `propagate` (inner graph errors surface as SubgraphError)

### Secrets Management

- All source credentials accessed via `ctx.secrets.require(secret_key_name)`
- Never via `os.environ` (sole exception: `INVOKE_AUTH_TOKEN` in `server.py`)
- Secret key names declared in `config/agent.yaml` under `requires.secrets`

## EU AI Act Art.13 Design-Time Evidence (Advisory until 2026-09-01; required from 2026-09-01 when `docs/01_proposal.md` declares Annex III `In scope`)

The proposal declares this template **Not in scope**, so Art.13 evidence is not a
release gate. The design nevertheless records its transparency and oversight controls.

| Evidence item | Design reference / description |
|---------------|--------------------------------|
| Intended purpose and operating context | Prepare an internal pre-event accessibility operations handoff for authorized venue staff. |
| System capabilities and limitations | Retrieves and anonymizes request history, checks source coverage and resource freshness, maps request categories to capacity, and drafts coordination items; it cannot change bookings, decide attendee eligibility, contact attendees, allocate staff, or distribute output. |
| User-facing transparency information | The handoff exposes source citations, channel-coverage gaps, resource staleness, capacity gaps, no-request ambiguity, and review flags. |
| Human oversight mechanism | The generated document is explicitly internal-review-only and must be reviewed and manually distributed by the designated Accessibility Coordinator. |

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework.*`, `shared.*`, `langgraph.*`, own `src.*` only

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed sequential pipeline with deterministic steps; no LLM loop needed |
| Inner graph parent | BaseGraph | AgentBaseGraph | **BaseGraph** | Fully custom node topology (4 named steps); no standard backbone slots needed |
| Composition pattern | GraphNode (subgraph) | RemoteAgentNode (HTTP) | **GraphNode** | Inner domain nodes run in-process; no separate service deployment |
| PII anonymization location | PreProcessNode | RequestHistoryNode | **RequestHistoryNode + `_extra_security_gate_input()`** | PII is not visible until retrieval; gate runs before any downstream access |
| Reference code strategy | UUID random per record | SHA-256 of run-salt + source ID | **SHA-256 (per-run salt)** | Deterministic within run (traceable), non-persistent across runs (privacy) |
| Output format | Structured JSON | Markdown document | **Markdown** | Venue operators require human-readable matchday handoff; JSON unsuitable for direct review |
| Error on missing source | Hard stop (ERROR status) | Soft warning + partial result | **Hard stop** | Missing source configuration cannot produce a reliable handoff; partial results would be misleading |
| Cross-run reference linkage | Persist ref mapping in State | Per-run only (no persistence) | **Per-run only** | Privacy requirement: attendee linkage across runs prohibited |
