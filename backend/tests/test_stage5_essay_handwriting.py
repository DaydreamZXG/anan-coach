from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.external_learning.models import RecognitionKind
from app.core.config import Settings
from app.domains.essay_intelligence import handwriting_service as handwriting_module
from app.domains.essay_intelligence.handwriting_schemas import (
    EssayHandwritingStartCommand,
)
from app.domains.essay_intelligence.handwriting_service import EssayHandwritingService
from app.domains.external_ingestion.recognition.models import ArtifactInput
from app.domains.external_ingestion.recognition.errors import (
    RecognitionNotFound,
    RecognitionTransitionError,
)
from tests.test_essay_drafting_completion import (
    NOW,
    _create_command,
    _drafting_context,
)


def test_essay_handwriting_start_command_cannot_select_xingce_omr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        EssayHandwritingStartCommand.model_validate(
            {
                "artifactVersionId": str(uuid4()),
                "recognitionKind": "answer_sheet_omr",
            }
        )

    observed: dict[str, object] = {}
    expected_result = object()

    class RecognitionRuntimeSpy:
        def recognize(self, **kwargs: object) -> object:
            observed.update(kwargs)
            return expected_result

    monkeypatch.setattr(
        handwriting_module,
        "build_recognition_service",
        lambda **_: RecognitionRuntimeSpy(),
    )
    command = EssayHandwritingStartCommand(
        artifactVersionId=uuid4(),
    )

    artifact_source_kind = {"value": "camera_image"}

    class RecognitionRepositorySpy:
        def __init__(self, _: object) -> None:
            pass

        def artifact(self, **_: object) -> ArtifactInput:
            return ArtifactInput(
                artifactVersionId=command.artifact_version_id,
                ownerUserId=user_id,
                state="available",
                sourceKind=artifact_source_kind["value"],
                objectRef="tos://gongkao-dazi-prod/private/essay/source.jpg",
                mimeType="image/jpeg",
                contentSha256="5" * 64,
            )

    monkeypatch.setattr(
        handwriting_module,
        "SQLRecognitionRepository",
        RecognitionRepositorySpy,
    )
    user_id = uuid4()
    result = EssayHandwritingService().start(
        object(),  # type: ignore[arg-type]
        settings=Settings(
            recognition_default_essay_handwriting_route="essay-handwriting-ocr-v1"
        ),
        user_id=user_id,
        command=command,
        idempotency_key="stage5-essay-handwriting-fixed-kind",
    )

    assert result is expected_result
    assert observed["recognition_kind"] is RecognitionKind.HANDWRITING_OCR
    assert observed["processor_route"] == "essay-handwriting-ocr-v1"

    artifact_source_kind["value"] = "answer_sheet"
    with pytest.raises(RecognitionTransitionError):
        EssayHandwritingService().start(
            object(),  # type: ignore[arg-type]
            settings=Settings(
                recognition_default_essay_handwriting_route="essay-handwriting-ocr-v1"
            ),
            user_id=user_id,
            command=command,
            idempotency_key="stage5-essay-reject-answer-sheet",
        )


def test_confirmed_current_handwriting_revision_is_bound_to_essay_answer(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="stage5-handwriting-success-token",
    )
    answer_text = "第一段手写答案。\n第二段手写答案。"
    artifact_id, revision_id = _seed_handwriting(
        db_session_factory,
        user_id=str(context["user_id"]),
        answer_text=answer_text,
        state="confirmed",
    )
    command = _create_command(context)
    command["answer"] = {
        **command["answer"],
        "sourceType": "ocr_confirmed",
        "sourceArtifactVersionId": artifact_id,
        "recognitionRevisionId": revision_id,
        "answerText": answer_text,
    }
    response = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={
            **context["auth"],
            "Idempotency-Key": "stage5-handwriting-success-0001",
        },
        json=command,
    )

    assert response.status_code == 200, response.text
    answer = response.json()["data"]["answer"]
    assert answer["sourceType"] == "ocr_confirmed"
    assert answer["sourceArtifactVersionId"] == artifact_id
    assert answer["recognitionRevisionId"] == revision_id
    assert answer["answerText"] == answer_text

    class ArtifactSignerSpy:
        def sign_download(self, *, artifact_ref: str, expires_at: object) -> str:
            assert artifact_ref.endswith(f"/{artifact_id}.jpg")
            assert expires_at == NOW + timedelta(minutes=5)
            return "https://tos.example.test/private/essay-source.jpg?signature=test"

    with db_session_factory() as session:
        access = EssayHandwritingService(
            access_signer=ArtifactSignerSpy(),
            clock=lambda: NOW,
        ).artifact_access(
            session,
            user_id=context["user_id"],
            artifact_version_id=artifact_id,
            ttl_seconds=300,
        )
        assert str(access.artifact_version_id) == artifact_id
        assert access.mime_type == "image/jpeg"
        assert access.image_url.startswith("https://")

        with pytest.raises(RecognitionNotFound):
            EssayHandwritingService(
                access_signer=ArtifactSignerSpy(),
                clock=lambda: NOW,
            ).artifact_access(
                session,
                user_id=uuid4(),
                artifact_version_id=artifact_id,
                ttl_seconds=300,
            )


