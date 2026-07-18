from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    CandidateState,
    ContentMatchVersion,
    LearningTrack,
    RecognitionKind,
    RecognitionRevision,
    SourceArtifactState,
    SourceArtifactVersion,
    SourceKind,
)
from app.core.config import Settings
from app.domains.external_ingestion.answering.models import (
    ExternalAIAnswerPayload,
    ExternalAIAnswerProviderResult,
)
from app.domains.external_ingestion.answering.provider import ExternalAIAnswerProvider
from app.domains.external_ingestion.answering.service import ExternalAIAnswerService
from app.domains.external_ingestion.answering.worker import ExternalAIAnswerWorker
from app.domains.external_ingestion.matching.repository import SQLContentMatchRepository
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    QuestionUnit,
    RecognitionCandidatePayload,
    TextSpan,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.upload.models import (
    ScanReceipt,
    ScanVerdict,
    UploadMode,
    UploadSession,
    UploadState,
)
from app.domains.external_ingestion.upload.repository import PostgresUploadRepository


class FixedAnswerProvider:
    provider_name = "test-provider@release-1"
    model = "test-high-model"
    prompt_version = "external-question-answer@v1"
    schema_version = "external-question-answer-output@v1"

    def validate_configuration(self) -> None:
        return None

    def generate(self, *, query, source_segment_refs, idempotency_key):
        assert query.prompt_text == "下列说法正确的是？"
        assert source_segment_refs == ("segment-question",)
        assert idempotency_key.startswith("external-answer:")
        return ExternalAIAnswerProviderResult(
            output=ExternalAIAnswerPayload(
                answer="A",
                explanation="依据上传题面，A 符合题意。",
                keyPoints=("核对题干条件",),
                confidence="medium",
                limitations=("这不是官方题库答案",),
                sourceSegmentRefs=source_segment_refs,
            ),
            usageInputTokens=120,
            usageCachedInputTokens=20,
            usageOutputTokens=60,
            latencyMs=35,
            outputFingerprint="e" * 64,
            providerRequestIdFingerprint="f" * 64,
        )


