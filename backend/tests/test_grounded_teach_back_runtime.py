from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domains.teach_back_provider_shadow import TeachBackProviderShadowResult
from app.domains.teach_back_provider_shadow.contracts import ProviderCallAuditCandidate
from app.domains.teach_back_runtime.schemas import (
    StartTeachBackCommand,
    TeachBackTransferCommand,
)
from app.domains.teach_back_runtime.service import (
    TeachBackRuntimeError,
    TeachBackRuntimeService,
)
from app.domains.teach_back_shadow import (
    GroundingClaim,
    StudentReproductionOutput,
)
from app.domains.teach_back_shadow.contracts import GroundingShadowCandidate
from app.domains.teaching_packages import TeachingQuestionVisualBinding
from app.domains.xingce_learning.rescue.repository import (
    RescueMethodCardRow,
    RescueNodeLookup,
    RescueSimilarQuestionRow,
    RescueTargetProfile,
)


NOW = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
USER_A = "00000000-0000-4000-8000-00000000e151"
USER_B = "00000000-0000-4000-8000-00000000e152"
REVIEW_ID = "00000000-0000-4000-8000-00000000e153"
SOURCE_ID = "source-wrong-question"


class _Repository:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.commands: dict[tuple[str, str], dict[str, object]] = {}
        self.mastery: dict[str, dict[str, object]] = {}

    def by_command(self, _session, *, user_id: str, command_id: str):
        return next(
            (
                row
                for row in self.rows
                if row["user_id"] == user_id and row["command_id"] == command_id
            ),
            None,
        )

    def current(self, _session, *, user_id: str, session_id: str, for_update=False):
        rows = [
            row
            for row in self.rows
            if row["user_id"] == user_id and row["session_id"] == session_id
        ]
        return max(rows, key=lambda row: int(row["revision"])) if rows else None

    def insert_initial(self, _session, *, values):
        row = {**values, "revision": 1}
        self.rows.append(row)
        return row

    def insert_completed(self, _session, *, values):
        row = {**values, "state": "completed"}
        self.rows.append(row)
        return row

    def xingce_command(self, _session, *, user_id: str, command_id: str):
        return self.commands.get((user_id, command_id))

    def insert_xingce_command(
        self,
        _session,
        *,
        user_id: str,
        command_id: str,
        session_id: str,
        request_fingerprint: str,
        response_payload,
        created_at,
    ) -> None:
        self.commands[(user_id, command_id)] = {
            "request_fingerprint": request_fingerprint,
            "response_payload": response_payload,
        }

    def mastery_version(self, _session, *, user_id: str, mastery_version_id: str):
        row = self.mastery.get(mastery_version_id)
        return row if row and row["user_id"] == user_id else None


class _Rescue:
    def __init__(self, _session) -> None:
        pass

    def current_review(self, *, user_id: str, review_id: str):
        if user_id not in {USER_A, USER_B} or review_id != REVIEW_ID:
            return None
        return {"session_id": "00000000-0000-4000-8000-00000000e154"}

    def wrong_question_ids_for_review(self, *, user_id: str, session_id: str):
        return (SOURCE_ID,)

    def target_profile(self, *, user_id: str):
        return RescueTargetProfile(
            target_exam="guokao",
            target_region="全国",
            position_group="国考岗位",
        )

    def resolve_fresh_release(self):
        return {
            "release_id": "release",
            "exam_scope": "guokao",
            "track": "xingce",
            "generated_at": NOW,
        }

    def knowledge_nodes_for_questions(self, *, question_ids, release):
        return {
            SOURCE_ID: RescueNodeLookup(
                node_id="micro-skill-1",
                display_path="资料分析/增长率",
                module_name="资料分析",
                method_card_ids=("method-1",),
            )
        }

    def method_cards_by_ids(self, card_ids):
        return {
            "method-1": RescueMethodCardRow(
                card_id="method-1",
                title="增长率分母",
                module_name="资料分析",
                tag_path="资料分析/增长率",
                recognition="已知现期量与增长量",
                method_steps=("先求基期量", "再用增长量除以基期量"),
                pitfall="不要用现期量做分母",
                fix_action="做迁移题",
                time_budget_sec=60,
            )
        }

    def select_similar_question(self, **_kwargs):
        return [_question("transfer-a", "B"), _question("transfer-b", "C")]


