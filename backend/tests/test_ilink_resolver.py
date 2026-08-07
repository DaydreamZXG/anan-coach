from __future__ import annotations

import base64
import json
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import LearningTrack
from app.core.config import Settings
from app.domains.external_ingestion.answering.models import (
    ExternalAIAnswerPayload,
    ExternalAIAnswerProviderResult,
)
from app.domains.external_ingestion.matching.models import (
    AuthorizationDecision,
    IndexedContent,
    MatchingPolicy,
    MatchQuery,
)
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    ProcessorResult,
    TextSpan,
    VisualFeatureHash,
)
from app.domains.ilink_channel.resolver import (
    PhotoQuestionResolver,
    PhotoResolutionError,
    QueryRecognitionProcessor,
    SecondaryAnswerProvider,
    TosTransientImageStore,
    _authorized_candidate,
    _match_query,
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


class _DisagreeingSecondaryProvider:
    def validate(self) -> None:
        return None

    def generate(self, **_kwargs):
        from app.domains.ilink_channel.resolver import SecondaryAnswer

        return SecondaryAnswer(
            answer="A",
            explanation="备用模型选择 A。",
            output_fingerprint="c" * 64,
            request_fingerprint="d" * 64,
            provider_release="secondary-test",
            model="secondary-model",
        )


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


def test_secondary_disagreement_cannot_suppress_the_primary_candidate() -> None:
    resolver = PhotoQuestionResolver(
        core_settings=Settings(),
        channel_settings=_channel_settings(),
        storage=cast(TosTransientImageStore, object()),
        recognizer=cast(QueryRecognitionProcessor, object()),
        primary=cast(object, _PrimaryAnswerProvider()),  # type: ignore[arg-type]
        secondary=cast(object, _DisagreeingSecondaryProvider()),  # type: ignore[arg-type]
    )

    result = resolver._dual_ai(
        query=MatchQuery(
            track=LearningTrack.XINGCE,
            promptText="根据材料，正确选项是哪一个？",
            optionTexts=("A. 甲", "B. 乙", "C. 丙"),
        ),
        recognized={"segmentRefs": ["segment-1"]},
        query_id=UUID("00000000-0000-0000-0000-000000009304"),
    )

    assert result.answer_kind == "ai_candidate"
    assert result.answer_text == "C"
    assert "复核分歧" in result.explanation
    assert result.model_provenance is not None
    assert (
        result.model_provenance["policy"]
        == "primary-candidate-secondary-disagreed@v1"
    )


def test_secondary_provider_requests_and_accepts_the_complete_answer_contract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ILINK_SECONDARY_AI_BASE_URL", "https://secondary.example/v1")
    monkeypatch.setenv("ILINK_SECONDARY_AI_API_KEY", "secondary-test-key")
    monkeypatch.setenv("ILINK_SECONDARY_AI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ILINK_SECONDARY_AI_PROVIDER_RELEASE_ID", "secondary-release-v1")
    monkeypatch.setenv(
        "ILINK_SECONDARY_AI_HTTPS_HOST_ALLOWLIST", "secondary.example"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        document = json.loads(body["messages"][1]["content"])
        assert set(document["requiredSchema"]) == {
            "answer",
            "explanation",
            "keyPoints",
            "confidence",
            "limitations",
        }
        assert "requiredSchema" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            headers={"x-request-id": "secondary-request-1"},
            json={
                "id": "completion-1",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "B",
                                    "explanation": "1+1=2。",
                                    "keyPoints": ["基本加法"],
                                    "confidence": "high",
                                    "limitations": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
            },
        )

    provider = SecondaryAnswerProvider(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler), **kwargs
        )
    )
    result = provider.generate(
        query=MatchQuery(
            track=LearningTrack.XINGCE,
            promptText="1+1=?",
            optionTexts=("A. 1", "B. 2"),
        ),
        idempotency_key="secondary-contract-1",
    )

    assert result.answer == "B"
    assert result.explanation == "1+1=2。"


def test_photo_matching_uses_structured_visual_hashes(monkeypatch) -> None:
    feature = VisualFeatureHash(
        phash="1" * 16,
        dhash="2" * 16,
        ahash="3" * 16,
        edgeDhash="4" * 16,
    )
    box = BoundingBox(pageNo=1, x=0, y=0, width=1, height=1)
    query, _ = _match_query(
        ProcessorResult(
            providerRequestId="recognition-1",
            textSpans=[
                TextSpan(spanId="span-1", text="选出正确图形", box=box, confidence=0.9)
            ],
            segments=[
                PageSegment(
                    segmentId="segment-1",
                    segmentKind="question",
                    box=box,
                    textSpanRefs=["span-1"],
                    confidence=0.9,
                )
            ],
            visualFingerprints=["f" * 64],
            visualFeatureHashes=[feature],
            confidence=0.9,
        )
    )
    assert query.visual_fingerprints == ("f" * 64,)
    assert query.visual_feature_hashes == (feature,)

    content = IndexedContent(
        contentVersionId="content-1",
        track="xingce",
        promptText="选出正确图形",
        visualFingerprints=["f" * 64],
        visualFeatureHashes=[feature],
        referenceAnswer="A",
        distributionClass="licensed_transferable",
        authorizationSnapshotRef="snapshot-1",
        crossUserMatchAllowed=True,
    )

    class _Catalog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def search(self, **_kwargs):
            return (content,)

    class _Authorization:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def authorize(self, **_kwargs):
            return AuthorizationDecision(
                allowed=True,
                reason="licensed content",
                snapshotRef="snapshot-1",
            )

    monkeypatch.setattr(
        "app.domains.ilink_channel.resolver.SQLQuestionContentCatalog", _Catalog
    )
    monkeypatch.setattr(
        "app.domains.ilink_channel.resolver.SQLMatchAuthorization", _Authorization
    )
    candidate, ambiguous = _authorized_candidate(
        session=cast(Session, object()),
        owner_user_id=UUID("00000000-0000-0000-0000-000000009303"),
        query=query,
        policy=MatchingPolicy(
            policyVersion="matching-v1",
            similarThreshold=0.8,
            ambiguityMargin=0.05,
            textWeight=0.7,
            visualWeight=0.3,
            essayMinPromptChars=20,
            essayRequireMaterial=True,
            maximumCandidates=5,
        ),
        core=Settings(),
    )

    assert ambiguous is False
    assert candidate is not None
    assert candidate[0].content_version_id == "content-1"


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
