from __future__ import annotations

import base64
import json
from typing import cast
from uuid import UUID

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
    QueryRecognitionProcessor,
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


def test_answer_model_is_lazy_and_an_unapproved_model_abstains() -> None:
    resolver = PhotoQuestionResolver(
        core_settings=Settings(),
        channel_settings=_channel_settings(),
        storage=cast(TosTransientImageStore, object()),
        recognizer=cast(QueryRecognitionProcessor, object()),
    )

    result = resolver._single_ai(
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


def test_single_primary_model_returns_an_honestly_labelled_candidate() -> None:
    resolver = PhotoQuestionResolver(
        core_settings=Settings(),
        channel_settings=_channel_settings(),
        storage=cast(TosTransientImageStore, object()),
        recognizer=cast(QueryRecognitionProcessor, object()),
        primary=cast(object, _PrimaryAnswerProvider()),  # type: ignore[arg-type]
    )

    result = resolver._single_ai(
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
    assert result.model_provenance["policy"] == "single-primary-candidate@v1"
    assert "secondary" not in result.model_provenance


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


def test_visual_question_matches_by_stem_and_local_figure_not_page_chrome(
    monkeypatch,
) -> None:
    query_feature = VisualFeatureHash(
        phash="0" * 16,
        dhash="0" * 16,
        ahash="0" * 16,
        edgeDhash="0" * 16,
    )
    catalog_feature = VisualFeatureHash(
        phash="0" * 15 + "1",
        dhash="0" * 15 + "1",
        ahash="0" * 15 + "1",
        edgeDhash="0" * 15 + "1",
    )
    prompt = "左边为给定的纸盒外表面，右边哪一项能由它折叠而成？"
    query = MatchQuery(
        track="xingce",
        promptText=prompt,
        optionTexts=("A A\nB C D\nA B\nC C\nD D",),
        materialTexts=("00:03\n专项智能练习（图形推理）\n单选题",),
        visualFeatureHashes=(query_feature,),
    )
    text_only = IndexedContent(
        contentVersionId="text-only-duplicate",
        track="xingce",
        promptText=prompt,
        referenceAnswer="B",
        distributionClass="licensed_transferable",
        crossUserMatchAllowed=True,
    )
    visual = IndexedContent(
        contentVersionId="visual-source",
        track="xingce",
        promptText=prompt,
        visualFeatureHashes=(catalog_feature,),
        referenceAnswer="B",
        distributionClass="licensed_transferable",
        crossUserMatchAllowed=True,
    )

    class _Catalog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def search(self, **_kwargs):
            return (text_only, visual)

    class _Authorization:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def authorize(self, **_kwargs):
            return AuthorizationDecision(
                allowed=True,
                reason="licensed content",
                snapshotRef="snapshot-visual",
            )

    monkeypatch.setattr(
        "app.domains.ilink_channel.resolver.SQLQuestionContentCatalog", _Catalog
    )
    monkeypatch.setattr(
        "app.domains.ilink_channel.resolver.SQLMatchAuthorization", _Authorization
    )

    candidate, ambiguous = _authorized_candidate(
        session=cast(Session, object()),
        owner_user_id=UUID("00000000-0000-0000-0000-000000009305"),
        query=query,
        policy=MatchingPolicy(
            policyVersion="matching-v1",
            similarThreshold=0.86,
            ambiguityMargin=0.05,
            textWeight=0.75,
            visualWeight=0.25,
            essayMinPromptChars=20,
            essayRequireMaterial=True,
            maximumCandidates=5,
        ),
        core=Settings(),
    )

    assert ambiguous is False
    assert candidate is not None
    assert candidate[0].content_version_id == "visual-source"


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