class _VisualReader:
    def binding_for_question(self, _session, *, question_ref: str, visual_required: bool):
        return TeachingQuestionVisualBinding(
            questionRef=question_ref,
            contentVersionId=f"content:{question_ref}",
            visualRequired=visual_required,
            sourceAssetSha256s=(),
            evidence=(),
        )


class _Mastery:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.calls: list[dict[str, object]] = []

    def record_teach_back_transfer(self, _session, **values):
        self.calls.append(values)
        mastery_id = str(uuid4())
        row = {
            "mastery_version_id": mastery_id,
            "user_id": values["user_id"],
            "state": "review_scheduled",
            "verification_stage": "short_delay",
            "due_at": NOW + timedelta(days=1),
        }
        self.repository.mastery[mastery_id] = row
        return row


class _Session:
    pass


def _question(question_id: str, answer: str) -> RescueSimilarQuestionRow:
    return RescueSimilarQuestionRow(
        question_id=question_id,
        content_version_id=f"content:{question_id}",
        module_name="资料分析",
        stem=f"{question_id}：某指标增长率是多少？",
        options=tuple(
            {"label": label, "option_text": f"{index * 10}%"}
            for index, label in enumerate(("A", "B", "C", "D"), start=1)
        ),
        correct_option_ids=(answer,),
        explanation=f"{question_id} 的服务端解析",
        visual_required=False,
    )


def _audit(operation: str, key: str) -> ProviderCallAuditCandidate:
    return ProviderCallAuditCandidate(
        idempotencyKey=key,
        operation=operation,
        provider="test-provider@v1",
        model="test-model",
        promptVersion="prompt-v1",
        schemaVersion="schema-v1",
        inputFingerprint="1" * 64,
        outputFingerprint="2" * 64,
        inputTokens=10,
        cachedInputTokens=0,
        outputTokens=5,
        totalCostCny="0.00000001",
        latencyMs=1,
        requestIdSource="body:id",
        requestIdFingerprint="3" * 64,
    )


def _provider(command):
    return TeachBackProviderShadowResult(
        state="displayable",
        reasonCodes=(),
        studentOutputs=(
            StudentReproductionOutput(
                state="reproduced",
                reproducedSteps=("先求基期量", "再求增长率"),
                attemptedOptionId="B",
            ),
        ),
        groundingCandidates=(
            GroundingShadowCandidate(
                packageFingerprint=command.package_fingerprint,
                claims=(
                    GroundingClaim(
                        classification="supported",
                        summary="用户讲出了两步方法。",
                        evidenceRefs=("method:method-1:step:1",),
                    ),
                ),
                nextAction="ready_for_transfer",
            ),
        ),
        callAudits=(
            _audit("teach_back_student_reproduction", "student-key"),
            _audit("teach_back_grounding_verification", "verifier-key"),
        ),
    )


def _service(provider=_provider):
    repository = _Repository()
    mastery = _Mastery(repository)
    service = TeachBackRuntimeService(
        repository=repository,
        rescue_repository=_Rescue,
        visual_reader=_VisualReader(),
        mastery_service=mastery,
        provider_runner=provider,
        settings=SimpleNamespace(),
    )
    return service, repository, mastery


def test_runtime_has_one_package_compiler_owned_by_the_brick_assembler() -> None:
    source = Path("app/domains/teach_back_runtime/service.py").read_text(
        encoding="utf-8"
    )
    assert "compile_teaching_package" not in source
    assert 'targetScopeKey="guokao:national:2026:general:xingce"' not in source
    assert "self._brick_assembler.assemble(" in source


def test_start_replays_without_second_provider_call_and_never_exposes_answers() -> None:
    calls = 0

    def provider(command):
        nonlocal calls
        calls += 1
        return _provider(command)

    service, _, _ = _service(provider)
    command = StartTeachBackCommand(
        commandId="teach-back-start-015",
        sourceQuestionId=SOURCE_ID,
        userExplanation="先求基期量，再用增长量除以基期量。",
    )
    first = service.start(_Session(), user_id=USER_A, review_id=REVIEW_ID, command=command, now=NOW)
    replay = service.start(
        _Session(), user_id=USER_A, review_id=REVIEW_ID, command=command, now=NOW
    )

    assert calls == 1
    assert replay == first
    assert first.state == "ready_for_transfer"
    payload = first.model_dump(mode="json", by_alias=True)
    assert "answerKey" not in str(payload)
    assert "服务端解析" not in str(payload)
    assert first.transfer_question is not None


