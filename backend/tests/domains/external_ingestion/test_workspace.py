from __future__ import annotations

from types import MethodType, SimpleNamespace
from uuid import uuid4

from app.contracts.external_learning.models import CandidateState, LearningTrack
from app.domains.external_ingestion.workspace import ExternalImportWorkspaceService


class _Uploads:
    def __init__(self, upload: object) -> None:
        self._upload = upload

    def session(self, *, session_id):  # noqa: ANN001, ANN201
        del session_id
        return self._upload


class _Recognitions:
    def __init__(self) -> None:
        self.artifact_version_id = None

    def recognize(self, **kwargs):  # noqa: ANN003, ANN201
        self.artifact_version_id = kwargs["artifact_version_id"]
        return SimpleNamespace(
            recognition_revision_id=uuid4(),
            revision_no=1,
            state=CandidateState.CANDIDATE,
        )


class _RecognitionRepository:
    def __init__(self, existing: object | None) -> None:
        self._existing = existing

    def head(self, **_kwargs):  # noqa: ANN003, ANN201
        return self._existing


def test_deduplicated_import_processes_the_resolved_artifact_version() -> None:
    owner_user_id = uuid4()
    import_id = uuid4()
    transient_artifact_version_id = uuid4()
    resolved_artifact_version_id = uuid4()
    upload = SimpleNamespace(
        owner_user_id=owner_user_id,
        artifact_version_id=transient_artifact_version_id,
        resolved_artifact_version_id=resolved_artifact_version_id,
        recognition_eligible=True,
    )
    recognitions = _Recognitions()
    service = ExternalImportWorkspaceService.__new__(ExternalImportWorkspaceService)
    service._uploads = _Uploads(upload)  # type: ignore[attr-defined]
    service._recognitions = recognitions  # type: ignore[attr-defined]
    service._recognition_repository = _RecognitionRepository(None)  # type: ignore[attr-defined]
    service.confirm_recognition_and_match = MethodType(  # type: ignore[method-assign]
        lambda self, **kwargs: ("searched", kwargs),
        service,
    )

    result = service.process(
        owner_user_id=owner_user_id,
        import_id=import_id,
        learning_track=LearningTrack.XINGCE,
        processor_route="modal-exam-image-v1",
        idempotency_key="deduplicated-import-key",
    )

    assert result[0] == "searched"
    assert result[1]["learning_track"] == LearningTrack.XINGCE
    assert recognitions.artifact_version_id == resolved_artifact_version_id
    assert recognitions.artifact_version_id != transient_artifact_version_id


def test_deduplicated_import_reuses_existing_recognition() -> None:
    owner_user_id = uuid4()
    import_id = uuid4()
    resolved_artifact_version_id = uuid4()
    existing = SimpleNamespace(state=CandidateState.CONFIRMED)
    upload = SimpleNamespace(
        owner_user_id=owner_user_id,
        artifact_version_id=uuid4(),
        resolved_artifact_version_id=resolved_artifact_version_id,
        recognition_eligible=True,
    )
    recognitions = _Recognitions()
    service = ExternalImportWorkspaceService.__new__(ExternalImportWorkspaceService)
    service._uploads = _Uploads(upload)  # type: ignore[attr-defined]
    service._recognitions = recognitions  # type: ignore[attr-defined]
    service._recognition_repository = _RecognitionRepository(existing)  # type: ignore[attr-defined]
    service.workspace = MethodType(  # type: ignore[method-assign]
        lambda self, **kwargs: kwargs["preferred_recognition"],
        service,
    )

    result = service.process(
        owner_user_id=owner_user_id,
        import_id=import_id,
        learning_track=LearningTrack.XINGCE,
        processor_route="mineru-ocr-v1",
        idempotency_key="deduplicated-existing-recognition",
    )

    assert result is existing
    assert recognitions.artifact_version_id is None


def test_deduplicated_candidate_is_automatically_confirmed_and_searched() -> None:
    owner_user_id = uuid4()
    import_id = uuid4()
    existing = SimpleNamespace(
        recognition_revision_id=uuid4(),
        revision_no=3,
        state=CandidateState.CANDIDATE,
    )
    upload = SimpleNamespace(
        owner_user_id=owner_user_id,
        artifact_version_id=uuid4(),
        resolved_artifact_version_id=uuid4(),
        recognition_eligible=True,
    )
    recognitions = _Recognitions()
    service = ExternalImportWorkspaceService.__new__(ExternalImportWorkspaceService)
    service._uploads = _Uploads(upload)  # type: ignore[attr-defined]
    service._recognitions = recognitions  # type: ignore[attr-defined]
    service._recognition_repository = _RecognitionRepository(existing)  # type: ignore[attr-defined]
    service.confirm_recognition_and_match = MethodType(  # type: ignore[method-assign]
        lambda self, **kwargs: kwargs,
        service,
    )

    result = service.process(
        owner_user_id=owner_user_id,
        import_id=import_id,
        learning_track=LearningTrack.XINGCE,
        processor_route="mineru-ocr-v1",
        idempotency_key="deduplicated-candidate",
    )

    assert result["recognition_revision_id"] == existing.recognition_revision_id
    assert result["expected_revision_no"] == 3
    assert result["idempotency_key"] == "deduplicated-candidate:automatic-search"
    assert recognitions.artifact_version_id is None
