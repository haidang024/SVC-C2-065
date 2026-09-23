"""Unit coverage for invocation-scoped Azure OpenAI runtime behavior."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from src.services.llm_runtime import complete_text, request_advisory


class _CompleteClient:
    def complete(self, messages: list[dict[str, str]]) -> dict[str, str]:
        assert messages
        return {"content": "Grounded review complete."}


class _FailingClient:
    def complete(self, messages: list[dict[str, str]]) -> str:
        raise RuntimeError("provider unavailable")


def test_complete_text_supports_injected_client() -> None:
    result = complete_text(
        {},
        [{"role": "user", "content": "Review"}],
        _CompleteClient(),
    )
    assert result == "Grounded review complete."


def test_advisory_provider_failure_never_raises() -> None:
    state: dict[str, Any] = {}
    assert request_advisory(state, "Review safely", _FailingClient()) is None
    assert state["generation_mode"] == "deterministic_fallback"
    assert "timed out" in state["provider_error_message"]


def test_resolve_azure_llm_uses_three_invocation_secrets(
    monkeypatch: Any,
) -> None:
    from src.services import llm_runtime

    requested: list[str] = []

    class _Secrets:
        def require(self, name: str) -> str:
            requested.append(name)
            return f"value-for-{name}"

    class _Context:
        secrets = _Secrets()

    captured: dict[str, Any] = {}

    class _AzureClient:
        def __init__(self, config: dict[str, Any]) -> None:
            captured.update(config)

    monkeypatch.setattr(
        llm_runtime.InvocationContext,
        "from_state",
        lambda state: _Context(),
    )
    monkeypatch.setattr(
        "shared.services.llm.azure_openai_client.AzureOpenAIClient",
        _AzureClient,
    )

    client = llm_runtime.resolve_azure_llm({}, timeout_s=12.5, max_retry=1)
    assert isinstance(client, _AzureClient)
    assert requested == [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
    ]
    assert captured["azure_endpoint"] == "value-for-AZURE_OPENAI_ENDPOINT"
    assert captured["timeout"] == 12.5
    assert captured["max_retries"] == 1


def test_marketplace_output_exposes_provider_status() -> None:
    from src.graph.graph import Graph

    output = Graph(config={}).get_output(
        {
            "generation_mode": "deterministic_fallback",
            "provider_error_message": "Provider unavailable; fallback used.",
        }
    )
    assert output["generation_mode"] == "deterministic_fallback"
    assert output["provider_error_message"] == "Provider unavailable; fallback used."


def test_progress_helper_uses_agentcore_event_api(monkeypatch: Any) -> None:
    from src.services.progress_events import emit_progress

    captured: dict[str, Any] = {}

    class _Emitter:
        def emit_event(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    fake_module = SimpleNamespace(
        EventType=SimpleNamespace(PROGRESS_UPDATE="progress_update"),
        emitter=lambda: _Emitter(),
    )
    monkeypatch.setitem(sys.modules, "shared.services.events", fake_module)

    emit_progress("Working", {"provider": "azure_openai"})
    assert captured == {
        "event_type": "progress_update",
        "message": "Working",
        "metadata": {"provider": "azure_openai"},
    }
