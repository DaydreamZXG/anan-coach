from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import CandidateState, LearningTrack
from app.core.config import Settings
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.external_ingestion.answering.models import ExternalAIAnswerVersion
from app.domains.external_ingestion.answering.provider import ExternalAIAnswerProvider
from app.domains.external_ingestion.answering.repository import ExternalAIAnswerRepository
from app.domains.external_ingestion.recognition.models import RecognitionCandidatePayload
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.upload.repository import PostgresUploadRepository
from app.platform.jobs import BackgroundJobRepository


class ExternalAIAnswerError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ExternalAIAnswerService:
    JOB_TYPE = "external_question_answer_v1"
    INPUT_VERSION = "external-question-answer-job@v1"

    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        provider: ExternalAIAnswerProvider | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._provider = provider or ExternalAIAnswerProvider(settings)
        self._repository = ExternalAIAnswerRepository(session)
        self._recognitions = SQLRecognitionRepository(session)
        self._uploads = PostgresUploadRepository(session=session)
        self._jobs = BackgroundJobRepository()

    def request(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        question_unit_id: str,
        learning_track: LearningTrack,
        idempotency_key: str,
    ) -> ExternalAIAnswerVersion:
        self._provider.validate_configuration()
        upload = self._uploads.session(session_id=import_id)
        if upload is None or upload.owner_user_id != owner_user_id:
            raise ExternalAIAnswerError("import_not_found", "导入任务不存在。", status_code=404)
        recognition = self._recognitions.head(
            artifact_version_id=upload.artifact_version_id,
            owner_user_id=owner_user_id,
            recognition_kind="exam_segmentation",
        )
        if recognition is None or recognition.state != CandidateState.CONFIRMED:
            raise ExternalAIAnswerError(
                "recognition_not_confirmed", "请先确认题面识别结果。"
            )
        payload = RecognitionCandidatePayload.model_validate(recognition.candidate_payload)
        unit = next(
            (item for item in payload.question_units if item.question_unit_id == question_unit_id),
            None,
        )
        if unit is None:
            raise ExternalAIAnswerError("question_unit_not_found", "题目分区不存在。", status_code=404)
        match = self._current_match(
            recognition_revision_id=recognition.recognition_revision_id,
            question_unit_id=question_unit_id,
        )
        if match is None or match["evaluation_reason"] != "no_eligible_candidate":
            raise ExternalAIAnswerError(
                "ai_answer_not_unmatched",
                "只有题库未命中的题目才能申请 AI 估算解析。",
            )
        query_payload = _unit_query_payload(payload=payload, unit=unit, track=learning_track)
        input_fingerprint = _fingerprint(
            {
                "recognitionOutputSha256": recognition.output_sha256,
                "questionUnitId": question_unit_id,
                "query": query_payload,
                "provider": self._provider.provider_name,
                "model": self._provider.model,
                "promptVersion": self._provider.prompt_version,
                "schemaVersion": self._provider.schema_version,
            }
        )
        answer_id = uuid5(
            NAMESPACE_URL,
            f"external-ai-answer:{owner_user_id}:{recognition.recognition_revision_id}:{question_unit_id}",
        )
        self._repository.lock(answer_id=answer_id)
        existing = self._repository.head(answer_id=answer_id)
        if existing is not None:
            if existing.input_fingerprint != input_fingerprint:
                raise ExternalAIAnswerError(
                    "ai_answer_input_changed", "题面版本已变化，请重新识别后再申请。"
                )
            return existing
        reservation = self._commerce().reserve_feature_usage(
            user_id=str(owner_user_id),
            feature="photo_explain",
            reservation_key=f"external-answer:{answer_id}",
            business_key=f"external-answer:{answer_id}",
            server_session_ref=f"external-import:{import_id}",
            commit=False,
        )
        job = self._jobs.enqueue(
            self._session,
            job_key=f"external-answer:{answer_id}",
            job_type=self.JOB_TYPE,
            input_version=self.INPUT_VERSION,
            payload={
                "answerId": str(answer_id),
                "ownerUserId": str(owner_user_id),
                "importId": str(import_id),
                "recognitionRevisionId": str(recognition.recognition_revision_id),
                "questionUnitId": question_unit_id,
                "learningTrack": learning_track.value,
                "inputFingerprint": input_fingerprint,
                "query": query_payload,
                "sourceSegmentRefs": [
                    *unit.material_segment_refs,
                    *unit.prompt_segment_refs,
                    *unit.option_segment_refs,
                ],
                "reservationId": str(reservation.reservation_id),
                "reservationSource": reservation.source,
                "reservationKey": reservation.reservation_key,
            },
            max_attempts=3,
        )
        self._commerce().bind_feature_usage_reservation_job(
            reservation=reservation, job_id=job.job_id, commit=False
        )
        version = ExternalAIAnswerVersion(
            answerVersionId=uuid5(NAMESPACE_URL, f"external-answer-request:{idempotency_key}:{answer_id}"),
            answerId=answer_id,
            importId=import_id,
            artifactVersionId=recognition.artifact_version_id,
            recognitionRevisionId=recognition.recognition_revision_id,
            questionUnitId=question_unit_id,
            ownerUserId=owner_user_id,
            versionNo=1,
            state="requested",
            learningTrack=learning_track,
            inputFingerprint=input_fingerprint,
            provider=self._provider.provider_name,
            model=self._provider.model,
            promptVersion=self._provider.prompt_version,
            schemaVersion=self._provider.schema_version,
            reservationId=reservation.reservation_id,
            reservationSource=reservation.source,
            jobId=job.job_id,
            createdAt=datetime.now(UTC),
        )
        self._repository.insert(version)
        return version

    def confirm(
        self,
        *,
        owner_user_id: UUID,
        answer_version_id: UUID,
        expected_output_fingerprint: str,
        idempotency_key: str,
    ) -> ExternalAIAnswerVersion:
        source = self._repository.by_version(answer_version_id=answer_version_id)
        if source is None or source.owner_user_id != owner_user_id:
            raise ExternalAIAnswerError("ai_answer_not_found", "AI 解析不存在。", status_code=404)
        if source.state == "confirmed":
            return source
        if source.state != "candidate" or source.output_fingerprint != expected_output_fingerprint:
            raise ExternalAIAnswerError("ai_answer_version_changed", "AI 解析版本已变化。")
        self._repository.lock(answer_id=source.answer_id)
        head = self._repository.head(answer_id=source.answer_id, for_update=True)
        if head is None or head.answer_version_id != source.answer_version_id:
            raise ExternalAIAnswerError("ai_answer_version_changed", "AI 解析版本已变化。")
        confirmed = source.model_copy(
            update={
                "answer_version_id": uuid5(
                    NAMESPACE_URL, f"external-answer-confirm:{owner_user_id}:{idempotency_key}"
                ),
                "version_no": source.version_no + 1,
                "state": "confirmed",
                "supersedes_answer_version_id": source.answer_version_id,
                "created_at": datetime.now(UTC),
            }
        )
        self._repository.insert(confirmed)
        return confirmed

    def _current_match(self, *, recognition_revision_id: UUID, question_unit_id: str):
        return self._session.execute(
            text(
                """
                select evaluation_reason, state
                from learning.content_match_versions
                where recognition_revision_id = :recognition_revision_id
                  and question_unit_id = :question_unit_id
                order by version_no desc limit 1
                """
            ),
            {
                "recognition_revision_id": str(recognition_revision_id),
                "question_unit_id": question_unit_id,
            },
        ).mappings().one_or_none()

    def _commerce(self) -> CommerceService:
        return CommerceService(
            repository=CommerceRepository(self._session),
            app_store_verifier=AppStoreSignedTransactionVerifier(self._settings),
        )


def _unit_query_payload(*, payload, unit, track: LearningTrack) -> dict[str, object]:
    spans = {item.span_id: item.text for item in payload.text_spans}
    segments = {item.segment_id: item for item in payload.segments}

    def values(refs: tuple[str, ...]) -> list[str]:
        result = []
        for ref in refs:
            segment = segments[ref]
            value = "\n".join(
                spans[span_ref] for span_ref in segment.text_span_refs if span_ref in spans
            ).strip()
            if value:
                result.append(value)
        return result

    prompts = values(unit.prompt_segment_refs)
    prompt = "\n".join(prompts).strip()
    if not prompt:
        raise ExternalAIAnswerError("question_prompt_empty", "题干为空，请先修正识别结果。")
    materials = values(unit.material_segment_refs)
    if track == LearningTrack.ESSAY and not materials:
        raise ExternalAIAnswerError("essay_material_incomplete", "申论材料不完整，不能生成可靠解析。")
    return {
        "track": track.value,
        "promptText": prompt,
        "optionTexts": values(unit.option_segment_refs),
        "materialTexts": materials,
        "visualFingerprints": list(payload.visual_fingerprints),
    }


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