def test_unmatched_question_ai_answer_is_persistent_confirmable_and_metered(
    db_session: Session,
) -> None:
    owner_user_id = uuid4()
    db_session.execute(
        text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
        {"id": str(owner_user_id)},
    )
    import_id, artifact_version_id = _seed_available_import(
        db_session, owner_user_id=owner_user_id
    )
    question_unit_id = "1" * 64
    recognition_revision_id = uuid4()
    payload = RecognitionCandidatePayload(
        kind=RecognitionKind.EXAM_SEGMENTATION,
        textSpans=(
            TextSpan(
                spanId="span-question",
                text="下列说法正确的是？",
                box=BoundingBox(pageNo=1, x=0.1, y=0.1, width=0.8, height=0.1),
                confidence=0.99,
            ),
        ),
        segments=(
            PageSegment(
                segmentId="segment-question",
                segmentKind="question",
                box=BoundingBox(pageNo=1, x=0.1, y=0.1, width=0.8, height=0.1),
                textSpanRefs=("span-question",),
                confidence=0.99,
            ),
        ),
        questionUnits=(
            QuestionUnit(
                questionUnitId=question_unit_id,
                ordinal=1,
                promptSegmentRefs=("segment-question",),
            ),
        ),
        processorRequestIdHash="2" * 64,
        processorOutputContract="exam-segmentation@v1",
        requestHash="3" * 64,
    )
    candidate_payload = payload.model_dump(by_alias=True, mode="json")
    output_sha256 = sha256(
        json.dumps(
            candidate_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    recognition = RecognitionRevision(
        recognitionRevisionId=recognition_revision_id,
        artifactVersionId=artifact_version_id,
        ownerUserId=owner_user_id,
        revisionNo=1,
        recognitionKind=RecognitionKind.EXAM_SEGMENTATION,
        state=CandidateState.CONFIRMED,
        processorPolicyVersion="recognition-policy@v1",
        processorRoute="approved-test-route",
        inputSha256="4" * 64,
        outputSha256=output_sha256,
        candidatePayload=candidate_payload,
        confidence=0.99,
        expiresAt=datetime.now(UTC) + timedelta(days=1),
        confirmedAt=datetime.now(UTC),
        createdAt=datetime.now(UTC),
    )
    SQLRecognitionRepository(db_session).insert(recognition)
    SQLContentMatchRepository(db_session).insert(
        ContentMatchVersion(
            contentMatchVersionId=uuid4(),
            contentMatchId=uuid4(),
            artifactVersionId=artifact_version_id,
            recognitionRevisionId=recognition_revision_id,
            questionUnitId=question_unit_id,
            ownerUserId=owner_user_id,
            versionNo=1,
            state="candidate",
            candidates=(),
            evaluationReason="no_eligible_candidate",
            matchingPolicyVersion="external-hybrid-match-v1",
            inputFingerprint="6" * 64,
            createdAt=datetime.now(UTC),
        )
    )

    settings = Settings(
        environment="local",
        ai_price_version="test-price@v1",
        ai_input_cny_per_million_tokens="1.00",
        ai_cached_input_cny_per_million_tokens="0.20",
        ai_output_cny_per_million_tokens="2.00",
    )
    provider = FixedAnswerProvider()
    service = ExternalAIAnswerService(
        session=db_session,
        settings=settings,
        provider=cast(ExternalAIAnswerProvider, provider),
    )
    requested = service.request(
        owner_user_id=owner_user_id,
        import_id=import_id,
        question_unit_id=question_unit_id,
        learning_track=LearningTrack.XINGCE,
        idempotency_key="request-ai-answer-001",
    )
    replay = service.request(
        owner_user_id=owner_user_id,
        import_id=import_id,
        question_unit_id=question_unit_id,
        learning_track=LearningTrack.XINGCE,
        idempotency_key="request-ai-answer-001",
    )
    assert replay.answer_version_id == requested.answer_version_id
    db_session.commit()

    completed_job = ExternalAIAnswerWorker(
        settings=settings,
        provider=cast(ExternalAIAnswerProvider, provider),
    ).process_next(db_session)
    assert completed_job is not None
    assert completed_job.status == "succeeded"
    candidate_version_id = completed_job.result["answerVersionId"]

    candidate = service._repository.by_version(answer_version_id=candidate_version_id)
    assert candidate is not None
    assert candidate.state == "candidate"
    assert candidate.answer_payload is not None
    assert candidate.answer_payload.answer == "A"
    confirmed = service.confirm(
        owner_user_id=owner_user_id,
        answer_version_id=candidate.answer_version_id,
        expected_output_fingerprint=candidate.output_fingerprint or "",
        idempotency_key="confirm-ai-answer-001",
    )
    db_session.commit()

    assert confirmed.state == "confirmed"
    assert db_session.execute(
        text(
            "select array_agg(state order by version_no) "
            "from learning.external_ai_answer_versions where answer_id = :answer_id"
        ),
        {"answer_id": str(confirmed.answer_id)},
    ).scalar_one() == ["requested", "processing", "candidate", "confirmed"]
    assert db_session.execute(
        text(
            "select status from commerce.feature_usage_reservations "
            "where reservation_key = :reservation_key"
        ),
        {"reservation_key": f"external-answer:{confirmed.answer_id}"},
    ).scalar_one() == "consumed"
    assert db_session.execute(
        text(
            "select status from ai_audit.model_call_ledger "
            "where call_key = :call_key"
        ),
        {"call_key": f"external-answer:{confirmed.answer_id}"},
    ).scalar_one() == "succeeded"


def _seed_available_import(session: Session, *, owner_user_id):
    now = datetime.now(UTC)
    repository = PostgresUploadRepository(session=session)
    reservation_id = uuid4()
    session.execute(
        text(
            """
            insert into learning.upload_reservations (
              reservation_id, reservation_key, owner_user_id, byte_size,
              policy_version, state, attempt_no, created_at, updated_at
            ) values (
              :reservation_id, :reservation_key, :owner_user_id, 1024,
              'upload-quota@v1', 'reserved', 1, :now, :now
            )
            """
        ),
        {
            "reservation_id": str(reservation_id),
            "reservation_key": "a" * 64,
            "owner_user_id": str(owner_user_id),
            "now": now,
        },
    )
    upload = UploadSession(
        session_id=uuid4(),
        artifact_id=uuid4(),
        artifact_version_id=uuid4(),
        owner_user_id=owner_user_id,
        revision=1,
        state=UploadState.AWAITING_UPLOAD,
        source_kind=SourceKind.IMAGE_IMPORT,
        original_filename="question.jpg",
        extension=".jpg",
        mime_type="image/jpeg",
        byte_size=1024,
        content_sha256="b" * 64,
        page_count=None,
        object_ref=f"tos://private/test/{uuid4()}.jpg",
        reservation_id=str(reservation_id),
        upload_policy_version="external-upload-policy@v1",
        upload_mode=UploadMode.SINGLE_PUT,
        provider_upload_id=None,
        part_size=None,
        expected_parts=(),
        uploaded_parts=(),
        created_at=now,
        expires_at=now + timedelta(hours=1),
        updated_at=now,
    )
    repository.insert_session_with_command(
        session=upload,
        command_type="source_upload_start",
        idempotency_key=f"upload-{upload.session_id}",
        request_hash="c" * 64,
    )
    scanning = upload.transition(UploadState.SCANNING, at=now)
    repository.save_session(expected_revision=1, session=scanning)
    receipt = ScanReceipt(
        scan_id="scan-stage15-answer",
        verdict=ScanVerdict.CLEAN,
        detected_mime_type="image/jpeg",
        content_sha256=upload.content_sha256,
        scanner_policy_version="malware-policy@v1",
        provider_route="approved-test-scanner",
        data_processing_agreement_version="dpa@v1",
        definition_version="definitions@v1",
        scanned_at=now,
    )
    finalizing = scanning.transition(
        UploadState.FINALIZING, at=now, scan_receipt=receipt
    )
    repository.save_session(expected_revision=2, session=finalizing)
    artifact = SourceArtifactVersion(
        artifactVersionId=upload.artifact_version_id,
        artifactId=upload.artifact_id,
        ownerUserId=owner_user_id,
        versionNo=1,
        sourceKind=upload.source_kind,
        state=SourceArtifactState.AVAILABLE,
        objectRef=upload.object_ref,
        mimeType=upload.mime_type,
        contentSha256=upload.content_sha256,
        byteSize=upload.byte_size,
        createdAt=now,
    )
    repository.insert_artifact(session_id=upload.session_id, artifact=artifact)
    available = finalizing.transition(
        UploadState.AVAILABLE,
        at=now,
        resolved_artifact_version_id=artifact.artifact_version_id,
    )
    repository.save_session(expected_revision=3, session=available)
    return upload.session_id, upload.artifact_version_id
