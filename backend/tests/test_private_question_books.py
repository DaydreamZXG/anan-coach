from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.external_learning.models import (
    CandidateState,
    ContentMatchVersion,
    MatchCandidate,
    RecognitionKind,
    RecognitionRevision,
    SourceArtifactState,
    SourceArtifactVersion,
    SourceKind,
)
from app.domains.external_ingestion.matching.repository import (
    SQLContentMatchRepository,
)
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    QuestionUnit,
    RecognitionCandidatePayload,
    TextSpan,
)
from app.domains.external_ingestion.recognition.repository import (
    SQLRecognitionRepository,
)
from app.domains.external_ingestion.upload.models import (
    ScanReceipt,
    ScanVerdict,
    UploadMode,
    UploadSession,
    UploadState,
)
from app.domains.external_ingestion.upload.repository import (
    PostgresUploadRepository,
)
from app.domains.private_knowledge.repository import PrivateKnowledgeRepository
from app.domains.private_knowledge.service import PrivateKnowledgeService
from app.domains.private_knowledge.worker import PrivateKnowledgeDeletionWorker
from app.domains.private_question_books.repository import (
    PrivateQuestionBookRepository,
)
from app.domains.private_question_books.schemas import (
    CreatePrivateQuestionBookCommand,
)
from app.domains.private_question_books.service import (
    PrivateQuestionBookError,
    PrivateQuestionBookService,
)
from app.main import create_app


def test_question_book_create_list_and_detail_derive_current_match_heads(
    db_session: Session,
) -> None:
    seeded = _seed_book_context(
        db_session, match_states=("confirmed", "candidate", "unmatched")
    )
    service = _service(db_session)

    created = service.create(
        owner_user_id=seeded.owner,
        command=_create_command(seeded),
        idempotency_key="question-book-create-happy",
    )

    assert created.total_question_count == 3
    assert created.resolved_question_count == 1
    assert created.unresolved_question_count == 2
    assert created.next_unresolved_question_unit_id == seeded.unit_ids[1]
    assert [item.state for item in created.questions] == [
        "matched",
        "needs_confirmation",
        "unmatched",
    ]
    assert service.list(owner_user_id=seeded.owner).books[0].book_id == created.book_id
    assert service.get(owner_user_id=seeded.owner, book_id=created.book_id) == created
    assert PrivateKnowledgeService(
        PrivateKnowledgeRepository(db_session)
    ).list(owner_user_id=seeded.owner).sources == ()
    paths = create_app().openapi()["paths"]
    assert set(paths["/api/v2/external-learning/question-books"]) == {"get", "post"}
    assert set(paths["/api/v2/external-learning/question-books/{book_id}"]) == {
        "delete",
        "get",
    }


def test_question_book_rejects_cross_owner_and_stale_recognition(
    db_session: Session,
) -> None:
    seeded = _seed_book_context(db_session, match_states=("unmatched",))
    other_owner = _seed_user(db_session, "其他学习者")
    service = _service(db_session)

    with pytest.raises(PrivateQuestionBookError) as cross_owner:
        service.create(
            owner_user_id=other_owner,
            command=_create_command(seeded),
            idempotency_key="question-book-cross-owner",
        )
    assert cross_owner.value.status_code == 404

    successor_id = _insert_recognition_successor(db_session, seeded)
    with pytest.raises(PrivateQuestionBookError) as stale:
        service.create(
            owner_user_id=seeded.owner,
            command=_create_command(seeded),
            idempotency_key="question-book-stale-recognition",
        )
    assert stale.value.code == "import_or_recognition_unavailable"
    assert successor_id != seeded.recognition_id
    assert db_session.execute(
        text("select count(*) from learning.private_knowledge_source_versions")
    ).scalar_one() == 0


def test_question_book_create_and_delete_are_idempotent(db_session: Session) -> None:
    seeded = _seed_book_context(db_session, match_states=("confirmed",))
    service = _service(db_session)
    command = _create_command(seeded)

    first = service.create(
        owner_user_id=seeded.owner,
        command=command,
        idempotency_key="question-book-idempotent-create",
    )
    replay = service.create(
        owner_user_id=seeded.owner,
        command=command,
        idempotency_key="question-book-idempotent-create",
    )
    assert replay.book_id == first.book_id

    with pytest.raises(PrivateQuestionBookError) as conflict:
        service.create(
            owner_user_id=seeded.owner,
            command=CreatePrivateQuestionBookCommand(
                importId=seeded.import_id,
                recognitionRevisionId=seeded.recognition_id,
                title="换了标题",
            ),
            idempotency_key="question-book-idempotent-create",
        )
    assert conflict.value.code == "idempotency_conflict"

    deletion = service.request_delete(
        owner_user_id=seeded.owner,
        book_id=first.book_id,
        idempotency_key="question-book-idempotent-delete",
    )
    deletion_replay = service.request_delete(
        owner_user_id=seeded.owner,
        book_id=first.book_id,
        idempotency_key="question-book-idempotent-delete",
    )
    assert deletion_replay.deletion_request_id == deletion.deletion_request_id