def test_provider_idempotency_scope_isolated_between_users() -> None:
    fingerprints: list[str] = []

    def provider(command):
        fingerprints.append(command.package_fingerprint)
        return _provider(command)

    service, _, _ = _service(provider)
    for user_id, command_id in (
        (USER_A, "teach-back-user-a-015"),
        (USER_B, "teach-back-user-b-015"),
    ):
        service.start(
            _Session(),
            user_id=user_id,
            review_id=REVIEW_ID,
            command=StartTeachBackCommand(
                commandId=command_id,
                sourceQuestionId=SOURCE_ID,
                userExplanation="先求基期量，再用增长量除以基期量。",
            ),
            now=NOW,
        )

    assert len(fingerprints) == 2
    assert fingerprints[0] != fingerprints[1]


def test_only_transfer_question_advances_existing_mastery_and_is_owner_isolated() -> None:
    service, _, mastery = _service()
    started = service.start(
        _Session(),
        user_id=USER_B,
        review_id=REVIEW_ID,
        command=StartTeachBackCommand(
            commandId="teach-back-owner-015",
            sourceQuestionId=SOURCE_ID,
            userExplanation="先求基期量，再用增长量除以基期量。",
        ),
        now=NOW,
    )
    assert started.transfer_question is not None
    with pytest.raises(TeachBackRuntimeError) as cross_user:
        service.get(_Session(), user_id=USER_A, session_id=str(started.session_id))
    assert cross_user.value.status_code == 404

    receipt = service.transfer(
        _Session(),
        user_id=USER_B,
        session_id=str(started.session_id),
        command=TeachBackTransferCommand(
            commandId="teach-back-transfer-015",
            revision=started.revision,
            selectedOptionIds=["C"],
            confidence="high",
        ),
        now=NOW,
    )

    assert receipt.correct is True
    assert receipt.verification_stage == "short_delay"
    assert len(mastery.calls) == 1
    assert mastery.calls[0]["user_id"] == USER_B
    assert mastery.calls[0]["question_id"] == started.transfer_question.question_id
    replay = service.transfer(
        _Session(),
        user_id=USER_B,
        session_id=str(started.session_id),
        command=TeachBackTransferCommand(
            commandId="teach-back-transfer-015",
            revision=started.revision,
            selectedOptionIds=["C"],
            confidence="high",
        ),
        now=NOW,
    )
    assert replay == receipt
    assert len(mastery.calls) == 1


def test_stale_revision_and_provider_failure_fail_locally() -> None:
    service, _, mastery = _service()
    started = service.start(
        _Session(),
        user_id=USER_A,
        review_id=REVIEW_ID,
        command=StartTeachBackCommand(
            commandId="teach-back-stale-start-015",
            sourceQuestionId=SOURCE_ID,
            userExplanation="先求基期量，再求增长率。",
        ),
        now=NOW,
    )
    with pytest.raises(TeachBackRuntimeError) as stale:
        service.transfer(
            _Session(),
            user_id=USER_A,
            session_id=str(started.session_id),
            command=TeachBackTransferCommand(
                commandId="teach-back-stale-transfer-015",
                revision=started.revision + 1,
                selectedOptionIds=["B"],
                confidence="medium",
            ),
            now=NOW,
        )
    assert stale.value.code == "teach_back_revision_conflict"
    assert mastery.calls == []

    def unavailable(_command):
        raise RuntimeError("provider unavailable")

    failed_service, _, failed_mastery = _service(unavailable)
    failed = failed_service.start(
        _Session(),
        user_id=USER_A,
        review_id=REVIEW_ID,
        command=StartTeachBackCommand(
            commandId="teach-back-provider-fail-015",
            sourceQuestionId=SOURCE_ID,
            userExplanation="先求基期量，再求增长率。",
        ),
        now=NOW,
    )
    assert failed.state == "provider_failed"
    assert failed.transfer_question is None
    assert failed_mastery.calls == []


def test_explanation_overflow_is_rejected_by_closed_contract() -> None:
    with pytest.raises(Exception):
        StartTeachBackCommand(
            commandId="teach-back-overflow-015",
            sourceQuestionId=SOURCE_ID,
            userExplanation="讲" * 50_001,
        )
