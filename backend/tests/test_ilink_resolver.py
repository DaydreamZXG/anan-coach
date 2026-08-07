from __future__ import annotations

import base64
import json
from typing import cast
from uuid import UUID

from app.contracts.external_learning.models import LearningTrack
from app.core.config import Settings
from app.domains.external_ingestion.matching.models import MatchQuery
from app.domains.external_ingestion.answering.models import (
    ExternalAIAnswerPayload,
    ExternalAIAnswerProviderResult,
)
from app.domains.ilink_channel.resolver import (
    PhotoQuestionResolver,
    PhotoResolutionError,
    QueryRecognitionProcessor,
    TosTransientImageStore,
)
from app.domains.ilink_channel.settings import ILinkSettings


def _channel_settings() -> ILinkSettings:
    return ILinkSettings(
        enabled=True,
        bot_account_id="anan-bot-1",
        bot_token="official-token-secret",
        base_url="https://ilinkai.weixin.qq.com",
        cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        app_id="bot",
        app_client_version=132102,
        bot_agent="Anan/1.0",
        identity_key_id="ilink-key-v1",
        identity_encryption_key=base64.b64encode(b"e" * 32).decode(),
        identity_hmac_key="h" * 40,
        binding_ttl_seconds=600,
        long_poll_timeout_seconds=35,
        request_timeout_seconds=15,
        maximum_image_bytes=10 * 1024 * 1024,
        transient_retention_seconds=900,
        query_retention_days=30,
        recognition_route="photo-query-approved-v1",
    )


def test_answer_models_are_lazy_and_unapproved_models_abstain() -> None:
    resolver = PhotoQuestionResolver(
        core_settings=Settings(),
        channel_settings=_channel_settings(),
        storage=cast(TosTransientImageStore, object()),
        recognizer=cast(QueryRecognitionProcessor, object()),
    )

    result = resolver._dual_ai(
        query=MatchQuery(
            track=LearningTrack.XINGCE,
            promptText="根据材料，正确选项是哪一个？",
            optionTexts=("A. 甲", "B. 乙"),
        ),
        recognized={"segmentRefs": ["segment-1"]},
        query_id=UUID("00000000-0000-0000-0000-000000009301"),
    )

    assert result.answer_kind == "ambiguous"
    assert result.answer_text == "这道题暂时没有可核验的答案"
    assert result.model_provenance is None


class _PrimaryAnswerProvider:
    provider_name = "primary-test"
    model = "primary-model"

    def validate_configuration(self) -> None:
        return None

    def generate(self, **_kwargs) -> ExternalAIAnswerProviderResult:
        return ExternalAIAnswerProviderResult(
            output=ExternalAIAnswerPayload(
                answer="C",
                explanation="甲与乙的对应关系支持 C。",
                keyPoints=["对应关系"],
                confidence="medium",
                limitations=[],
                sourceSegmentRefs=["segment-1"],
            ),
            usageInputTokens=10,
            usageCachedInputTokens=0,
            usageOutputTokens=5,
            latencyMs=20,
            outputFingerprint="a" * 64,
            providerRequestIdFingerprint="b" * 64,
        )


class _UnavailableSecondaryProvider:
    def validate(self) -> None:
        return None

    def generate(self, **_kwargs):
        raise PhotoResolutionError("secondary_answer_failed")


def test_secondary_outage_keeps_an_honestly_labelled_primary_candidate() -> None:
    resolver = PhotoQuestionResolver(
        core_settings=Settings(),
        channel_settings=_channel_settings(),
        storage=cast(TosTransientImageStore, object()),
        recognizer=cast(QueryRecognitionProcessor, object()),
        primary=cast(object, _PrimaryAnswerProvider()),  # type: ignore[arg-type]
        secondary=cast(object, _UnavailableSecondaryProvider()),  # type: ignore[arg-type]
    )

    result = resolver._dual_ai(
        query=MatchQuery(
            track=LearningTrack.XINGCE,
            promptText="根据材料，正确选项是哪一个？",
            optionTexts=("A. 甲", "B. 乙", "C. 丙"),
        ),
        recognized={"segmentRefs": ["segment-1"]},
        query_id=UUID("00000000-0000-0000-0000-000000009302"),
    )

    assert result.answer_kind == "ai_candidate"
    assert result.answer_text == "C"
    assert "单模型候选结论" in result.explanation
    assert result.model_provenance is not None
    assert (
        result.model_provenance["policy"]
        == "primary-candidate-secondary-unavailable@v1"
    )


def test_ilink_recognition_accepts_the_shared_mineru_adapter() -> None:
    route = "photo-query-approved-v1"
    release = "mineru-staging-20260802"
    approval = {
        "policyVersion": "mineru-policy-v1",
        "route": route,
        "provider": "mineru",
        "providerRelease": release,
        "modelRelease": "mineru-vlm",
        "promptVersion": "mineru-exam-segmentation-v1",
        "priceVersion": "mineru-community-v1",
        "billingUnit": "page",
        "unitPriceCny": "0",
        "approvedKinds": ["exam_segmentation"],
        "approvedMimeTypes": ["image/jpeg", "image/png"],
        "dataRegion": "operator_selected_external",
        "dpaEvidenceRef": "operator-approved-mineru-staging",
        "securityReviewRef": "mineru-staging-review",
        "approvalReceiptRef": "mineru-staging-20260802",
        "validFrom": "2026-08-01T00:00:00Z",
        "validUntil": "2027-08-01T00:00:00Z",
    }
    processor = QueryRecognitionProcessor(
        core=Settings(
            recognition_processor_enabled=True,
            recognition_processor_endpoint="https://ocr.eo.mk/api/v1",
            recognition_processor_token="mineru-test-token",
            recognition_processor_release_id=release,
            recognition_processor_adapter="mineru_ocr",
            recognition_processor_model="mineru-vlm",
            recognition_processor_actual_model="mineru-vlm",
            recognition_processor_max_input_bytes=10 * 1024 * 1024,
            recognition_processor_max_output_bytes=4 * 1024 * 1024,
            recognition_processor_max_pages=1,
            recognition_processor_https_host_allowlist="ocr.eo.mk",
            recognition_processor_approvals_json=json.dumps([approval]),
            tos_access_key="test-access-key",
            tos_secret_key="test-secret-key",
            tos_endpoint="https://tos-cn-guangzhou.ivolces.com",
            tos_region="cn-guangzhou",
            tos_bucket="test-private-bucket",
        ),
        channel=_channel_settings(),
    )

    assert processor.provider_release == release
