from __future__ import annotations

from app.core.config import Settings, get_settings
from app.domains.essay_intelligence.multi_model_runtime import (
    _price_cards,
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
                "gemini": item,
                "tokenrhythm": item,
                "hunyuan": item,
                "adjudicator": item,
            }
        )
    )

    assert set(prices) == {"gemini", "tokenrhythm", "hunyuan", "adjudicator"}


def test_checkpoint_contract_reads_grok_history_and_writes_tokenrhythm() -> None:
    assert _CHECKPOINT_PHASE_PATTERN.fullmatch("candidate:grok")
    assert _CHECKPOINT_PHASE_PATTERN.fullmatch("candidate:tokenrhythm")
    assert not _CHECKPOINT_PHASE_PATTERN.fullmatch("candidate:unknown")
