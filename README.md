# SVC-C2-065 — Sports Ticketing Accessibility Request History Agent

> **Category**: Cat 2 (multi-step domain workflow)
> **Industry**: Services

## Overview

This template retrieves event-scoped accessibility request history for an upcoming
sports fixture, strips attendee-identifying and disability-description content from
the retrieved records, checks whether every expected booking channel is represented
in the data, maps the sanitized requests to configured venue accessibility resources,
and drafts an internal matchday operations handoff for a human coordinator to review.

It is read-only end to end. It does not modify bookings, contact attendees, distribute
the handoff it drafts, allocate staff or resources, or decide anyone's eligibility for
an accommodation — those actions all happen downstream of this agent, by a person. When
a request-history source or a venue-resource source is not configured, the affected node
refuses and reports a hard error rather than producing a handoff that would silently read
as "there are no accessibility requests for this event".

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it
to your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/` for the design specification and test specification.

## Customising

1. Point `request_history_source` and `venue_resource_source` in `config/config.yaml`
   at your own systems — both are empty by default, and the owning node refuses rather
   than answers until a source is configured.
2. Adjust `expected_booking_channels`, `pii_fields`, and `staleness_threshold_days` in
   `config/config.yaml` to match your own venue's booking channels and data retention.
3. Review the node implementations under `src/nodes/` for domain-specific logic,
   particularly the request-type-to-resource mapping in `request_classification_node.py`.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
