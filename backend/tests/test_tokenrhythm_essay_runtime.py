from __future__ import annotations

import json

from app.core.config import Settings, get_settings
from app.domains.essay_intelligence.multi_model_runtime import (
    EssayMultiModelRuntimePlan,
    _price_cards,
    _primary_settings,
    _tokenrhythm_settings,
)
from app.domains.essay_intelligence.review_checkpoint import (
    _CHECKPOINT_PHASE_PATTERN,
)


def test_tokenrhythm_environment_replaces_grok_without_secret_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AI_ESSAY_TOKENRHYTHM_BASE_URL", "https://tokenrhythm.studio/v1")
    monkeypatch.setenv("AI_ESSAY_TOKENRHYTHM_API_KEY", "runtime-only-secret")
    monkeypatch.setenv("AI_ESSAY_TOKENRHYTHM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv(
        "AI_ESSAY_TOKENRHYTHM_PROVIDER_RELEASE_ID",
        "tokenrhythm-deepseek-v4-flash",
    )
    monkeypatch.setenv("AI_ESSAY_GROK_API_KEY", "deprecated-secret")

    settings = get_settings()

    assert settings.ai_essay_tokenrhythm_base_url == "https://tokenrhythm.studio/v1"
    assert settings.ai_essay_tokenrhythm_api_key == "runtime-only-secret"
    assert settings.ai_essay_tokenrhythm_model == "deepseek-v4-flash"
    assert not hasattr(settings, "ai_essay_grok_api_key")


def test_tokenrhythm_candidate_settings_map_to_grounded_provider_contract() -> None:
    settings = Settings(
        ai_essay_tokenrhythm_base_url="https://tokenrhythm.studio/v1",
        ai_essay_tokenrhythm_api_key="runtime-only-secret",
        ai_essay_tokenrhythm_model="deepseek-v4-flash",
        ai_essay_tokenrhythm_provider_release_id="tokenrhythm-deepseek-v4-flash",
        ai_essay_tokenrhythm_provider_https_host_allowlist="tokenrhythm.studio",
        ai_essay_tokenrhythm_provider_idempotency_capability="nonreplayable_audited",
        ai_essay_tokenrhythm_provider_request_id_header="x-trace-id",
        ai_essay_tokenrhythm_max_input_bytes=8_388_608,
        ai_essay_tokenrhythm_max_output_bytes=2_097_152,
    )

    mapped = _tokenrhythm_settings(settings)

    assert mapped.ai_provider_base_url == "https://tokenrhythm.studio/v1"
    assert mapped.ai_api_key == "runtime-only-secret"
    assert mapped.ai_essay_review_model == "deepseek-v4-flash"
    assert mapped.ai_essay_review_provider_release_id == "tokenrhythm-deepseek-v4-flash"
    assert mapped.ai_essay_review_provider_request_id_header == "x-trace-id"


def test_essay_primary_credentials_are_isolated_from_generic_deepseek_runtime() -> None:
    settings = Settings(
        ai_provider_base_url="https://tokenrhythm.studio/v1",
        ai_api_key="generic-deepseek-key",
        ai_essay_primary_base_url="https://gemini.example.com/v1",
        ai_essay_primary_api_key="gemini-only-key",
        ai_essay_review_model="gemini-3.5-flash",
    )

    mapped = _primary_settings(settings)

    assert mapped.ai_provider_base_url == "https://gemini.example.com/v1"
    assert mapped.ai_api_key == "gemini-only-key"
    assert settings.ai_provider_base_url == "https://tokenrhythm.studio/v1"
    assert settings.ai_api_key == "generic-deepseek-key"


def test_price_contract_requires_tokenrhythm_instead_of_grok() -> None:
    item = {
        "version": "2026-08-02",
        "inputCnyPerMillion": "1",
        "cachedInputCnyPerMillion": "0.2",
        "outputCnyPerMillion": "2",
    }

    prices = _price_cards(
        __import__("json").dumps(
            {
                "tokenrhythm": item,
                "hunyuan": item,
                "adjudicator": item,
            }
        )
    )

    assert set(prices) == {"tokenrhythm", "hunyuan", "adjudicator"}


def test_formal_execution_identity_no_longer_requires_gemini(monkeypatch) -> None:
    item = {
        "version": "2026-08-08",
        "inputCnyPerMillion": "1",
        "cachedInputCnyPerMillion": "0.2",
        "outputCnyPerMillion": "2",
    }
    monkeypatch.setenv(
        "CLOUDBASE_HUNYUAN_RELEASE_ID",
        "hunyuan-cloud-function-2026-08-08.1",
    )
    monkeypatch.setenv("CLOUDBASE_HUNYUAN_MODEL", "hunyuan-2.0-instruct-20251111")
    monkeypatch.setenv(
        "AI_ESSAY_ADJUDICATOR_PROVIDER_RELEASE_ID",
        "tokenrhythm-deepseek-v4-flash-adjudicator",
    )
    monkeypatch.setenv("AI_ESSAY_ADJUDICATOR_MODEL", "deepseek-v4-flash")
    settings = Settings(
        ai_essay_grading_plan_release_id="essay-grading-2model-2026-08-08.1",
        ai_essay_grading_price_cards_json=json.dumps(
            {key: item for key in ("tokenrhythm", "hunyuan", "adjudicator")}
        ),
        ai_essay_tokenrhythm_model="deepseek-v4-flash",
        ai_essay_tokenrhythm_provider_release_id="tokenrhythm-deepseek-v4-flash",
        ai_essay_primary_base_url="",
        ai_essay_primary_api_key="",
        ai_essay_review_provider_release_id="",
    )

    identity = EssayMultiModelRuntimePlan.execution_identity_from_settings(settings)

    assert identity.provider == "essay-multi-model@essay-grading-2model-2026-08-08.1"
    assert identity.model.startswith("plan-sha256:")


def test_checkpoint_contract_reads_grok_history_and_writes_tokenrhythm() -> None:
    assert _CHECKPOINT_PHASE_PATTERN.fullmatch("candidate:grok")
    assert _CHECKPOINT_PHASE_PATTERN.fullmatch("candidate:tokenrhythm")
    assert not _CHECKPOINT_PHASE_PATTERN.fullmatch("candidate:unknown")
