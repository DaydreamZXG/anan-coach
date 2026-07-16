from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from app.domains.external_ingestion.upload.models import (
    ObjectMetadata,
    ScanReceipt,
    ScanVerdict,
    UploadPartAuthorization,
)
from app.domains.learning_media.repository import LearningMediaRepository
from app.domains.learning_media.schemas import StartLearningMediaUploadCommand
from app.domains.learning_media.service import (
    RETENTION_CLEANUP_JOB_TYPE,
    LearningMediaService,
)
from app.domains.learning_media.worker import LearningMediaWorker
from tests.test_essay_learning_artifacts import (
    _client as _essay_client,
    _content_ids,
    _reflection_command,
    _seed_answer_and_completed_review,
)
from tests.test_planning_routes import _seed_learner_and_published_essay_content
from tests.test_xingce_planning_closure import _submitted_attempt


NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


class FakeLearningMediaStorage:
    def __init__(self) -> None:
        self.objects: dict[str, ObjectMetadata] = {}
        self.quarantined: list[str] = []
        self.delete_invocations: list[tuple[str, str]] = []
        self.delete_operations: dict[str, str] = {}
        self.physically_deleted: list[str] = []

    def authorize_single_put(
        self,
        *,
        object_key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> UploadPartAuthorization:
        object_ref = f"tos://private-test/{object_key}"
        return UploadPartAuthorization(
            object_ref=object_ref,
            part_number=1,
            byte_size=byte_size,
            content_sha256=content_sha256,
            url="https://object.example.invalid/voice",
            required_headers={
                "content-type": mime_type,
                "content-length": str(byte_size),
                "x-tos-content-sha256": content_sha256,
            },
            expires_at=expires_at,
        )

    def head_private(self, *, object_ref: str) -> ObjectMetadata:
        return self.objects[object_ref]

    def quarantine_private(self, *, object_ref: str, operation_key: str) -> str:
        self.quarantined.append(object_ref)
        return f"tos://private-test/private/quarantine/{operation_key}/voice.mp3"

    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        self.delete_invocations.append((object_ref, operation_key))
        replay = self.delete_operations.get(operation_key)
        if replay is not None:
            assert replay == object_ref
            return
        self.delete_operations[operation_key] = object_ref
        self.physically_deleted.append(object_ref)
        self.objects.pop(object_ref, None)


class FailOnceDeletedSuccessorRepository(LearningMediaRepository):
    def __init__(self) -> None:
        self.failed = False

    def insert_deleted_successor(
        self,
        session: Session,
        *,
        source: RowMapping,
        artifact_version_id: str,
        created_at: datetime,
    ) -> RowMapping:
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated crash after private object deletion")
        return super().insert_deleted_successor(
            session,
            source=source,
            artifact_version_id=artifact_version_id,
            created_at=created_at,
        )


class FakeLearningMediaScanner:
    def __init__(self, *, verdict: ScanVerdict = ScanVerdict.CLEAN) -> None:
        self.verdict = verdict

    def scan(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        operation_key: str,
    ) -> ScanReceipt:
        del object_ref, operation_key
        return ScanReceipt(
            scan_id=str(uuid4()),
            verdict=self.verdict,
            detected_mime_type="audio/mpeg",
            content_sha256=expected_sha256,
            scanner_policy_version="scanner-policy-test@v1",
            provider_route="scanner-test",
            data_processing_agreement_version="dpa-test@v1",
            definition_version="definitions-test@v1",
            scanned_at=datetime.now(UTC),
        )


def test_reflection_voice_upload_is_private_scanned_and_versioned(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    storage = FakeLearningMediaStorage()
    service = LearningMediaService(storage=storage)
    digest = "a" * 64
    command = StartLearningMediaUploadCommand(
        purpose="reflection_voice",
        originalFilename="reflection.mp3",
        mimeType="audio/mpeg",
        byteSize=2048,
        contentSha256=digest,
        durationMilliseconds=12_000,
    )
    with db_session_factory() as session:
        started = service.start(
            session,
            user_id=user_id,
            command_id="learning-media-start-1",
            command=command,
        )
        session.commit()
    assert started.session.state == "awaiting_upload"
    assert started.authorization is not None
    assert started.authorization.object_ref.startswith(
        f"tos://private-test/private/learning-media/{user_id}/"
    )
    storage.objects[started.authorization.object_ref] = ObjectMetadata(
        object_ref=started.authorization.object_ref,
        byte_size=2048,
        content_sha256=digest,
        mime_type="audio/mpeg",
        access="private",
        server_side_encrypted=True,
    )

    with db_session_factory() as session:
        queued = service.complete(
            session,
            user_id=user_id,
            upload_session_id=str(started.session.upload_session_id),
            command_id="learning-media-complete-1",
        )
        session.commit()
    assert queued.session.state == "scanning"
    assert queued.authorization is None
    assert queued.artifact is None

    worker = LearningMediaWorker(
        storage=storage,
        scanner=FakeLearningMediaScanner(),
    )
    with db_session_factory() as session:
        job = worker.process_next(session)
    assert job is not None
    assert job.status == "succeeded"

    with db_session_factory() as session:
        available = service.get(
            session,
            user_id=user_id,
            upload_session_id=str(started.session.upload_session_id),
        )
        artifact_row = session.execute(
            text(
                "select object_ref, scanner_receipt from "
                "learning.learning_media_artifact_versions"
            )
        ).mappings().one()
    assert available.session.state == "available"
    assert available.artifact is not None
    assert available.artifact.artifact_version_id == started.session.artifact_version_id
    assert artifact_row["object_ref"] == started.authorization.object_ref
    assert artifact_row["scanner_receipt"]["verdict"] == "clean"


def test_reflection_voice_rejects_integrity_mismatch_before_scan(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    storage = FakeLearningMediaStorage()
    service = LearningMediaService(storage=storage)
    command = StartLearningMediaUploadCommand(
        purpose="reflection_voice",
        originalFilename="reflection.mp3",
        mimeType="audio/mpeg",
        byteSize=1024,
        contentSha256="b" * 64,
        durationMilliseconds=8_000,
    )
    with db_session_factory() as session:
        started = service.start(
            session,
            user_id=user_id,
            command_id="learning-media-start-integrity",
            command=command,
        )
        session.commit()
    assert started.authorization is not None
    storage.objects[started.authorization.object_ref] = ObjectMetadata(
        object_ref=started.authorization.object_ref,
        byte_size=1024,
        content_sha256="c" * 64,
        mime_type="audio/mpeg",
        access="private",
        server_side_encrypted=True,
    )

    with db_session_factory() as session:
        try:
            service.complete(
                session,
                user_id=user_id,
                upload_session_id=str(started.session.upload_session_id),
                command_id="learning-media-complete-integrity",
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "learning_media_integrity_mismatch"
            session.rollback()
        else:
            raise AssertionError("integrity mismatch must fail closed")
    with db_session_factory() as session:
        state = session.execute(
            text(
                "select state from learning.learning_media_upload_sessions "
                "where upload_session_id = cast(:upload_session_id as uuid)"
            ),
            {"upload_session_id": str(started.session.upload_session_id)},
        ).scalar_one()
    assert state == "awaiting_upload"


def test_rejected_voice_is_quarantined_and_never_published(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    storage = FakeLearningMediaStorage()
    service = LearningMediaService(storage=storage)
    digest = "d" * 64
    command = StartLearningMediaUploadCommand(
        purpose="reflection_voice",
        originalFilename="unsafe.mp3",
        mimeType="audio/mpeg",
        byteSize=4096,
        contentSha256=digest,
        durationMilliseconds=10_000,
    )
    with db_session_factory() as session:
        started = service.start(
            session,
            user_id=user_id,
            command_id="learning-media-start-rejected",
            command=command,
        )
        session.commit()
    assert started.authorization is not None
    storage.objects[started.authorization.object_ref] = ObjectMetadata(
        object_ref=started.authorization.object_ref,
        byte_size=4096,
        content_sha256=digest,
        mime_type="audio/mpeg",
        access="private",
        server_side_encrypted=True,
    )
    with db_session_factory() as session:
        service.complete(
            session,
            user_id=user_id,
            upload_session_id=str(started.session.upload_session_id),
            command_id="learning-media-complete-rejected",
        )
        session.commit()

    worker = LearningMediaWorker(
        storage=storage,
        scanner=FakeLearningMediaScanner(verdict=ScanVerdict.MALICIOUS),
    )
    with db_session_factory() as session:
        result = worker.process_next(session)
    assert result is not None and result.status == "succeeded"
    assert storage.quarantined == [started.authorization.object_ref]

    with db_session_factory() as session:
        projection = service.get(
            session,
            user_id=user_id,
            upload_session_id=str(started.session.upload_session_id),
        )
        artifact_count = session.execute(
            text("select count(*) from learning.learning_media_artifact_versions")
        ).scalar_one()
    assert projection.session.state == "quarantined"
    assert projection.session.failure_code == "security_scan_rejected"
    assert projection.artifact is None
    assert artifact_count == 0


def test_xingce_reflection_voice_binding_is_atomic_owned_and_retained(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "learning-media-xingce-binding-token"
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token=token,
        choices=["D", "B", "C"],
    )
    user_id = _user_for_token(db_session_factory, token)
    foreign_user_id = _seed_user(db_session_factory)
    storage = FakeLearningMediaStorage()
    owned = _available_voice(
        db_session_factory,
        user_id=user_id,
        storage=storage,
        key="xingce-owned",
        started_at=NOW,
    )
    foreign = _available_voice(
        db_session_factory,
        user_id=foreign_user_id,
        storage=storage,
        key="xingce-foreign",
        started_at=NOW + timedelta(hours=1),
    )
    pending = _start_voice(
        db_session_factory,
        user_id=user_id,
        storage=storage,
        key="xingce-pending",
        started_at=NOW + timedelta(hours=2),
    )
    review = submitted["review"]

    for command_id, artifact_version_id in (
        ("xingce-voice-cross-owner", foreign.session.artifact_version_id),
        ("xingce-voice-not-available", pending.session.artifact_version_id),
    ):
        response = client.put(
            f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
            headers={**auth, "Idempotency-Key": command_id},
            json=_xingce_reflection_payload(
                review,
                artifact_version_id=str(artifact_version_id),
            ),
        )
        assert response.status_code == 409, response.text
        with db_session_factory() as session:
            assert session.execute(
                text(
                    "select count(*) from learning.xingce_reflection_versions "
                    "where review_id = cast(:review_id as uuid)"
                ),
                {"review_id": review["reviewId"]},
            ).scalar_one() == 0
            assert session.execute(
                text("select count(*) from learning.learning_media_bindings")
            ).scalar_one() == 0
            assert session.execute(
                text(
                    "select count(*) from learning.xingce_commands "
                    "where command_id = :command_id"
                ),
                {"command_id": command_id},
            ).scalar_one() == 0

    success = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "xingce-voice-owned"},
        json=_xingce_reflection_payload(
            review,
            artifact_version_id=str(owned.session.artifact_version_id),
        ),
    )
    assert success.status_code == 200, success.text
    reflection = success.json()["data"]["review"]["reflection"]
    assert reflection["voiceArtifactVersionIds"] == [
        str(owned.session.artifact_version_id)
    ]
    with db_session_factory() as session:
        binding = session.execute(
            text(
                "select user_id, artifact_version_id, entity_type, entity_version_id "
                "from learning.learning_media_bindings"
            )
        ).mappings().one()
    assert str(binding["user_id"]) == user_id
    assert str(binding["artifact_version_id"]) == str(
        owned.session.artifact_version_id
    )
    assert binding["entity_type"] == "xingce_reflection"
    assert str(binding["entity_version_id"]) == reflection["reflectionVersionId"]

    retention_worker = LearningMediaWorker(
        storage=storage,
        scanner=FakeLearningMediaScanner(),
    )
    with db_session_factory() as session:
        retained = retention_worker.process_next(
            session,
            now=NOW + timedelta(hours=24),
        )
    assert retained is not None
    assert retained.job_type == RETENTION_CLEANUP_JOB_TYPE
    assert retained.result == {
        "state": "available",
        "retentionAction": "retained_bound",
        "artifactVersionId": str(owned.session.artifact_version_id),
    }
    assert storage.delete_invocations == []
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select state from learning.learning_media_upload_sessions "
                "where upload_session_id = cast(:upload_session_id as uuid)"
            ),
            {"upload_session_id": str(owned.session.upload_session_id)},
        ).scalar_one() == "available"


def test_essay_reflection_voice_binding_is_atomic_and_owned(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "learning-media-essay-binding-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    answer_id, _review_id = _seed_answer_and_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    foreign_user_id = _seed_user(db_session_factory)
    storage = FakeLearningMediaStorage()
    owned = _available_voice(
        db_session_factory,
        user_id=user_id,
        storage=storage,
        key="essay-owned",
        started_at=NOW,
    )
    foreign = _available_voice(
        db_session_factory,
        user_id=foreign_user_id,
        storage=storage,
        key="essay-foreign",
        started_at=NOW + timedelta(hours=1),
    )
    pending = _start_voice(
        db_session_factory,
        user_id=user_id,
        storage=storage,
        key="essay-pending",
        started_at=NOW + timedelta(hours=2),
    )
    client = _essay_client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}

    for command_id, artifact_version_id in (
        ("essay-voice-cross-owner", foreign.session.artifact_version_id),
        ("essay-voice-not-available", pending.session.artifact_version_id),
    ):
        response = client.post(
            "/api/v2/essay-intelligence/artifacts",
            headers={**auth, "Idempotency-Key": command_id},
            json=_essay_reflection_payload(
                ids,
                answer_id,
                artifact_version_id=str(artifact_version_id),
            ),
        )
        assert response.status_code == 409, response.text
        with db_session_factory() as session:
            assert session.execute(
                text(
                    "select count(*) from learning.essay_learning_artifact_versions "
                    "where artifact_type = 'reflection'"
                )
            ).scalar_one() == 0
            assert session.execute(
                text("select count(*) from learning.learning_media_bindings")
            ).scalar_one() == 0
            assert session.execute(
                text(
                    "select count(*) from platform.outbox_events "
                    "where event_type = 'essay_learning_artifact.recorded'"
                )
            ).scalar_one() == 0

    success = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "essay-voice-owned"},
        json=_essay_reflection_payload(
            ids,
            answer_id,
            artifact_version_id=str(owned.session.artifact_version_id),
        ),
    )
    assert success.status_code == 200, success.text
    reflection = success.json()["data"]
    assert reflection["payload"]["voiceArtifactVersionIds"] == [
        str(owned.session.artifact_version_id)
    ]
    with db_session_factory() as session:
        binding = session.execute(
            text(
                "select user_id, artifact_version_id, entity_type, entity_version_id "
                "from learning.learning_media_bindings"
            )
        ).mappings().one()
    assert str(binding["user_id"]) == user_id
    assert str(binding["artifact_version_id"]) == str(
        owned.session.artifact_version_id
    )
    assert binding["entity_type"] == "essay_reflection"
    assert str(binding["entity_version_id"]) == reflection["artifactVersionId"]


