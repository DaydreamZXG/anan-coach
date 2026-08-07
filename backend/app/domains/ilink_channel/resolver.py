from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session
import tos

from app.contracts.external_learning.models import LearningTrack, RecognitionKind
from app.core.config import Settings
from app.domains.ai.provider import AIProviderConfigurationError
from app.domains.external_ingestion.answering.provider import (
    ExternalAIAnswerProvider,
    ExternalAIAnswerProviderError,
)
from app.domains.external_ingestion.matching.authorization import SQLMatchAuthorization
from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.external_ingestion.matching.fingerprint import (
    canonical_fingerprint,
    grouped_text_similarity,
    normalize_text,
    requires_visual_evidence,
    visual_similarity,
)
from app.domains.external_ingestion.matching.models import MatchQuery, MatchingPolicy
from app.domains.external_ingestion.recognition.errors import (
    ProcessorConfigurationError,
    ProcessorResponseError,
)
from app.domains.external_ingestion.recognition.grok_vision import (
    GrokVisionRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.mineru_ocr import (
    MinerURecognitionProcessor,
)
from app.domains.external_ingestion.recognition.modal_paddle import (
    ModalPaddleOCRRecognitionProcessor,
)
from app.domains.external_ingestion.recognition.models import (
    ProcessorApproval,
    ProcessorRequest,
    ProcessorResult,
    provider_replay_key,
)
from app.domains.external_ingestion.recognition.processor import (
    HTTPRecognitionProcessor,
    ProcessorRegistry,
)
from app.domains.ilink_channel.protocol import ValidatedImage
from app.domains.ilink_channel.schemas import AnswerProjection
from app.domains.ilink_channel.settings import ILinkSettings
from app.internal_services.private_objects import PrivateObjectReader, private_object_key


class PhotoResolutionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class StoredImage:
    object_ref: str
    content_sha256: str
    mime_type: str


class TosTransientImageStore:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        required = (
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
            settings.tos_bucket,
        )
        if not all(value.strip() for value in required):
            raise PhotoResolutionError("private_storage_not_configured")
        if not settings.tos_endpoint.startswith("https://"):
            raise PhotoResolutionError("private_storage_requires_https")
        self._bucket = settings.tos_bucket
        self._client = client or tos.TosClientV2(
            settings.tos_access_key,
            settings.tos_secret_key,
            settings.tos_endpoint,
            settings.tos_region,
        )

    def put(
        self,
        *,
        user_id: UUID,
        query_id: UUID,
        image: ValidatedImage,
    ) -> StoredImage:
        digest = sha256(image.payload).hexdigest()
        extension = "jpg" if image.mime_type == "image/jpeg" else "png"
        key = f"private/ilink/{user_id}/{query_id}/{digest}.{extension}"
        try:
            existing = self._client.head_object(self._bucket, key)
        except Exception as exc:
            if _tos_status(exc) != 404:
                raise PhotoResolutionError("private_storage_unavailable", retryable=True) from exc
        else:
            existing_hash = str(getattr(existing, "meta", {}).get("content-sha256", ""))
            if existing_hash != digest:
                raise PhotoResolutionError("private_storage_conflict")
            return StoredImage(
                object_ref=f"tos://{self._bucket}/{key}",
                content_sha256=digest,
                mime_type=image.mime_type,
            )
        try:
            self._client.put_object(
                self._bucket,
                key,
                content_length=len(image.payload),
                content_sha256=digest,
                content_type=image.mime_type,
                acl=tos.ACLType.ACL_Private,
                server_side_encryption="AES256",
                meta={"content-sha256": digest, "purpose": "ilink-transient-query"},
                content=image.payload,
                forbid_overwrite=True,
            )
        except Exception as exc:
            raise PhotoResolutionError("private_storage_unavailable", retryable=True) from exc
        return StoredImage(
            object_ref=f"tos://{self._bucket}/{key}",
            content_sha256=digest,
            mime_type=image.mime_type,
        )

    def delete(self, *, object_ref: str) -> None:
        try:
            key = private_object_key(object_ref, bucket=self._bucket)
            self._client.delete_object(self._bucket, key)
        except Exception as exc:
            if _tos_status(exc) != 404:
                raise PhotoResolutionError("transient_delete_failed", retryable=True) from exc

    def stat(self, *, object_ref: str) -> int:
        try:
            key = private_object_key(object_ref, bucket=self._bucket)
            response = self._client.head_object(self._bucket, key)
        except Exception as exc:
            if _tos_status(exc) == 404:
                raise PhotoResolutionError("material_object_missing") from exc
            raise PhotoResolutionError("private_storage_unavailable", retryable=True) from exc
        size = getattr(response, "content_length", None)
        if not isinstance(size, int) or size < 1:
            raise PhotoResolutionError("material_object_missing")
        return size


class QueryRecognitionProcessor:
    def __init__(self, *, core: Settings, channel: ILinkSettings) -> None:
        if not core.recognition_processor_enabled:
            raise PhotoResolutionError("recognition_not_enabled")
        endpoint = _approved_recognition_endpoint(core)
        try:
            raw = json.loads(core.recognition_processor_approvals_json)
            approvals = tuple(ProcessorApproval.model_validate(item) for item in raw)
        except (TypeError, ValueError, ValidationError) as exc:
            raise PhotoResolutionError("recognition_approval_invalid") from exc
        adapter = core.recognition_processor_adapter.strip().casefold()
        if adapter == "grok_vision_chat_completions":
            processor = GrokVisionRecognitionProcessor(
                endpoint=endpoint,
                bearer_token=core.recognition_processor_token,
                expected_provider_release=core.recognition_processor_release_id,
                requested_model=core.recognition_processor_model,
                expected_actual_model=core.recognition_processor_actual_model,
                source_reader=PrivateObjectReader(core),
                maximum_input_bytes=core.recognition_processor_max_input_bytes,
                maximum_output_bytes=core.recognition_processor_max_output_bytes,
                maximum_pages=core.recognition_processor_max_pages,
            )
        elif adapter == "private_contract":
            processor = HTTPRecognitionProcessor(
                endpoint=endpoint,
                bearer_token=core.recognition_processor_token,
                expected_provider_release=core.recognition_processor_release_id,
            )
        elif adapter == "modal_paddle_ocr":
            if core.recognition_processor_max_pages != 1:
                raise PhotoResolutionError("recognition_page_limit_invalid")
            if any(
                approval.model_release != core.recognition_processor_actual_model.strip()
                for approval in approvals
            ):
                raise PhotoResolutionError("recognition_model_approval_drift")
            processor = ModalPaddleOCRRecognitionProcessor(
                endpoint=endpoint,
                bearer_token=core.recognition_processor_token,
                expected_provider_release=core.recognition_processor_release_id,
                expected_model_release=core.recognition_processor_actual_model,
                source_reader=PrivateObjectReader(core),
                maximum_input_bytes=core.recognition_processor_max_input_bytes,
                maximum_output_bytes=core.recognition_processor_max_output_bytes,
            )
        elif adapter == "mineru_ocr":
            processor = MinerURecognitionProcessor(
                endpoint=endpoint,
                bearer_token=core.recognition_processor_token,
                expected_provider_release=core.recognition_processor_release_id,
                source_reader=PrivateObjectReader(core),
                maximum_input_bytes=core.recognition_processor_max_input_bytes,
                maximum_output_bytes=core.recognition_processor_max_output_bytes,
                maximum_pages=core.recognition_processor_max_pages,
            )
        else:
            raise PhotoResolutionError("recognition_adapter_unsupported")
        self._registry = ProcessorRegistry(
            approvals,
            {approval.route: processor for approval in approvals},
        )
        self._route = channel.recognition_route
        self._release = processor.provider_release
        try:
            for mime_type in ("image/jpeg", "image/png"):
                self._registry.resolve(
                    route=self._route,
                    kind=RecognitionKind.EXAM_SEGMENTATION,
                    mime_type=mime_type,
                    at=datetime.now(UTC),
                )
        except ProcessorConfigurationError as exc:
            raise PhotoResolutionError("recognition_route_not_approved") from exc

    @property
    def provider_release(self) -> str:
        return self._release

    def recognize(
        self,
        *,
        user_id: UUID,
        query_id: UUID,
        image: StoredImage,
        owner_scope_hash: str,
    ) -> ProcessorResult:
        try:
            _, processor = self._registry.resolve(
                route=self._route,
                kind=RecognitionKind.EXAM_SEGMENTATION,
                mime_type=image.mime_type,
                at=datetime.now(UTC),
            )
            return processor.process(
                ProcessorRequest(
                    artifactVersionId=query_id,
                    ownerScopeHash=owner_scope_hash,
                    objectRef=image.object_ref,
                    mimeType=image.mime_type,
                    inputSha256=image.content_sha256,
                    pageCount=1,
                    recognitionKind="exam_segmentation",
                    idempotencyKey=provider_replay_key(
                        owner_user_id=user_id,
                        operation="ilink_recognize",
                        command_id=query_id,
                    ),
                )
            )
        except ProcessorConfigurationError as exc:
            # 配置错误是确定性失败（处理器未批准/参数非法），重试不会成功。
            raise PhotoResolutionError("recognition_failed", retryable=False) from exc
        except ProcessorResponseError as exc:
            # 上游响应错误（5xx/超时/结果不可用）多为瞬态，允许重试。
            raise PhotoResolutionError("recognition_failed", retryable=True) from exc


class PhotoQuestionResolver:
    def __init__(
        self,
        *,
        core_settings: Settings,
        channel_settings: ILinkSettings,
        storage: TosTransientImageStore | None = None,
        recognizer: QueryRecognitionProcessor | None = None,
        primary: ExternalAIAnswerProvider | None = None,
    ) -> None:
        self._core = core_settings
        self._channel = channel_settings
        self.storage = storage or TosTransientImageStore(core_settings)
        self._recognizer = recognizer or QueryRecognitionProcessor(
            core=core_settings, channel=channel_settings
        )
        # Matching an authorized question does not use the answer model.
        # Keep this dependency lazy so a model outage or an unapproved
        # release cannot take the whole iLink inbox (binding, plan queries and
        # authorized question-bank answers) offline.
        self._primary = primary

    def resolve(
        self,
        session: Session,
        *,
        user_id: UUID,
        query_id: UUID,
        stored: StoredImage,
        owner_scope_hash: str,
    ) -> AnswerProjection:
        recognition = self._recognizer.recognize(
            user_id=user_id,
            query_id=query_id,
            image=stored,
            owner_scope_hash=owner_scope_hash,
        )
        query, recognized = _match_query(recognition)
        policy = MatchingPolicy(
            policyVersion=self._core.matching_policy_version,
            similarThreshold=self._core.matching_similar_threshold,
            ambiguityMargin=self._core.matching_ambiguity_margin,
            textWeight=self._core.matching_text_weight,
            visualWeight=self._core.matching_visual_weight,
            essayMinPromptChars=self._core.matching_essay_min_prompt_chars,
            essayRequireMaterial=self._core.matching_essay_require_material,
            maximumCandidates=self._core.matching_maximum_candidates,
        )
        if query.track == LearningTrack.ESSAY and (
            len(normalize_text(query.prompt_text)) < policy.essay_min_prompt_chars
            or (policy.essay_require_material and not query.material_texts)
        ):
            return AnswerProjection(
                answerKind="material_required",
                answerText="请补充完整共享材料",
                explanation="这是一道依赖材料的长题，当前图片不足以形成可靠答案。",
                track=query.track.value,
                recognizedPayload=recognized,
            )
        candidate, ambiguity = _authorized_candidate(
            session=session,
            owner_user_id=user_id,
            query=query,
            policy=policy,
            core=self._core,
        )
        if ambiguity:
            return AnswerProjection(
                answerKind="ambiguous",
                answerText="当前无法唯一确定原题",
                explanation="题库中有多个相近候选，请补拍题号、材料标题或完整选项。",
                track=query.track.value,
                recognizedPayload=recognized,
            )
        if candidate is not None:
            content, match_kind, snapshot = candidate
            if not content.reference_answer:
                raise PhotoResolutionError("matched_answer_missing")
            reference_decision = SQLMatchAuthorization(session).authorize(
                owner_user_id=user_id,
                content=content,
                purpose="reference_answer",
            )
            if not reference_decision.allowed or not reference_decision.snapshot_ref:
                raise PhotoResolutionError("matched_answer_not_authorized")
            return AnswerProjection(
                answerKind=("authorized_exact" if match_kind == "exact" else "similar_reference"),
                answerText=content.reference_answer,
                explanation=(
                    content.solution_explanation
                    or ("原题已精确命中。" if match_kind == "exact" else "仅提供相似题参考。")
                ),
                track=query.track.value,
                recognizedPayload=recognized,
                contentVersionId=content.content_version_id,
                authorizationSnapshotRef=reference_decision.snapshot_ref,
                modelProvenance=None,
            )
        return self._single_ai(query=query, recognized=recognized, query_id=query_id)

    def _single_ai(
        self, *, query: MatchQuery, recognized: dict[str, object], query_id: UUID
    ) -> AnswerProjection:
        try:
            primary_provider = self._primary or ExternalAIAnswerProvider(self._core)
            primary_provider.validate_configuration()
        except (
            AIProviderConfigurationError,
            ExternalAIAnswerProviderError,
        ):
            return AnswerProjection(
                answerKind="ambiguous",
                answerText="这道题暂时没有可核验的答案",
                explanation=(
                    "题面已经识别，但没有命中你有权查看的题库版本，"
                    "当前 AI 答题模型尚未通过运行校验。"
                ),
                track=query.track.value,
                recognizedPayload=recognized,
                modelProvenance=None,
            )
        self._primary = primary_provider
        try:
            primary = primary_provider.generate(
                query=query,
                source_segment_refs=tuple(str(item) for item in recognized["segmentRefs"]),
                idempotency_key=f"ilink-primary-answer:{query_id}",
            )
        except ExternalAIAnswerProviderError as exc:
            raise PhotoResolutionError(exc.code, retryable=exc.retryable) from exc
        return AnswerProjection(
            answerKind="ai_candidate",
            answerText=primary.output.answer,
            explanation=(
                primary.output.explanation
                + "\n\n这是单模型候选结论，不是官方答案。"
            ),
            track=query.track.value,
            recognizedPayload=recognized,
            modelProvenance={
                "policy": "single-primary-candidate@v1",
                "primary": {
                    "provider": primary_provider.provider_name,
                    "model": primary_provider.model,
                    "outputFingerprint": primary.output_fingerprint,
                    "requestFingerprint": primary.provider_request_id_fingerprint,
                },
            },
        )

def _match_query(result: ProcessorResult) -> tuple[MatchQuery, dict[str, object]]:
    spans = {span.span_id: span.text.strip() for span in result.text_spans}
    segments: list[dict[str, object]] = []
    prompts: list[str] = []
    options: list[str] = []
    materials: list[str] = []
    prompt_count = 0
    essay_prompt = False
    refs: list[str] = []
    for segment in result.segments:
        value = "\n".join(
            spans[ref] for ref in segment.text_span_refs if ref in spans and spans[ref]
        ).strip()
        if not value:
            continue
        refs.append(segment.segment_id)
        segments.append(
            {
                "segmentId": segment.segment_id,
                "segmentKind": segment.segment_kind,
                "text": value,
                "confidence": segment.confidence,
            }
        )
        if segment.segment_kind in {"question", "essay_prompt"}:
            prompt_count += 1
            prompts.append(value)
            essay_prompt = essay_prompt or segment.segment_kind == "essay_prompt"
        elif segment.segment_kind == "options":
            options.append(value)
        elif segment.segment_kind == "material":
            materials.append(value)
    if prompt_count == 0:
        raise PhotoResolutionError("question_prompt_missing")
    if prompt_count > 1:
        raise PhotoResolutionError("multiple_questions_detected")
    track = LearningTrack.ESSAY if essay_prompt else LearningTrack.XINGCE
    query = MatchQuery(
        track=track,
        promptText="\n".join(prompts),
        optionTexts=tuple(options),
        materialTexts=tuple(materials),
        visualFingerprints=result.visual_fingerprints,
        visualFeatureHashes=result.visual_feature_hashes,
    )
    return query, {
        "contractVersion": "ilink-query-recognition@v1",
        "track": track.value,
        "segments": segments,
        "segmentRefs": refs,
        "recognitionFingerprint": canonical_fingerprint(
            result.model_dump(by_alias=True, mode="json")
        ),
    }


def _authorized_candidate(
    *,
    session: Session,
    owner_user_id: UUID,
    query: MatchQuery,
    policy: MatchingPolicy,
    core: Settings,
):
    catalog = SQLQuestionContentCatalog(
        session,
        sql_prefilter_threshold=core.matching_sql_prefilter_threshold,
        prefetch_factor=core.matching_sql_prefetch_factor,
        maximum_prefetch=core.matching_sql_maximum_prefetch,
    )
    authorization = SQLMatchAuthorization(session)
    scored = []
    for content in catalog.search(query=query, maximum_candidates=policy.maximum_candidates):
        if content.track != query.track:
            continue
        if (
            query.visual_feature_hashes
            and requires_visual_evidence(query.prompt_text)
            and not content.visual_feature_hashes
        ):
            continue
        if (
            content.distribution_class == "account_private"
            and content.owner_user_id != owner_user_id
        ):
            continue
        purpose = (
            "owner_private_match"
            if content.distribution_class == "account_private"
            else "cross_user_match"
        )
        decision = authorization.authorize(
            owner_user_id=owner_user_id, content=content, purpose=purpose
        )
        if not decision.allowed or not decision.snapshot_ref:
            continue
        text_score = grouped_text_similarity(
            query_prompt=query.prompt_text,
            content_prompt=content.prompt_text,
            query_options=query.option_texts,
            content_options=content.option_texts,
            query_materials=query.material_texts,
            content_materials=content.material_texts,
        )
        visual_score = visual_similarity(
            query.visual_feature_hashes, content.visual_feature_hashes
        )
        has_visual = bool(
            query.visual_feature_hashes and content.visual_feature_hashes
        )
        score = round(
            text_score * policy.text_weight + visual_score * policy.visual_weight
            if has_visual
            else text_score,
            12,
        )
        exact = text_score == 1.0 and (not has_visual or visual_score == 1.0)
        if exact or score >= policy.similar_threshold:
            scored.append(
                (
                    score if not exact else 1.0,
                    content.content_version_id,
                    content,
                    "exact" if exact else "similar",
                    decision.snapshot_ref,
                )
            )
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return None, False
    if len(scored) > 1 and scored[0][0] - scored[1][0] <= policy.ambiguity_margin:
        return None, True
    _, _, content, kind, snapshot = scored[0]
    return (content, kind, snapshot), False


def _approved_recognition_endpoint(settings: Settings) -> str:
    parsed = urlsplit(settings.recognition_processor_endpoint)
    allowed = {
        item.strip().casefold()
        for item in settings.recognition_processor_https_host_allowlist.split(",")
        if item.strip()
    }
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.hostname.casefold() not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PhotoResolutionError("recognition_endpoint_not_approved")
    return settings.recognition_processor_endpoint.rstrip("/")


def _tos_status(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    return int(value) if isinstance(value, int) else None