def test_question_book_view_follows_new_match_head_without_copying_state(
    db_session: Session,
) -> None:
    seeded = _seed_book_context(db_session, match_states=("candidate",))
    service = _service(db_session)
    created = service.create(
        owner_user_id=seeded.owner,
        command=_create_command(seeded),
        idempotency_key="question-book-dynamic-head",
    )
    assert created.questions[0].state == "needs_confirmation"
    source_digest = db_session.execute(
        text(
            "select content_sha256 from learning.private_knowledge_source_versions "
            "where source_id = cast(:source_id as uuid)"
        ),
        {"source_id": str(created.book_id)},
    ).scalar_one()

    _confirm_match_head(db_session, seeded, ordinal=1)
    refreshed = service.get(owner_user_id=seeded.owner, book_id=created.book_id)

    assert refreshed.questions[0].state == "matched"
    assert refreshed.unresolved_question_count == 0
    assert refreshed.state == "ready"
    assert db_session.execute(
        text(
            "select count(*) from learning.private_knowledge_source_versions "
            "where source_id = cast(:source_id as uuid)"
        ),
        {"source_id": str(created.book_id)},
    ).scalar_one() == 1
    assert db_session.execute(
        text(
            "select content_sha256 from learning.private_knowledge_source_versions "
            "where source_id = cast(:source_id as uuid)"
        ),
        {"source_id": str(created.book_id)},
    ).scalar_one() == source_digest


def test_question_book_artifact_deduplication_is_race_safe(
    db_session: Session,
) -> None:
    seeded = _seed_book_context(db_session, match_states=("unmatched",))
    service = _service(db_session)

    first = service.create(
        owner_user_id=seeded.owner,
        command=_create_command(seeded),
        idempotency_key="question-book-race-a",
    )
    second = service.create(
        owner_user_id=seeded.owner,
        command=_create_command(seeded),
        idempotency_key="question-book-race-b",
    )

    assert second.book_id == first.book_id
    assert db_session.execute(
        text(
            "select count(*) from learning.private_knowledge_source_versions "
            "where artifact_version_id = cast(:artifact as uuid)"
        ),
        {"artifact": str(seeded.artifact_id)},
    ).scalar_one() == 1
    assert db_session.execute(
        text(
            "select count(*) from learning.private_knowledge_commands "
            "where source_version_id = cast(:source_version as uuid)"
        ),
        {"source_version": str(first.source_version_id)},
    ).scalar_one() == 1


