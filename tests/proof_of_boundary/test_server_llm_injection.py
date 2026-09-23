"""Proof that Azure OpenAI clients are invocation-scoped and auth trust remains bounded."""

from __future__ import annotations

import importlib


def test_server_keeps_llm_invocation_scoped() -> None:
    import src.api.server as server

    importlib.reload(server)
    assert server.app is not None
    assert server.agent is not None
    assert server._llm is None
    assert server._namespace == "agent1000"


def test_server_chains_environment_secrets_first() -> None:
    import src.api.server as server

    providers = server._secrets_provider._providers
    assert providers
    assert providers[0].__class__.__name__ == "EnvProvider"


def test_external_bearer_never_promotes_to_internal() -> None:
    import src.api.server as server
    from framework.schemas.trust_level import TrustLevel

    assert (
        server._resolve_standalone_trust(
            TrustLevel.ANONYMOUS,
            "Bearer external",
            "external",
            "runner",
        )
        is TrustLevel.VERIFIED_EXTERNAL
    )


def test_runner_bearer_promotes_to_internal() -> None:
    import src.api.server as server
    from framework.schemas.trust_level import TrustLevel

    assert (
        server._resolve_standalone_trust(
            TrustLevel.ANONYMOUS,
            "Bearer runner",
            "external",
            "runner",
        )
        is TrustLevel.INTERNAL
    )


def test_wrong_or_missing_bearer_is_rejected_when_auth_is_enabled() -> None:
    import pytest
    from fastapi import HTTPException
    from framework.schemas.trust_level import TrustLevel
    import src.api.server as server

    for authorization in ("", "Bearer wrong"):
        with pytest.raises(HTTPException) as exc:
            server._resolve_standalone_trust(
                TrustLevel.ANONYMOUS,
                authorization,
                "external",
                "runner",
            )
        assert exc.value.status_code == 401
