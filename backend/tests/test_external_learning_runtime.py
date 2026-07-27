from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    ContentComponent,
    LicenseDecision,
    LicenseSnapshotStatus,
    LicenseUseRequest,
)
from app.domains.answer_ingestion.schemas import (
    AnswerEntry,
    ConfirmAnswerCandidateCommand,
    CreateAnswerCandidateCommand,
)
from app.domains.answer_ingestion.service import AnswerIngestionService
from app.domains.external_ingestion.matching.catalog import SQLQuestionContentCatalog
from app.domains.learning_packages.renderer import PacketRenderer
from app.domains.learning_packages.repository import LearningPackageRepository
from app.domains.learning_packages.schemas import (
    CreatePacketCommand,
    PacketContentInput,
    PacketRenderRequest,
    PacketRenderResult,
)
from app.domains.learning_packages.service import LearningPackageService
from app.domains.learning_packages.worker import PacketRenderWorker
from app.domains.learning_brain.service import LearningBrainService
from app.domains.xingce_learning.content_version import question_content_version_id


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def test_omr_blank_candidate_requires_explicit_correction_state() -> None:
    pending = AnswerEntry(
        bindingCode="ANSWER-BINDING-1",
        answer="",
        confidence=0.2,
        requiresCorrection=True,
    )

    assert pending.answer == ""
    assert pending.requires_correction is True
    with pytest.raises(ValidationError, match="blank answers must be marked for correction"):
        AnswerEntry(bindingCode="ANSWER-BINDING-1", answer="")


class FakeLicenseEvaluator:
    def __init__(self) -> None:
        self.snapshot_id = uuid4()

    def evaluate(self, request: LicenseUseRequest) -> LicenseDecision:
        return LicenseDecision(
            snapshotId=self.snapshot_id,
            allowed=True,
            reason="explicit_grant",
            contentLicenseId=request.content_license_id,
            contentVersionId=request.content_version_id,
            licenseRevision=1,
            authorizationEpoch=request.expected_authorization_epoch or 1,
            grantMatrixHash="a" * 64,
            evidenceManifestHash="b" * 64,
            evaluatedAt=NOW,
        )

    def snapshot_status(self, *, snapshot_id: UUID) -> LicenseSnapshotStatus:
        assert snapshot_id == self.snapshot_id
        return LicenseSnapshotStatus(
            snapshotId=snapshot_id,
            valid=True,
            reason="current",
            boundAuthorizationEpoch=1,
            currentAuthorizationEpoch=1,
            checkedAt=NOW,
        )


class FakePacketRenderer(PacketRenderer):
    def __init__(self) -> None:
        self.calls = 0

    def render(self, *, request: PacketRenderRequest, idempotency_key: str) -> PacketRenderResult:
        self.calls += 1
        assert idempotency_key == f"offline-packet-render:{request.packet_version_id}"
        return PacketRenderResult(
            artifactRef=f"tos://private-bucket/private/packets/{request.packet_version_id}.pdf",
            artifactSha256="c" * 64,
            providerRequestId="render-request-1",
        )