def test_question_book_export_delete_and_migration_lifecycle(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as session:
        seeded = _seed_book_context(
            session, match_states=("confirmed", "unmatched")
        )
        service = _service(session)
        created = service.create(
            owner_user_id=seeded.owner,
            command=_create_command(seeded),
            idempotency_key="question-book-lifecycle-create",
        )
        exported = service.export_markdown(
            owner_user_id=seeded.owner, book_id=created.book_id
        )
        assert "document_kind: question_book" in exported.content
        assert "题干1" in exported.content
        assert PrivateKnowledgeService(
            PrivateKnowledgeRepository(session)
        ).list(owner_user_id=seeded.owner).sources == ()
        deletion = service.request_delete(
            owner_user_id=seeded.owner,
            book_id=created.book_id,
            idempotency_key="question-book-lifecycle-delete",
        )
        session.commit()

    storage = _DeleteStorage()
    with db_session_factory() as session:
        completed = PrivateKnowledgeDeletionWorker(storage=storage).process_next(session)  # type: ignore[arg-type]
        assert completed is not None
        assert completed.status == "succeeded"

    assert storage.deleted == [seeded.object_ref]
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.private_knowledge_source_versions "
                "where source_id = cast(:source_id as uuid)"
            ),
            {"source_id": str(created.book_id)},
        ).scalar_one() == 0
        artifact = session.execute(
            text(
                "select state, object_ref, contract_payload "
                "from learning.source_artifact_versions "
                "where artifact_version_id = cast(:artifact as uuid)"
            ),
            {"artifact": str(seeded.artifact_id)},
        ).mappings().one()
        assert artifact["state"] == "deleted"
        assert artifact["object_ref"].startswith("deleted/")
        assert artifact["contract_payload"] == {}
        recognition = session.execute(
            text(
                "select state, candidate_payload from learning.recognition_revisions "
                "where recognition_revision_id = cast(:recognition as uuid)"
            ),
            {"recognition": str(seeded.recognition_id)},
        ).mappings().one()
        assert recognition["state"] == "expired"
        assert recognition["candidate_payload"] == {}
        assert session.execute(
            text(
                "select bool_and(state = 'expired' and candidates = '[]'::jsonb "
                "and contract_payload = '{}'::jsonb) "
                "from learning.content_match_versions "
                "where artifact_version_id = cast(:artifact as uuid)"
            ),
            {"artifact": str(seeded.artifact_id)},
        ).scalar_one() is True
        assert session.execute(text("select version_num from alembic_version")).scalar_one() == "20260815_113"
        assert session.execute(
            text(
                "select state from privacy.private_material_deletion_requests "
                "where deletion_request_id = cast(:deletion as uuid)"
            ),
            {"deletion": str(deletion.deletion_request_id)},
        ).scalar_one() == "completed"

    compatibility = (
        Path(__file__).parents[2] / "ops" / "release-db-compatibility.env"
    ).read_text()
    assert "ARTIFACT_DATABASE_MIGRATION_HEAD=20260815_113" in compatibility
    migration = import_module(
        "migrations.versions.20260814_111_private_question_book_kind"
    )
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()


class _SeededBook:
    def __init__(
        self,
        *,
        owner: str,
        import_id: UUID,
        artifact_id: UUID,
        recognition_id: UUID,
        payload: RecognitionCandidatePayload,
        unit_ids: tuple[str, ...],
        match_ids: tuple[UUID | None, ...],
        object_ref: str,
    ) -> None:
        self.owner = owner
        self.import_id = import_id
        self.artifact_id = artifact_id
        self.recognition_id = recognition_id
        self.payload = payload
        self.unit_ids = unit_ids
        self.match_ids = match_ids
        self.object_ref = object_ref


def _service(session: Session) -> PrivateQuestionBookService:
    return PrivateQuestionBookService(
        PrivateQuestionBookRepository(session), PrivateKnowledgeRepository(session)
    )


def _create_command(seeded: _SeededBook) -> CreatePrivateQuestionBookCommand:
    return CreatePrivateQuestionBookCommand(
        importId=seeded.import_id,
        recognitionRevisionId=seeded.recognition_id,
        title="张老师职测高频题本",
    )


def _seed_user(session: Session, display_name: str = "题本学习者") -> str:
    user_id = str(uuid4())
    session.execute(
        text(
            "insert into learner.users (user_id, display_name) "
            "values (cast(:user_id as uuid), :display_name)"
        ),
        {"user_id": user_id, "display_name": display_name},
    )
    return user_id