def test_unbound_retention_cleanup_recovers_after_restart_without_duplicate_facts(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    storage = FakeLearningMediaStorage()
    available = _available_voice(
        db_session_factory,
        user_id=user_id,
        storage=storage,
        key="retention-recovery",
        started_at=NOW,
    )
    object_ref = next(iter(storage.objects))
    expires_at = NOW + timedelta(hours=24)
    failing_worker = LearningMediaWorker(
        storage=storage,
        scanner=FakeLearningMediaScanner(),
        repository=FailOnceDeletedSuccessorRepository(),
    )
    with db_session_factory() as session:
        first = failing_worker.process_next(session, now=expires_at)
    assert first is not None
    assert first.job_type == RETENTION_CLEANUP_JOB_TYPE
    assert first.status == "queued"
    assert first.last_error_code == "learning_media_retention_cleanup_unavailable"
    assert len(storage.delete_invocations) == 1
    assert storage.physically_deleted == [object_ref]
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select state from learning.learning_media_upload_sessions "
                "where upload_session_id = cast(:upload_session_id as uuid)"
            ),
            {"upload_session_id": str(available.session.upload_session_id)},
        ).scalar_one() == "available"
        assert session.execute(
            text("select count(*) from learning.learning_media_artifact_versions")
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from learning.learning_media_audit_events "
                "where event_type = 'learning-media-retention-expired@v1'"
            )
        ).scalar_one() == 0

    recovered_at = expires_at + timedelta(minutes=1)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update platform.background_jobs set run_after = :run_after "
                "where job_type = :job_type and status = 'queued'"
            ),
            {
                "run_after": recovered_at,
                "job_type": RETENTION_CLEANUP_JOB_TYPE,
            },
        )
    restarted_worker = LearningMediaWorker(
        storage=storage,
        scanner=FakeLearningMediaScanner(),
    )
    with db_session_factory() as session:
        recovered = restarted_worker.process_next(session, now=recovered_at)
    assert recovered is not None
    assert recovered.status == "succeeded"
    assert recovered.result is not None
    assert recovered.result["retentionAction"] == "deleted_unbound"
    assert len(storage.delete_invocations) == 2
    assert storage.delete_invocations[0] == storage.delete_invocations[1]
    assert storage.physically_deleted == [object_ref]

    service = LearningMediaService(storage=storage)
    with db_session_factory() as session:
        projection = service.get(
            session,
            user_id=user_id,
            upload_session_id=str(available.session.upload_session_id),
        )
        versions = session.execute(
            text(
                "select artifact_version_id, version_no, state, object_ref, "
                "supersedes_artifact_version_id "
                "from learning.learning_media_artifact_versions "
                "order by version_no"
            )
        ).mappings().all()
        audit = session.execute(
            text(
                "select sequence_no, from_state, to_state, evidence "
                "from learning.learning_media_audit_events "
                "where event_type = 'learning-media-retention-expired@v1'"
            )
        ).mappings().one()
        event = session.execute(
            text(
                "select deduplication_key, aggregate_id, payload "
                "from platform.outbox_events "
                "where event_type = 'learning-media-retention-expired@v1'"
            )
        ).mappings().one()
    assert projection.session.state == "expired"
    assert projection.session.failure_code == "unbound_retention_expired"
    assert projection.artifact is not None
    assert projection.artifact.state == "deleted"
    assert str(projection.artifact.artifact_version_id) == str(
        versions[1]["artifact_version_id"]
    )
    assert projection.artifact.artifact_version_id != available.session.artifact_version_id
    assert [(row["version_no"], row["state"]) for row in versions] == [
        (1, "available"),
        (2, "deleted"),
    ]
    assert versions[1]["object_ref"] is None
    assert versions[1]["supersedes_artifact_version_id"] == versions[0][
        "artifact_version_id"
    ]
    assert audit["sequence_no"] == 4
    assert (audit["from_state"], audit["to_state"]) == (
        "available",
        "expired",
    )
    assert audit["evidence"]["deleteOperationKey"] == storage.delete_invocations[0][1]
    assert event["deduplication_key"] == (
        f"learning-media:{available.session.upload_session_id}:retention-expired"
    )
    assert str(event["aggregate_id"]) == str(available.session.upload_session_id)
    assert event["payload"]["payload"]["deletedArtifactVersionId"] == str(
        versions[1]["artifact_version_id"]
    )
    with db_session_factory() as session:
        assert restarted_worker.process_next(session, now=recovered_at) is None


