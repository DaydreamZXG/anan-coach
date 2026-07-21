from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domains.external_ingestion.recognition.errors import ProcessorConfigurationError
from app.domains.external_ingestion.recognition_runtime import (
    _approved_endpoint,
    build_recognition_service,
)


def _settings(**overrides) -> Settings:
    values = {
        "recognition_processor_endpoint": "https://recognition.example.com/v1",
        "recognition_processor_adapter": "private_contract",
        "recognition_processor_https_host_allowlist": "recognition.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_runtime_accepts_approved_https_origin() -> None:
    assert _approved_endpoint(_settings()) == "https://recognition.example.com/v1"


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://34.96.236.81:8317/v1",
        "ftp://recognition.example.com/v1",
        "https://other.example.com/v1",
    ),
)
def test_runtime_rejects_plaintext_and_unapproved_routes(endpoint: str) -> None:
    with pytest.raises(ProcessorConfigurationError):
        _approved_endpoint(_settings(recognition_processor_endpoint=endpoint))


def test_runtime_keeps_approved_https_as_default() -> None:
    settings = _settings(
        recognition_processor_endpoint="https://recognition.example.com/v1",
        recognition_processor_adapter="private_contract",
        recognition_processor_https_host_allowlist="recognition.example.com",
    )

    assert _approved_endpoint(settings) == "https://recognition.example.com/v1"


def test_runtime_rejects_processor_construction_when_not_enabled() -> None:
    with pytest.raises(ProcessorConfigurationError, match="not enabled"):
        build_recognition_service(session=None, settings=_settings())  # type: ignore[arg-type]
