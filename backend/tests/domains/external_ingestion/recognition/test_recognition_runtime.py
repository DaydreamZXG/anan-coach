from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition_runtime import _approved_endpoint


def _settings(**overrides) -> Settings:
    values = {
        "recognition_processor_endpoint": "http://34.96.236.81:8317/v1",
        "recognition_processor_adapter": "grok_vision_chat_completions",
        "recognition_processor_allow_insecure_http": True,
        "recognition_processor_insecure_http_origin": "http://34.96.236.81:8317",
        "recognition_processor_https_host_allowlist": "34.96.236.81",
    }
    values.update(overrides)
    return Settings(**values)


def test_runtime_accepts_only_explicit_grok_plaintext_origin() -> None:
    assert _approved_endpoint(_settings()) == "http://34.96.236.81:8317/v1"


@pytest.mark.parametrize(
    "override",
    (
        {"recognition_processor_adapter": "private_contract"},
        {"recognition_processor_allow_insecure_http": False},
        {"recognition_processor_insecure_http_origin": "http://34.96.236.81:9000"},
        {"recognition_processor_https_host_allowlist": "recognition.example.com"},
        {"recognition_processor_endpoint": "http://34.96.236.82:8317/v1"},
    ),
)
def test_runtime_rejects_any_unapproved_plaintext_route(override) -> None:
    with pytest.raises(ProcessorConfigurationError):
        _approved_endpoint(_settings(**override))


def test_runtime_keeps_approved_https_as_default() -> None:
    settings = _settings(
        recognition_processor_endpoint="https://recognition.example.com/v1",
        recognition_processor_adapter="private_contract",
        recognition_processor_allow_insecure_http=False,
        recognition_processor_insecure_http_origin="",
        recognition_processor_https_host_allowlist="recognition.example.com",
    )

    assert _approved_endpoint(settings) == "https://recognition.example.com/v1"