def _seed_book_context(
    session: Session,
    *,
    match_states: tuple[str, ...],
) -> _SeededBook:
    owner = _seed_user(session)
    now = datetime.now(UTC)
    reservation_id = uuid4()
    session.execute(
        text(
            """
            insert into learning.upload_reservations (
              reservation_id, reservation_key, owner_user_id, byte_size,
              policy_version, state, attempt_no, created_at, updated_at
            ) values (
              cast(:reservation_id as uuid), :reservation_key,
              cast(:owner as uuid), 2048, 'upload-quota@v1',
              'reserved', 1, :now, :now
            )
            """
        ),
        {
            "reservation_id": str(reservation_id),
            "reservation_key": sha256(str(reservation_id).encode()).hexdigest(),
            "owner": owner,
            "now": now,
        },
    )
    owner_uuid = UUID(owner)
    upload = UploadSession(
        session_id=uuid4(),
        artifact_id=uuid4(),
        artifact_version_id=uuid4(),
        owner_user_id=owner_uuid,
        revision=1,
        state=UploadState.AWAITING_UPLOAD,
        source_kind=SourceKind.PDF_UPLOAD,
        original_filename="teacher-book.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        byte_size=2048,
        content_sha256=sha256(str(uuid4()).encode()).hexdigest(),
        page_count=max(1, len(match_states)),
        object_ref=f"tos://private/question-books/{uuid4()}.pdf",
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
    uploads = PostgresUploadRepository(session=session)
    uploads.insert_session_with_command(
        session=upload,
        command_type="source_upload_start",
        idempotency_key=f"question-book-upload-{upload.session_id}",
        request_hash=sha256(str(upload.session_id).encode()).hexdigest(),
    )
    scanning = upload.transition(UploadState.SCANNING, at=now)
    uploads.save_session(expected_revision=1, session=scanning)
    receipt = ScanReceipt(
        scan_id=f"scan-{upload.session_id}",
        verdict=ScanVerdict.CLEAN,
        detected_mime_type="application/pdf",
        content_sha256=upload.content_sha256,
        scanner_policy_version="malware-policy@v1",
        provider_route="approved-test-scanner",
        data_processing_agreement_version="dpa@v1",
        definition_version="definitions@v1",
        scanned_at=now,
        detected_page_count=upload.page_count,
    )
    finalizing = scanning.transition(
        UploadState.FINALIZING, at=now, scan_receipt=receipt
    )
    uploads.save_session(expected_revision=2, session=finalizing)
    artifact = SourceArtifactVersion(
        artifactVersionId=upload.artifact_version_id,
        artifactId=upload.artifact_id,
        ownerUserId=owner_uuid,
        versionNo=1,
        sourceKind=upload.source_kind,
        state=SourceArtifactState.AVAILABLE,
        objectRef=upload.object_ref,
        mimeType=upload.mime_type,
        contentSha256=upload.content_sha256,
        byteSize=upload.byte_size,
        pageCount=upload.page_count,
        createdAt=now,
    )
    uploads.insert_artifact(session_id=upload.session_id, artifact=artifact)
    available = finalizing.transition(
        UploadState.AVAILABLE,
        at=now,
        resolved_artifact_version_id=artifact.artifact_version_id,
    )
    uploads.save_session(expected_revision=3, session=available)

    payload, unit_ids = _payload(len(match_states))
    recognition_id = uuid4()
    payload_json = payload.model_dump(by_alias=True, mode="json")
    SQLRecognitionRepository(session).insert(
        RecognitionRevision(
            recognitionRevisionId=recognition_id,
            artifactVersionId=artifact.artifact_version_id,
            ownerUserId=owner_uuid,
            revisionNo=1,
            recognitionKind=RecognitionKind.EXAM_SEGMENTATION,
            state=CandidateState.CONFIRMED,
            processorPolicyVersion="recognition-policy@v1",
            processorRoute="approved-test-route",
            inputSha256="4" * 64,
            outputSha256=_json_digest(payload_json),
            candidatePayload=payload_json,
            confidence=0.99,
            expiresAt=now + timedelta(days=7),
            confirmedAt=now,
            createdAt=now,
        )
    )
    match_ids: list[UUID | None] = []
    matches = SQLContentMatchRepository(session)
    for ordinal, state in enumerate(match_states, start=1):
        if state == "missing":
            match_ids.append(None)
            continue
        match_id = uuid4()
        match_ids.append(match_id)
        candidates: tuple[MatchCandidate, ...]
        evaluation_reason: str
        confirmed_content: str | None = None
        if state == "confirmed":
            content_id = f"{ordinal + 9:x}" * 64
            candidates = (
                MatchCandidate(
                    contentVersionId=content_id,
                    matchKind="exact",
                    score=0.99,
                    evidenceRefs=(f"text:unit:{ordinal}",),
                ),
            )
            evaluation_reason = "single_exact"
            confirmed_content = content_id
        elif state == "candidate":
            candidates = (
                MatchCandidate(
                    contentVersionId=f"{ordinal + 9:x}" * 64,
                    matchKind="similar",
                    score=0.8,
                    evidenceRefs=(f"text:unit:{ordinal}:a",),
                ),
                MatchCandidate(
                    contentVersionId=f"{ordinal + 10:x}" * 64,
                    matchKind="similar",
                    score=0.79,
                    evidenceRefs=(f"text:unit:{ordinal}:b",),
                ),
            )
            evaluation_reason = "score_tie"
        else:
            candidates = ()
            evaluation_reason = "no_eligible_candidate"
        matches.insert(
            ContentMatchVersion(
                contentMatchVersionId=uuid4(),
                contentMatchId=match_id,
                artifactVersionId=artifact.artifact_version_id,
                recognitionRevisionId=recognition_id,
                questionUnitId=unit_ids[ordinal - 1],
                ownerUserId=owner_uuid,
                versionNo=1,
                state="confirmed" if state == "confirmed" else "candidate",
                candidates=candidates,
                evaluationReason=evaluation_reason,
                confirmedContentVersionId=confirmed_content,
                matchingPolicyVersion="external-hybrid-match-v1",
                inputFingerprint=f"{ordinal + 4:x}" * 64,
                createdAt=now,
            )
        )
    return _SeededBook(
        owner=owner,
        import_id=upload.session_id,
        artifact_id=artifact.artifact_version_id,
        recognition_id=recognition_id,
        payload=payload,
        unit_ids=unit_ids,
        match_ids=tuple(match_ids),
        object_ref=upload.object_ref,
    )


def _payload(count: int) -> tuple[RecognitionCandidatePayload, tuple[str, ...]]:
    spans = tuple(
        TextSpan(
            spanId=f"span-{ordinal}",
            text=f"题干{ordinal}",
            box=BoundingBox(
                pageNo=ordinal,
                x=0.1,
                y=0.1,
                width=0.8,
                height=0.2,
            ),
            confidence=0.98,
        )
        for ordinal in range(1, count + 1)
    )
    segments = tuple(
        PageSegment(
            segmentId=f"segment-{ordinal}",
            segmentKind="question",
            box=span.box,
            textSpanRefs=(span.span_id,),
            confidence=0.98,
        )
        for ordinal, span in enumerate(spans, start=1)
    )
    unit_ids = tuple(f"{ordinal:x}" * 64 for ordinal in range(1, count + 1))
    units = tuple(
        QuestionUnit(
            questionUnitId=unit_ids[ordinal - 1],
            ordinal=ordinal,
            promptSegmentRefs=(segments[ordinal - 1].segment_id,),
        )
        for ordinal in range(1, count + 1)
    )
    return (
        RecognitionCandidatePayload(
            kind=RecognitionKind.EXAM_SEGMENTATION,
            textSpans=spans,
            segments=segments,
            questionUnits=units,
            processorRequestIdHash="d" * 64,
            processorOutputContract="exam-segmentation@v1",
            requestHash="e" * 64,
        ),
        unit_ids,
    )


def _insert_recognition_successor(
    session: Session, seeded: _SeededBook
) -> UUID:
    successor_id = uuid4()
    payload_json = seeded.payload.model_copy(
        update={
            "predecessor_revision_id": seeded.recognition_id,
            "correction_reason": "测试过期修订",
        }
    ).model_dump(by_alias=True, mode="json")
    now = datetime.now(UTC)
    SQLRecognitionRepository(session).insert(
        RecognitionRevision(
            recognitionRevisionId=successor_id,
            artifactVersionId=seeded.artifact_id,
            ownerUserId=UUID(seeded.owner),
            revisionNo=2,
            recognitionKind=RecognitionKind.EXAM_SEGMENTATION,
            state=CandidateState.CONFIRMED,
            processorPolicyVersion="recognition-policy@v1",
            processorRoute="approved-test-route",
            inputSha256="7" * 64,
            outputSha256=_json_digest(payload_json),
            candidatePayload=payload_json,
            confidence=0.99,
            supersedesRecognitionRevisionId=seeded.recognition_id,
            expiresAt=now + timedelta(days=7),
            confirmedAt=now,
            createdAt=now,
        )
    )
    return successor_id


def _confirm_match_head(
    session: Session, seeded: _SeededBook, *, ordinal: int
) -> None:
    match_id = seeded.match_ids[ordinal - 1]
    assert match_id is not None
    row = session.execute(
        text(
            """
            select content_match_version_id, candidates
            from learning.content_match_versions
            where content_match_id = cast(:match_id as uuid)
            order by version_no desc limit 1
            """
        ),
        {"match_id": str(match_id)},
    ).mappings().one()
    candidates = tuple(MatchCandidate.model_validate(item) for item in row["candidates"])
    SQLContentMatchRepository(session).insert(
        ContentMatchVersion(
            contentMatchVersionId=uuid4(),
            contentMatchId=match_id,
            artifactVersionId=seeded.artifact_id,
            recognitionRevisionId=seeded.recognition_id,
            questionUnitId=seeded.unit_ids[ordinal - 1],
            ownerUserId=UUID(seeded.owner),
            versionNo=2,
            state="confirmed",
            candidates=candidates,
            evaluationReason="score_tie",
            confirmedContentVersionId=candidates[0].content_version_id,
            matchingPolicyVersion="external-hybrid-match-v1",
            inputFingerprint="f" * 64,
            supersedesContentMatchVersionId=row["content_match_version_id"],
            createdAt=datetime.now(UTC),
        )
    )


def _json_digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


class _DeleteStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        assert operation_key
        self.deleted.append(object_ref)