def test_unconfirmed_or_text_mismatched_handwriting_cannot_enter_review(
    db_session_factory: sessionmaker[Session],
) -> None:
    context = _drafting_context(
        db_session_factory,
        token="stage5-handwriting-rejected-token",
    )
    artifact_id, revision_id = _seed_handwriting(
        db_session_factory,
        user_id=str(context["user_id"]),
        answer_text="服务端确认文本",
        state="candidate",
    )
    command = _create_command(context)
    command["answer"] = {
        **command["answer"],
        "sourceType": "ocr_confirmed",
        "sourceArtifactVersionId": artifact_id,
        "recognitionRevisionId": revision_id,
        "answerText": "客户端自称确认的其他文本",
    }

    response = context["client"].post(
        "/api/v2/essay-intelligence/drafting/complete-plan-task",
        headers={
            **context["auth"],
            "Idempotency-Key": "stage5-handwriting-rejected-0001",
        },
        json=command,
    )

    assert response.status_code == 409, response.text
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.essay_answer_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": context["user_id"]},
        ).scalar_one() == 0


def _seed_handwriting(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    answer_text: str,
    state: str,
) -> tuple[str, str]:
    artifact_version_id = uuid4()
    recognition_revision_id = uuid4()
    artifact_id = uuid4()
    parts = answer_text.split("\n")
    payload = {
        "schemaVersion": "recognition-candidate-payload@v1",
        "kind": "handwriting_ocr",
        "textSpans": [
            {
                "spanId": f"span-{index}",
                "text": part,
                "box": {
                    "pageNo": 1,
                    "x": 0.05,
                    "y": 0.05 + index * 0.2,
                    "width": 0.9,
                    "height": 0.15,
                },
                "confidence": 0.72 + index * 0.1,
            }
            for index, part in enumerate(parts)
        ],
        "segments": [],
        "omrMarks": [],
        "pageImageSha256": ["1" * 64],
        "visualFingerprints": ["2" * 64],
        "processorRequestIdHash": "3" * 64,
        "processorOutputContract": "recognition-processor-output@v1",
        "requestHash": "4" * 64,
        "predecessorRevisionId": None,
        "correctionReason": None,
    }
    payload_hash = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.source_artifact_versions (
                  artifact_version_id, artifact_id, owner_user_id, version_no,
                  source_kind, state, object_ref, mime_type, content_sha256,
                  byte_size, page_count, supersedes_artifact_version_id,
                  contract_payload, created_at
                ) values (
                  :artifact_version_id, :artifact_id, cast(:user_id as uuid), 1,
                  'camera_image', 'available', :object_ref, 'image/jpeg', :sha,
                  1024, null, null, cast(:contract_payload as jsonb), :created_at
                )
                """
            ),
            {
                "artifact_version_id": str(artifact_version_id),
                "artifact_id": str(artifact_id),
                "user_id": user_id,
                "object_ref": f"tos://test/private/{artifact_version_id}.jpg",
                "sha": "5" * 64,
                "contract_payload": json.dumps({"source": "stage5-test"}),
                "created_at": NOW,
            },
        )
        session.execute(
            text(
                """
                insert into learning.recognition_revisions (
                  recognition_revision_id, artifact_version_id, owner_user_id,
                  revision_no, recognition_kind, state, processor_policy_version,
                  processor_route, input_sha256, output_sha256, candidate_payload,
                  confidence, expires_at, confirmed_at, created_at,
                  supersedes_recognition_revision_id
                ) values (
                  :revision_id, :artifact_id, cast(:user_id as uuid), 1,
                  'handwriting_ocr', :state, 'stage5-test-policy', 'stage5-test-route',
                  :input_sha, :output_sha, cast(:payload as jsonb), 0.8,
                  :expires_at, :confirmed_at, :created_at, null
                )
                """
            ),
            {
                "revision_id": str(recognition_revision_id),
                "artifact_id": str(artifact_version_id),
                "user_id": user_id,
                "state": state,
                "input_sha": "5" * 64,
                "output_sha": payload_hash,
                "payload": json.dumps(payload, ensure_ascii=False),
                "expires_at": NOW + timedelta(days=1),
                "confirmed_at": NOW if state == "confirmed" else None,
                "created_at": NOW,
            },
        )
    return str(artifact_version_id), str(recognition_revision_id)