def _xingce_reflection_payload(
    review: dict[str, object],
    *,
    artifact_version_id: str,
) -> dict[str, object]:
    return {
        "expectedRevision": review["revision"],
        "rawText": "我用语音复盘了这组三题的判断过程。",
        "tags": ["审题", "复盘"],
        "strategyNote": "下一组先圈限定词。",
        "doubtNote": None,
        "voiceArtifactVersionIds": [artifact_version_id],
    }


def _essay_reflection_payload(
    ids: dict[str, str],
    answer_id: str,
    *,
    artifact_version_id: str,
) -> dict[str, object]:
    payload = _reflection_command(ids, answer_id)
    base_payload = payload["payload"]
    assert isinstance(base_payload, dict)
    payload["payload"] = {
        **base_payload,
        "voiceArtifactVersionIds": [artifact_version_id],
    }
    return payload


def _start_voice(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    storage: FakeLearningMediaStorage,
    key: str,
    started_at: datetime,
):
    service = LearningMediaService(storage=storage)
    digest = sha256(key.encode("utf-8")).hexdigest()
    command = StartLearningMediaUploadCommand(
        purpose="reflection_voice",
        originalFilename=f"{key}.mp3",
        mimeType="audio/mpeg",
        byteSize=2048,
        contentSha256=digest,
        durationMilliseconds=12_000,
    )
    with factory() as session:
        started = service.start(
            session,
            user_id=user_id,
            command_id=f"learning-media-start-{key}",
            command=command,
            now=started_at,
        )
        session.commit()
    return started