def test_existing_question_publication_migration_does_not_accumulate_advisory_locks(
    db_session: Session,
) -> None:
    db_session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type, stem, answer
            )
            select
              'q-migration-' || value,
              'objective',
              'active',
              '判断推理',
              'single',
              '迁移题目 ' || value,
              'A'
            from generate_series(1, 40) value
            """
        )
    )
    migration = import_module("migrations.versions.20260715_21_stage15_external_learning_runtime")

    migration._publish_existing_question_versions(db_session.connection())

    assert (
        db_session.execute(text("select count(*) from product.question_content_heads")).scalar_one()
        == 40
    )
    assert (
        db_session.execute(
            text(
                "select count(*) from pg_locks "
                "where pid = pg_backend_pid() and locktype = 'advisory'"
            )
        ).scalar_one()
        == 0
    )


def test_packet_render_and_confirmed_answer_round_trip(db_session: Session) -> None:
    user_id = uuid4()
    db_session.execute(
        text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
        {"id": str(user_id)},
    )
    db_session.execute(
        text(
            """
            insert into product.position_rule_releases (
              release_id, exam_code, exam_year, rule_set_version,
              position_data_version, status, activated_at
            ) values (
              :release_id, 'guokao', 2026, 'external-learning-rules',
              'external-learning-positions', 'active', :activated_at
            )
            """
        ),
        {"release_id": str(uuid4()), "activated_at": NOW},
    )
    db_session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type, stem, answer
            ) values ('q-stage15-1', 'objective', 'active', '判断推理', 'single',
                      '以下哪项正确？', 'A')
            """
        )
    )
    db_session.execute(
        text(
            """
            insert into product.question_options (
              question_id, option_index, label, option_text
            ) values
              ('q-stage15-1', 1, 'A', '选项甲'),
              ('q-stage15-1', 2, 'B', '选项乙')
            """
        )
    )
    source = LearningPackageRepository().question_snapshot(db_session, question_id="q-stage15-1")
    assert source is not None
    content_version_id = question_content_version_id(source)
    published = SQLQuestionContentCatalog(
        db_session,
        clock=lambda: NOW,
    ).publish_current(question_id="q-stage15-1")
    assert published.content_version_id == content_version_id
    licenses = FakeLicenseEvaluator()
    packet_service = LearningPackageService(
        session=db_session,
        license_evaluator=licenses,
        clock=lambda: NOW,
    )
    license_id = uuid4()
    packet_command = CreatePacketCommand(
        renderFormat="pdf",
        expiresInDays=14,
        items=(
            PacketContentInput(
                questionId="q-stage15-1",
                contentVersionId=content_version_id,
                contentLicenseId=license_id,
                expectedAuthorizationEpoch=1,
                track="xingce",
                components=(ContentComponent.QUESTION_PROMPT,),
            ),
        ),
    )
    requested = packet_service.request_packet(
        user_id=str(user_id),
        idempotency_key="packet-roundtrip-001",
        command=packet_command,
    )
    replay = packet_service.request_packet(
        user_id=str(user_id),
        idempotency_key="packet-roundtrip-001",
        command=packet_command,
    )
    assert replay.packet_version_id == requested.packet_version_id
    db_session.commit()

    renderer = FakePacketRenderer()
    worker = PacketRenderWorker(
        renderer=renderer,
        license_evaluator=licenses,
        clock=lambda: NOW + timedelta(seconds=1),
    )
    completed_job = worker.process_next(db_session)
    assert completed_job is not None
    assert completed_job.status == "succeeded"
    db_session.commit()

    ready = LearningPackageRepository().current_packet(db_session, packet_id=requested.packet_id)
    assert ready is not None
    assert ready.state == "ready"
    assert ready.version_no == 3
    assert renderer.calls == 1

    answer_service = AnswerIngestionService(
        session=db_session,
        license_evaluator=licenses,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    candidate = answer_service.create_candidate(
        user_id=str(user_id),
        idempotency_key="answer-candidate-001",
        command=CreateAnswerCandidateCommand(
            packetVersionId=ready.packet_version_id,
            track="xingce",
            inputMode="answer_string",
            answerString="A",
        ),
    )
    assert candidate.state == "candidate"
    assert candidate.candidate_payload["confirmedForLearning"] is False
    assert (
        db_session.execute(text("select count(*) from learning.external_answer_facts")).scalar_one()
        == 0
    )

    receipt = answer_service.confirm_candidate(
        user_id=str(user_id),
        ingestion_id=candidate.ingestion_id,
        idempotency_key="answer-confirm-001",
        command=ConfirmAnswerCandidateCommand(
            expectedVersionNo=1,
            confirmation="user_verified",
        ),
    )
    db_session.commit()
    assert receipt.ingestion.state == "committed"
    assert receipt.ingestion.candidate_payload["confirmedForLearning"] is True
    fact = (
        db_session.execute(
            text(
                "select target_writer, evidence_policy_version, review_state, answer_payload "
                "from learning.external_answer_facts"
            )
        )
        .mappings()
        .one()
    )
    assert fact["target_writer"] == "xingce_learning"
    assert fact["evidence_policy_version"] == "external-confirmed-lower-weight-v1"
    assert fact["review_state"] == "user_confirmed"
    assert fact["answer_payload"] == {"answer": "A"}

    profile = LearningBrainService().get_or_create_profile(
        db_session,
        user_id=str(user_id),
        now=NOW + timedelta(minutes=2),
    )
    assert profile.evidence_summary.external_answer_count == 1
    module = next(item for item in profile.modules if item.module_key == "判断推理")
    assert module.external_evidence_count == 1
    assert module.correct_rate is None
    assert any(
        item.entity_type == "external_answer_fact" and item.role == "user_confirmed_external_answer"
        for item in module.evidence_refs
    )
