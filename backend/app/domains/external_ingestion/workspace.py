from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    CandidateState,
    ContentMatchVersion,
    LearningTrack,
    RecognitionKind,
    RecognitionRevision,
)
from app.domains.external_ingestion.answering.models import ExternalAIAnswerVersion
from app.domains.external_ingestion.matching.models import MatchQuery
from app.domains.external_ingestion.answering.repository import ExternalAIAnswerRepository
from app.domains.external_ingestion.matching.errors import MatchingAuthorizationError
from app.domains.external_ingestion.matching.repository import SQLContentMatchRepository
from app.domains.external_ingestion.matching.service import MatchingService
from app.domains.external_ingestion.recognition.models import (
    RecognitionCandidatePayload,
    TextSpan,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition.service import RecognitionService
from app.domains.external_ingestion.schemas import (
    ConfirmImportMatchCommand,
    CorrectImportRecognitionCommand,
    ImportRecognitionSegment,
    ImportAIAnswer,
    ImportMatchedContent,
    ImportQuestionUnit,
    ImportReferenceAnswer,
    ImportWorkspace,
)
from app.domains.external_ingestion.upload.repository import PostgresUploadRepository


class ExternalImportError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ExternalImportWorkspaceService:
    def __init__(
        self,
        *,
        session: Session,
        recognition_service: RecognitionService,
        matching_service: MatchingService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._recognitions = recognition_service
        self._matching = matching_service
        self._recognition_repository = SQLRecognitionRepository(session)
        self._match_repository = SQLContentMatchRepository(session)
        self._answer_repository = ExternalAIAnswerRepository(session)
        self._uploads = PostgresUploadRepository(session=session)
        self._clock = clock or (lambda: datetime.now(UTC))

    def process(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        learning_track: LearningTrack,
        processor_route: str,
        idempotency_key: str,
    ) -> ImportWorkspace:
        upload = self._required_upload(owner_user_id=owner_user_id, import_id=import_id)
        if not upload.recognition_eligible:
            raise ExternalImportError(
                "import_not_recognition_eligible",
                "文件尚未通过完整安全检查，不能开始识别。",
                status_code=409,
            )
        resolved_artifact_version_id = _resolved_artifact_version_id(upload)
        existing = self._current_import_recognition(
            owner_user_id=owner_user_id,
            artifact_version_id=resolved_artifact_version_id,
        )
        if existing is not None:
            if existing.state == CandidateState.CANDIDATE:
                return self.confirm_recognition_and_match(
                    owner_user_id=owner_user_id,
                    import_id=import_id,
                    recognition_revision_id=existing.recognition_revision_id,
                    expected_revision_no=existing.revision_no,
                    learning_track=learning_track,
                    idempotency_key=f"{idempotency_key}:automatic-search",
                )
            return self.workspace(
                owner_user_id=owner_user_id,
                import_id=import_id,
                preferred_recognition=existing,
                learning_track=learning_track,
            )
        recognition = self._recognitions.recognize(
            owner_user_id=owner_user_id,
            artifact_version_id=resolved_artifact_version_id,
            recognition_kind=RecognitionKind.EXAM_SEGMENTATION,
            processor_route=processor_route,
            idempotency_key=idempotency_key,
        )
        return self.confirm_recognition_and_match(
            owner_user_id=owner_user_id,
            import_id=import_id,
            recognition_revision_id=recognition.recognition_revision_id,
            expected_revision_no=recognition.revision_no,
            learning_track=learning_track,
            idempotency_key=f"{idempotency_key}:automatic-search",
        )

    def correct_recognition(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        recognition_revision_id: UUID,
        command: CorrectImportRecognitionCommand,
        idempotency_key: str,
    ) -> ImportWorkspace:
        current = self._required_import_recognition(
            owner_user_id=owner_user_id,
            import_id=import_id,
            recognition_revision_id=recognition_revision_id,
        )
        payload = RecognitionCandidatePayload.model_validate(current.candidate_payload)
        corrected_payload = _corrected_payload(payload, command=command)
        corrected = self._recognitions.correct(
            owner_user_id=owner_user_id,
            recognition_revision_id=current.recognition_revision_id,
            expected_revision_no=command.expected_revision_no,
            corrected_payload=corrected_payload,
            reason="user_reviewed_import_segments",
            idempotency_key=idempotency_key,
        )
        return self.workspace(
            owner_user_id=owner_user_id,
            import_id=import_id,
            preferred_recognition=corrected,
        )

    def confirm_recognition_and_match(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        recognition_revision_id: UUID,
        expected_revision_no: int,
        learning_track: LearningTrack,
        idempotency_key: str,
    ) -> ImportWorkspace:
        current = self._required_import_recognition(
            owner_user_id=owner_user_id,
            import_id=import_id,
            recognition_revision_id=recognition_revision_id,
        )
        confirmed = self._recognitions.confirm(
            owner_user_id=owner_user_id,
            recognition_revision_id=current.recognition_revision_id,
            expected_revision_no=expected_revision_no,
            idempotency_key=f"{idempotency_key}:recognition",
        )
        payload = RecognitionCandidatePayload.model_validate(confirmed.candidate_payload)
        matches: list[ContentMatchVersion] = []
        for unit in payload.question_units:
            match, evaluation = self._matching.evaluate(
                owner_user_id=owner_user_id,
                recognition=confirmed,
                question_unit_id=unit.question_unit_id,
                query=_match_query(
                    confirmed,
                    track=learning_track,
                    question_unit_id=unit.question_unit_id,
                ),
                idempotency_key=f"{idempotency_key}:match:{unit.question_unit_id}",
            )
            if (
                evaluation.ambiguity_reason in {"single_exact", "single_similar"}
                and len(match.candidates) == 1
            ):
                match = self._matching.confirm(
                    owner_user_id=owner_user_id,
                    content_match_version_id=match.content_match_version_id,
                    expected_version_no=match.version_no,
                    content_version_id=match.candidates[0].content_version_id,
                    idempotency_key=f"{idempotency_key}:exact:{unit.question_unit_id}",
                )
            matches.append(match)
        return self.workspace(
            owner_user_id=owner_user_id,
            import_id=import_id,
            preferred_recognition=confirmed,
            preferred_matches=tuple(matches),
            learning_track=learning_track,
        )

    def confirm_match(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        content_match_version_id: UUID,
        command: ConfirmImportMatchCommand,
        idempotency_key: str,
    ) -> ImportWorkspace:
        self._required_upload(owner_user_id=owner_user_id, import_id=import_id)
        match = self._match_repository.version(content_match_version_id=content_match_version_id)
        if (
            match is None
            or match.owner_user_id != owner_user_id
            or not self._recognition_belongs_to_import(
                import_id=import_id, recognition_revision_id=match.recognition_revision_id
            )
        ):
            raise ExternalImportError(
                "content_match_not_found", "匹配候选不存在。", status_code=404
            )
        confirmed = self._matching.confirm(
            owner_user_id=owner_user_id,
            content_match_version_id=content_match_version_id,
            expected_version_no=command.expected_version_no,
            content_version_id=command.confirmed_content_version_id,
            idempotency_key=idempotency_key,
        )
        recognition = self._recognition_repository.revision(
            recognition_revision_id=confirmed.recognition_revision_id
        )
        if recognition is None:
            raise ExternalImportError("recognition_not_found", "识别版本不存在。", status_code=409)
        return self.workspace(
            owner_user_id=owner_user_id,
            import_id=import_id,
            preferred_recognition=recognition,
            preferred_matches=(confirmed,),
        )

    def workspace(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        preferred_recognition: RecognitionRevision | None = None,
        preferred_matches: tuple[ContentMatchVersion, ...] = (),
        learning_track: LearningTrack | None = None,
    ) -> ImportWorkspace:
        upload = self._required_upload(owner_user_id=owner_user_id, import_id=import_id)
        artifact = self._uploads.artifact_for_session(session_id=import_id)
        recognition = preferred_recognition or self._current_import_recognition(
            owner_user_id=owner_user_id,
            artifact_version_id=_resolved_artifact_version_id(upload),
        )
        matches = {
            item.question_unit_id: item
            for item in (
                self._current_matches(recognition.recognition_revision_id)
                if recognition is not None
                else ()
            )
        }
        matches.update({item.question_unit_id: item for item in preferred_matches})
        state, processing_label, poll_after, failure_reason = _workspace_state(
            upload_state=upload.state.value,
            recognition=recognition,
            matches=tuple(matches.values()),
            failure_code=upload.failure_code,
        )
        reasons: list[str] = []
        if recognition is not None and recognition.state == CandidateState.CANDIDATE:
            reasons.append("识别结果必须由你确认，确认前不会进行题库匹配。")
        question_units = _workspace_units(
            recognition=recognition,
            matches=matches,
            owner_user_id=owner_user_id,
            matching=self._matching,
            learning_track=learning_track,
            ai_answers={
                unit.question_unit_id: answer
                for unit in (
                    RecognitionCandidatePayload.model_validate(
                        recognition.candidate_payload
                    ).question_units
                    if recognition is not None
                    else ()
                )
                if (
                    answer := self._answer_repository.current_for_unit(
                        owner_user_id=owner_user_id,
                        import_id=import_id,
                        question_unit_id=unit.question_unit_id,
                    )
                )
                is not None
            },
        )
        for unit in question_units:
            reasons.extend(unit.partial_reasons)
        if (
            recognition is not None
            and recognition.state == CandidateState.CONFIRMED
            and question_units
            and all(unit.state == "ready" for unit in question_units)
        ):
            state, processing_label, poll_after, failure_reason = (
                "ready",
                None,
                None,
                None,
            )
        return ImportWorkspace(
            importId=import_id,
            state=state,
            artifact=artifact,
            recognition=recognition,
            recognitionSegments=(_segments(recognition) if recognition is not None else ()),
            questionUnits=question_units,
            partialReasons=tuple(dict.fromkeys(reasons)),
            failureReason=failure_reason,
            processingLabel=processing_label,
            pollAfterMs=poll_after,
            expiresAt=upload.expires_at,
            diagnosticEligibility=(
                "eligible_confirmed"
                if question_units
                and all(
                    item.match is not None and item.match.state == CandidateState.CONFIRMED
                    for item in question_units
                )
                else "blocked_unconfirmed"
            ),
            generatedAt=self._clock(),
        )

    def _required_upload(self, *, owner_user_id: UUID, import_id: UUID):
        upload = self._uploads.session(session_id=import_id)
        if upload is None or upload.owner_user_id != owner_user_id:
            raise ExternalImportError("import_not_found", "导入任务不存在。", status_code=404)
        return upload

    def _required_import_recognition(
        self,
        *,
        owner_user_id: UUID,
        import_id: UUID,
        recognition_revision_id: UUID,
    ) -> RecognitionRevision:
        upload = self._required_upload(owner_user_id=owner_user_id, import_id=import_id)
        recognition = self._recognition_repository.revision(
            recognition_revision_id=recognition_revision_id
        )
        if (
            recognition is None
            or recognition.owner_user_id != owner_user_id
            or recognition.artifact_version_id != _resolved_artifact_version_id(upload)
            or recognition.recognition_kind != RecognitionKind.EXAM_SEGMENTATION
        ):
            raise ExternalImportError("recognition_not_found", "识别结果不存在。", status_code=404)
        return recognition

    def _current_import_recognition(
        self, *, owner_user_id: UUID, artifact_version_id: UUID
    ) -> RecognitionRevision | None:
        return self._recognition_repository.head(
            artifact_version_id=artifact_version_id,
            owner_user_id=owner_user_id,
            recognition_kind=RecognitionKind.EXAM_SEGMENTATION.value,
        )

    def _current_matches(self, recognition_revision_id: UUID) -> tuple[ContentMatchVersion, ...]:
        version_ids = (
            self._session.execute(
                text(
                    """
                select distinct on (question_unit_id) content_match_version_id
                from learning.content_match_versions
                where recognition_revision_id = :recognition_revision_id
                order by question_unit_id, version_no desc
                """
                ),
                {"recognition_revision_id": str(recognition_revision_id)},
            )
            .scalars()
            .all()
        )
        return tuple(
            item
            for item in (
                self._match_repository.version(content_match_version_id=version_id)
                for version_id in version_ids
            )
            if item is not None
        )

    def _recognition_belongs_to_import(
        self, *, import_id: UUID, recognition_revision_id: UUID
    ) -> bool:
        upload = self._uploads.session(session_id=import_id)
        recognition = self._recognition_repository.revision(
            recognition_revision_id=recognition_revision_id
        )
        return bool(
            upload is not None
            and recognition is not None
            and recognition.artifact_version_id == _resolved_artifact_version_id(upload)
            and recognition.owner_user_id == upload.owner_user_id
        )


def _resolved_artifact_version_id(upload: object) -> UUID:
    resolved = getattr(upload, "resolved_artifact_version_id", None)
    if isinstance(resolved, UUID):
        return resolved
    raise ExternalImportError(
        "import_artifact_unresolved",
        "导入文件尚未形成可识别的资料版本。",
        status_code=409,
    )


def _corrected_payload(
    source: RecognitionCandidatePayload, *, command: CorrectImportRecognitionCommand
) -> RecognitionCandidatePayload:
    corrections = {item.segment_id: item.text.strip() for item in command.segments}
    if len(corrections) != len(command.segments):
        raise ExternalImportError(
            "duplicate_segment_correction", "识别修订包含重复分区。", status_code=422
        )
    segments = {item.segment_id: item for item in source.segments}
    if set(corrections) != set(segments):
        raise ExternalImportError(
            "segment_set_changed", "识别分区已经变化，请刷新后重新修订。", status_code=409
        )
    spans: list[TextSpan] = []
    corrected_segments = []
    for segment in source.segments:
        span_id = f"user-{segment.segment_id}"
        spans.append(
            TextSpan(
                spanId=span_id,
                text=corrections[segment.segment_id],
                box=segment.box,
                confidence=1.0,
            )
        )
        corrected_segments.append(
            segment.model_copy(update={"text_span_refs": (span_id,), "confidence": 1.0})
        )
    return source.model_copy(
        update={"text_spans": tuple(spans), "segments": tuple(corrected_segments)}
    )


def _segments(recognition: RecognitionRevision) -> tuple[ImportRecognitionSegment, ...]:
    payload = RecognitionCandidatePayload.model_validate(recognition.candidate_payload)
    spans = {item.span_id: item for item in payload.text_spans}
    state = (
        "confirmed"
        if recognition.state == CandidateState.CONFIRMED
        else "rejected"
        if recognition.state == CandidateState.REJECTED
        else "candidate"
    )
    result: list[ImportRecognitionSegment] = []
    for ordinal, segment in enumerate(payload.segments, start=1):
        referenced = [spans[item] for item in segment.text_span_refs if item in spans]
        text_value = "\n".join(item.text for item in referenced).strip()
        confidence = min([segment.confidence, *(item.confidence for item in referenced)])
        result.append(
            ImportRecognitionSegment(
                segmentId=segment.segment_id,
                pageNo=segment.box.page_no,
                ordinal=ordinal,
                text=text_value,
                confidence=confidence,
                state=state,
                requiresCorrection=(not text_value or confidence < 0.85),
            )
        )
    return tuple(result)


def _match_query(
    recognition: RecognitionRevision,
    *,
    track: LearningTrack,
    question_unit_id: str,
) -> MatchQuery:
    payload = RecognitionCandidatePayload.model_validate(recognition.candidate_payload)
    spans = {item.span_id: item.text for item in payload.text_spans}
    unit = next(
        (item for item in payload.question_units if item.question_unit_id == question_unit_id),
        None,
    )
    if unit is None:
        raise ExternalImportError(
            "question_unit_not_found", "题目分区已变化，请刷新后重试。", status_code=409
        )
    segment_ids = {
        *unit.prompt_segment_refs,
        *unit.option_segment_refs,
        *unit.material_segment_refs,
    }

    def text_for(refs: tuple[str, ...]) -> tuple[str, ...]:
        refs_set = set(refs)
        return tuple(
            "\n".join(spans[ref] for ref in segment.text_span_refs if ref in spans).strip()
            for segment in payload.segments
            if segment.segment_id in refs_set
        )

    prompts = text_for(unit.prompt_segment_refs)
    options = text_for(unit.option_segment_refs)
    materials = text_for(unit.material_segment_refs)
    prompt = "\n".join(item for item in prompts if item).strip()
    if not prompt:
        prompt = "\n".join(
            spans[ref]
            for segment in payload.segments
            if segment.segment_id in segment_ids
            for ref in segment.text_span_refs
            if ref in spans
        ).strip()
    return MatchQuery(
        track=track,
        promptText=prompt,
        optionTexts=tuple(item for item in options if item),
        materialTexts=tuple(item for item in materials if item),
        visualFingerprints=payload.visual_fingerprints,
        visualFeatureHashes=payload.visual_feature_hashes,
    )


def _workspace_units(
    *,
    recognition: RecognitionRevision | None,
    matches: dict[str, ContentMatchVersion],
    owner_user_id: UUID,
    matching: MatchingService,
    learning_track: LearningTrack | None,
    ai_answers: dict[str, ExternalAIAnswerVersion],
) -> tuple[ImportQuestionUnit, ...]:
    if recognition is None:
        return ()
    payload = RecognitionCandidatePayload.model_validate(recognition.candidate_payload)
    units: list[ImportQuestionUnit] = []
    for unit in payload.question_units:
        match = matches.get(unit.question_unit_id)
        unit_reasons: list[str] = list(_match_reasons(match)) if match else []
        reference_answer = None
        matched_content = None
        ai_answer = None
        stored_ai = ai_answers.get(unit.question_unit_id)
        if stored_ai is not None:
            payload_value = stored_ai.answer_payload
            ai_answer = ImportAIAnswer(
                answerVersionId=stored_ai.answer_version_id,
                state=stored_ai.state,
                answerText=payload_value.answer if payload_value else None,
                explanation=payload_value.explanation if payload_value else None,
                keyPoints=payload_value.key_points if payload_value else (),
                confidence=payload_value.confidence if payload_value else None,
                limitations=payload_value.limitations if payload_value else (),
                outputFingerprint=stored_ai.output_fingerprint,
                failureCode=stored_ai.failure_code,
            )
        if learning_track == LearningTrack.ESSAY:
            query = _match_query(
                recognition,
                track=learning_track,
                question_unit_id=unit.question_unit_id,
            )
            if not query.material_texts:
                unit_reasons.append("申论材料不完整，不能给出可靠原题或 AI 答案结论。")
        if (
            recognition.state == CandidateState.CONFIRMED
            and match is not None
            and match.state == "confirmed"
        ):
            try:
                content = matching.matched_content(
                    owner_user_id=owner_user_id,
                    recognition=recognition,
                    match=match,
                )
                matched_content = ImportMatchedContent(
                    contentVersionId=content.content_version_id,
                    promptText=content.prompt_text,
                    optionTexts=content.option_texts,
                    materialTexts=content.material_texts,
                    solutionExplanation=content.solution_explanation,
                )
                answer = matching.stored_answer_candidate(
                    owner_user_id=owner_user_id,
                    recognition=recognition,
                    match=match,
                )
            except MatchingAuthorizationError:
                unit_reasons.append("已匹配原题，但当前授权不允许展示参考答案。")
            else:
                if answer is None:
                    unit_reasons.append("已匹配原题，但题库暂无可展示的参考答案。")
                elif answer.layer in {"exact", "similar"} and answer.content_version_id:
                    reference_answer = ImportReferenceAnswer(
                        sourceType="authorized_reference",
                        matchKind=answer.layer,
                        answerText=answer.answer_text,
                        contentVersionId=answer.content_version_id,
                        authorizationState="authorized_current",
                        learningImpact="reference_only",
                    )
        if recognition.state == CandidateState.CANDIDATE:
            unit_state = "recognition_review"
        elif match is not None and match.state == "confirmed":
            unit_state = "ready"
        elif stored_ai is not None and stored_ai.state == "confirmed":
            unit_state = "ready"
        elif match is None or (
            match.evaluation_reason in {"essay_incomplete", "no_eligible_candidate"}
            and not match.candidates
        ):
            unit_state = "unmatched"
        elif match.state == "candidate":
            unit_state = "match_review"
        else:
            unit_state = "unmatched"
        units.append(
            ImportQuestionUnit(
                questionUnitId=unit.question_unit_id,
                ordinal=unit.ordinal,
                segmentIds=(
                    *unit.material_segment_refs,
                    *unit.prompt_segment_refs,
                    *unit.option_segment_refs,
                ),
                match=match,
                matchedContent=matched_content,
                referenceAnswer=reference_answer,
                aiAnswer=ai_answer,
                state=unit_state,
                partialReasons=tuple(dict.fromkeys(unit_reasons)),
            )
        )
    return tuple(units)


def _match_reasons(match: ContentMatchVersion) -> tuple[str, ...]:
    reasons = {
        "essay_incomplete": "申论材料或题目要求不完整，当前不能可靠匹配。",
        "no_eligible_candidate": "没有找到当前授权允许使用的可靠候选。",
        "score_tie": "存在多个接近候选，必须由你确认原题版本。",
        "single_similar": "当前结果是相似候选，不是精确命中，必须由你确认。",
        "single_exact": "识别到精确候选，仍需由你最终确认。",
    }
    return (reasons[match.evaluation_reason],) if match.state == "candidate" else ()


def _workspace_state(
    *,
    upload_state: str,
    recognition: RecognitionRevision | None,
    matches: tuple[ContentMatchVersion, ...],
    failure_code: str | None,
) -> tuple[str, str | None, int | None, str | None]:
    if upload_state == "awaiting_upload":
        return "uploading", "等待文件上传", 1_500, None
    if upload_state in {
        "assembling",
        "uploaded",
        "scanning",
        "scan_failed",
        "finalizing",
        "quarantining",
        "rejecting",
    }:
        return "processing", "正在完成安全检查", 2_000, None
    if upload_state == "quarantined":
        return "quarantined", None, None, "文件没有通过安全检查。"
    if upload_state == "expired":
        return "expired", None, None, "导入任务已过期。"
    if upload_state == "failed":
        return "failed", None, None, failure_code or "文件处理失败。"
    if recognition is None:
        return "processing", "等待识别处理", 2_000, None
    if recognition.state in {CandidateState.REJECTED, CandidateState.EXPIRED}:
        return "failed", None, None, "识别结果已拒绝或过期。"
    if matches and all(match.state == "confirmed" for match in matches):
        return "ready", None, None, None
    return "review", None, None, None