def _available_voice(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    storage: FakeLearningMediaStorage,
    key: str,
    started_at: datetime,
):
    started = _start_voice(
        factory,
        user_id=user_id,
        storage=storage,
        key=key,
        started_at=started_at,
    )
    assert started.authorization is not None
    storage.objects[started.authorization.object_ref] = ObjectMetadata(
        object_ref=started.authorization.object_ref,
        byte_size=started.session.byte_size,
        content_sha256=started.session.content_sha256,
        mime_type=started.session.mime_type,
        access="private",
        server_side_encrypted=True,
    )
    service = LearningMediaService(storage=storage)
    with factory() as session:
        service.complete(
            session,
            user_id=user_id,
            upload_session_id=str(started.session.upload_session_id),
            command_id=f"learning-media-complete-{key}",
            now=started_at + timedelta(minutes=1),
        )
        session.commit()
    worker = LearningMediaWorker(
        storage=storage,
        scanner=FakeLearningMediaScanner(),
    )
    with factory() as session:
        scan = worker.process_next(
            session,
            now=started_at + timedelta(minutes=2),
        )
    assert scan is not None
    assert scan.status == "succeeded"
    assert scan.job_type != RETENTION_CLEANUP_JOB_TYPE
    with factory() as session:
        available = service.get(
            session,
            user_id=user_id,
            upload_session_id=str(started.session.upload_session_id),
        )
    assert available.session.state == "available"
    assert available.artifact is not None
    return available


def _user_for_token(factory: sessionmaker[Session], token: str) -> str:
    with factory() as session:
        return str(
            session.execute(
                text(
                    "select user_id from learner.app_sessions "
                    "where session_token_hash = :token_hash"
                ),
                {"token_hash": token_hash_for_tests(token)},
            ).scalar_one()
        )


def _seed_user(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (:user_id, '语音复盘测试用户')"
            ),
            {"user_id": user_id},
        )
    return user_id
