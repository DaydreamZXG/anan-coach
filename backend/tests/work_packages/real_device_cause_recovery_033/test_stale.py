from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domains.xingce_learning.cause_agent.provider import (
    CauseAgentProviderConfigurationError,
    build_cause_agent_providers,
)


def test_missing_or_invalid_fallback_configuration_fails_closed_without_secret_leak() -> None:
    primary_only = build_cause_agent_providers(Settings())
    assert [provider.route_key for provider in primary_only] == ["tokenrhythm"]

    secret = "test-secret-that-must-not-appear"
    providers = build_cause_agent_providers(
        Settings(
            environment="production",
            ai_api_key="primary-test-key",
            ai_provider_base_url="https://primary.example.test/v1",
            ai_xingce_cause_provider_release_id="primary-release",
            ai_xingce_cause_provider_https_host_allowlist="primary.example.test",
            ai_xingce_cause_deepseek_api_key=secret,
            ai_xingce_cause_deepseek_provider_https_host_allowlist=(
                "wrong.example.test"
            ),
        )
    )
    assert [provider.route_key for provider in providers] == [
        "tokenrhythm",
        "deepseek_official",
    ]
    with pytest.raises(CauseAgentProviderConfigurationError) as captured:
        providers[1].validate_configuration()
    assert "allowlisted" in str(captured.value)
    assert secret not in str(captured.value)
