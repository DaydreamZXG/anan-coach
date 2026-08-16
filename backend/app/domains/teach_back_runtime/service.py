from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domains.coach_brick_assembly import (
    CoachBrickAssemblyReason,
    CoachTeachingBrickAssembler,
    assembly_reason_message,
)
from app.domains.ai.model_cost import ModelPriceCard
from app.domains.teach_back_provider_shadow import (
    TeachBackProviderConfig,
    TeachBackProviderShadowResult,
    TeachBackShadowProvider,
    run_provider_shadow,
)
from app.domains.teach_back_runtime.repository import TeachBackRuntimeRepository
from app.domains.teach_back_runtime.schemas import (
    StartTeachBackCommand,
    TeachBackGroundingClaim,
    TeachBackQuestion,
    TeachBackSessionProjection,
    TeachBackStudentResult,
    TeachBackTransferCommand,
    TeachBackTransferReceipt,
)
from app.domains.teach_back_shadow import (
    ShadowPreparationInput,
    prepare_shadow_evaluation,
)
from app.domains.teaching_packages import StructuredVisualEvidenceReader
from app.domains.xingce_learning.mastery.service import XingceMasteryService
from app.domains.xingce_learning.rescue.repository import RescueRepository


class TeachBackRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


ProviderRunner = Callable[[ShadowPreparationInput], TeachBackProviderShadowResult]


class TeachBackRuntimeService:
    """Turn one confirmed method weakness into a grounded teach-back and transfer."""

    def __init__(
        self,
        *,
        repository: TeachBackRuntimeRepository | None = None,
        rescue_repository: type[RescueRepository] = RescueRepository,
        visual_reader: StructuredVisualEvidenceReader | None = None,
        mastery_service: XingceMasteryService | None = None,
        provider_runner: ProviderRunner | None = None,
        settings: Settings | None = None,
        brick_assembler: CoachTeachingBrickAssembler | None = None,
    ) -> None:
        self._repository = repository or TeachBackRuntimeRepository()
        resolved_visual_reader = visual_reader or StructuredVisualEvidenceReader()
        self._brick_assembler = brick_assembler or CoachTeachingBrickAssembler(
            rescue_repository=rescue_repository,
            visual_reader=resolved_visual_reader,
        )
        self._mastery_service = mastery_service or XingceMasteryService()
        self._settings = settings or get_settings()
        self._provider_runner = provider_runner or self._run_provider

    def start(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
        command: StartTeachBackCommand,
        now: datetime | None = None,
    ) -> TeachBackSessionProjection:
        recorded_at = now or datetime.now(UTC)
        request_fingerprint = _fingerprint(
            {
                "operation": "teach_back_start",
                "reviewId": review_id,
                "sourceQuestionId": command.source_question_id,
                "userExplanation": command.user_explanation,
            }
        )
        replay = self._repository.by_command(
            session, user_id=user_id, command_id=command.command_id
        )
        if replay is not None:
            _require_fingerprint(replay, request_fingerprint)
            return self._project(replay)

        assembly = self._brick_assembler.assemble(
            session,
            user_id=user_id,
            review_id=review_id,
            source_question_id=command.source_question_id,
            now=recorded_at,
        )
        if not assembly.available:
            raise _assembly_error(assembly.reason)
        package = assembly.package
        sidecar = assembly.visual_sidecar
        method = assembly.method
        assert package is not None and sidecar is not None and method is not None
        runtime_fingerprint = _fingerprint(
            {
                "compiledPackageFingerprint": package.fingerprint,
                "userId": user_id,
                "userExplanation": command.user_explanation,
            }
        )
        shadow_input = ShadowPreparationInput(
            packageFingerprint=runtime_fingerprint,
            methodCardRef=package.method_card_ref,
            methodSteps=method.method_steps,
            pitfalls=((method.pitfall,) if method.pitfall else ()),
            studentQuestionRef=package.student_question_ref,
            studentStem=package.student_stem,
            studentOptions=package.student_options,
            studentAnswerKey=package.student_answer_key,
            studentExplanation=package.student_explanation,
            studentVisualEvidence=sidecar.student_visual_evidence,
            verifierVisualEvidence=sidecar.student_verifier_visual_evidence,
            userExplanation=command.user_explanation,
        )
        prepared = prepare_shadow_evaluation(shadow_input)
        student_output: dict[str, object] | None
        grounding_output: dict[str, object] | None
        audits: list[dict[str, object]]
        if prepared.state != "ready":
            state = "blocked"
            student_output = {
                "state": "blocked",
                "reproducedSteps": [],
                "attemptedOptionId": None,
                "blockedAt": "还没有形成可执行的讲解",
                "clarificationQuestion": "你能先说出遇到这类题第一步要观察什么吗？",
            }
            grounding_output = None
            audits = []
        else:
            try:
                result = self._provider_runner(shadow_input)
            except RuntimeError:
                state = "provider_failed"
                student_output = None
                grounding_output = None
                audits = []
            else:
                audits = [
                    item.model_dump(mode="json", by_alias=True) for item in result.call_audits
                ]
                if result.state != "displayable":
                    state = "blocked"
                    student_output = None
                    grounding_output = None
                else:
                    student = result.student_outputs[0]
                    grounding = result.grounding_candidates[0]
                    student_output = student.model_dump(mode="json", by_alias=True)
                    grounding_output = grounding.model_dump(mode="json", by_alias=True)
                    state = (
                        "ready_for_transfer"
                        if student.state == "reproduced"
                        and grounding.next_action == "ready_for_transfer"
                        else "blocked"
                    )
        authority = {
            "compiledPackageFingerprint": package.fingerprint,
            "package": package.model_dump(mode="json", by_alias=True),
            "visualSidecar": sidecar.model_dump(mode="json", by_alias=True),
            "method": {
                "title": method.title,
                "steps": list(method.method_steps),
                "pitfall": method.pitfall,
            },
        }
        row = self._repository.insert_initial(
            session,
            values={
                "session_version_id": str(uuid4()),
                "session_id": str(uuid4()),
                "command_id": command.command_id,
                "request_fingerprint": request_fingerprint,
                "user_id": user_id,
                "state": state,
                "review_id": review_id,
                "source_question_id": command.source_question_id,
                "package_fingerprint": runtime_fingerprint,
                "package_authority": authority,
                "user_explanation": command.user_explanation,
                "student_output": student_output,
                "grounding_output": grounding_output,
                "call_audits": audits,
                "created_at": recorded_at,
            },
        )
        return self._project(row)

    def get(self, session: Session, *, user_id: str, session_id: str) -> TeachBackSessionProjection:
        row = self._repository.current(session, user_id=user_id, session_id=session_id)
        if row is None:
            raise TeachBackRuntimeError(
                "teach_back_not_found", "这次讲给岸岸的记录不存在。", status_code=404
            )
        return self._project(row)

    def transfer(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
        command: TeachBackTransferCommand,
        now: datetime | None = None,
    ) -> TeachBackTransferReceipt:
        recorded_at = now or datetime.now(UTC)
        request_fingerprint = _fingerprint(
            {
                "operation": "teach_back_transfer",
                "sessionId": session_id,
                "revision": command.revision,
                "selectedOptionIds": sorted(command.selected_option_ids),
                "confidence": command.confidence,
            }
        )
        replay = self._repository.by_command(
            session, user_id=user_id, command_id=command.command_id
        )
        if replay is not None:
            _require_fingerprint(replay, request_fingerprint)
            return self._receipt(session, user_id=user_id, row=replay)
        command_replay = self._repository.xingce_command(
            session, user_id=user_id, command_id=command.command_id
        )
        if command_replay is not None:
            _require_fingerprint(command_replay, request_fingerprint)
            raise TeachBackRuntimeError(
                "teach_back_transfer_incomplete",
                "这次迁移作答尚未形成完整记录，请稍后刷新。",
                status_code=409,
            )
        current = self._repository.current(
            session, user_id=user_id, session_id=session_id, for_update=True
        )
        if current is None:
            raise TeachBackRuntimeError(
                "teach_back_not_found", "这次讲给岸岸的记录不存在。", status_code=404
            )
        if int(current["revision"]) != command.revision:
            raise TeachBackRuntimeError(
                "teach_back_revision_conflict", "页面已经更新，请刷新后继续。", status_code=409
            )
        if str(current["state"]) != "ready_for_transfer":
            raise TeachBackRuntimeError(
                "teach_back_not_ready", "岸岸还没有复现成功，暂时不能进入迁移题。", status_code=409
            )
        authority = dict(current["package_authority"])
        package = dict(authority["package"])
        question_id = str(package["userTransferQuestionRef"])
        answer_key = sorted(str(item) for item in package["userTransferAnswerKey"])
        correct = sorted(command.selected_option_ids) == answer_key
        self._repository.insert_xingce_command(
            session,
            user_id=user_id,
            command_id=command.command_id,
            session_id=session_id,
            request_fingerprint=request_fingerprint,
            response_payload={"sessionId": session_id, "state": "completed"},
            created_at=recorded_at,
        )
        mastery = self._mastery_service.record_teach_back_transfer(
            session,
            user_id=user_id,
            review_id=str(current["review_id"]),
            command_id=command.command_id,
            question_id=question_id,
            correct=correct,
            confidence=command.confidence,
            evidence_ref=f"teach-back-session:{session_id}:revision:{command.revision}",
            recorded_at=recorded_at,
        )
        completed = self._repository.insert_completed(
            session,
            values={
                "session_version_id": str(uuid4()),
                "session_id": session_id,
                "command_id": command.command_id,
                "request_fingerprint": request_fingerprint,
                "user_id": user_id,
                "revision": command.revision + 1,
                "review_id": str(current["review_id"]),
                "source_question_id": str(current["source_question_id"]),
                "package_fingerprint": str(current["package_fingerprint"]),
                "package_authority": authority,
                "user_explanation": str(current["user_explanation"]),
                "student_output": current["student_output"],
                "grounding_output": current["grounding_output"],
                "call_audits": current["call_audits"],
                "transfer_selected_option_ids": command.selected_option_ids,
                "transfer_confidence": command.confidence,
                "transfer_correct": correct,
                "mastery_version_id": str(mastery["mastery_version_id"]),
                "supersedes_session_version_id": str(current["session_version_id"]),
                "created_at": recorded_at,
            },
        )
        return self._receipt(session, user_id=user_id, row=completed, mastery=mastery)

    def _run_provider(self, command: ShadowPreparationInput) -> TeachBackProviderShadowResult:
        provider = TeachBackShadowProvider(_provider_config(self._settings))
        return run_provider_shadow(
            command,
            provider=provider,
            price_card=ModelPriceCard.from_settings(self._settings),
        )

    def _project(self, row: RowMapping) -> TeachBackSessionProjection:
        state = str(row["state"])
        student_raw = row["student_output"]
        grounding_raw = row["grounding_output"]
        authority = dict(row["package_authority"])
        package = dict(authority["package"])
        sidecar = dict(authority["visualSidecar"])
        student = (
            TeachBackStudentResult.model_validate(student_raw) if student_raw is not None else None
        )
        grounding = [
            TeachBackGroundingClaim(
                classification=str(item["classification"]),
                summary=str(item["summary"]),
            )
            for item in ((grounding_raw or {}).get("claims") or [])
        ]
        transfer = None
        if state == "ready_for_transfer":
            transfer = TeachBackQuestion(
                questionId=package["userTransferQuestionRef"],
                stem=package["userTransferStem"],
                options=[_public_option(value) for value in package["userTransferOptions"]],
                visualEvidence=sidecar.get("userTransferVisualEvidence") or [],
            )
        return TeachBackSessionProjection(
            sessionId=row["session_id"],
            revision=int(row["revision"]),
            state=state,
            reviewId=row["review_id"],
            sourceQuestionId=str(row["source_question_id"]),
            coachMessage=_coach_message(state, student),
            student=student,
            grounding=grounding,
            transferQuestion=transfer,
            createdAt=row["created_at"],
        )

    def _receipt(
        self,
        session: Session,
        *,
        user_id: str,
        row: RowMapping,
        mastery: RowMapping | None = None,
    ) -> TeachBackTransferReceipt:
        if str(row["state"]) != "completed" or row["mastery_version_id"] is None:
            raise TeachBackRuntimeError(
                "teach_back_transfer_incomplete", "迁移作答尚未完成。", status_code=409
            )
        mastery_row = mastery or self._repository.mastery_version(
            session,
            user_id=user_id,
            mastery_version_id=str(row["mastery_version_id"]),
        )
        if mastery_row is None:
            raise TeachBackRuntimeError(
                "teach_back_mastery_missing", "迁移结果尚未同步到复测队列。", status_code=409
            )
        correct = bool(row["transfer_correct"])
        return TeachBackTransferReceipt(
            sessionId=row["session_id"],
            revision=int(row["revision"]),
            correct=correct,
            masteryVersionId=mastery_row["mastery_version_id"],
            masteryState=str(mastery_row["state"]),
            verificationStage=str(mastery_row["verification_stage"]),
            dueAt=mastery_row["due_at"],
            coachMessage=(
                "你讲的方法能迁移到新题，我已经把这次证据接回复测计划。"
                if correct
                else "这次迁移还没站稳，我已按真实结果调整复测，不会把它算成掌握。"
            ),
        )


def _public_option(value: object) -> dict[str, str]:
    rendered = str(value).strip()
    head, separator, tail = rendered.partition(". ")
    if not separator:
        head, separator, tail = rendered.partition("．")
    option_id = head.strip()
    if not option_id or not tail.strip():
        raise TeachBackRuntimeError(
            "teach_back_question_invalid",
            "迁移题选项格式不完整，暂时不展示。",
            status_code=409,
        )
    return {"optionId": option_id, "label": option_id, "text": tail.strip()}


def _provider_config(settings: Settings) -> TeachBackProviderConfig:
    candidates = (
        (
            settings.ai_companion_provider_base_url,
            settings.ai_companion_api_key,
            settings.ai_companion_provider_release_id,
            settings.ai_companion_model or settings.ai_model,
            settings.ai_companion_provider_https_host_allowlist,
            settings.ai_companion_provider_idempotency_capability,
            settings.ai_companion_provider_idempotency_key_header,
            settings.ai_companion_provider_request_id_header,
            settings.ai_companion_provider_replay_retention_seconds,
        ),
        (
            settings.ai_companion_deepseek_provider_base_url,
            settings.ai_companion_deepseek_api_key,
            settings.ai_companion_deepseek_provider_release_id,
            settings.ai_companion_deepseek_model,
            settings.ai_companion_deepseek_provider_https_host_allowlist,
            settings.ai_companion_deepseek_provider_idempotency_capability,
            settings.ai_companion_deepseek_provider_idempotency_key_header,
            settings.ai_companion_deepseek_provider_request_id_header,
            settings.ai_companion_deepseek_provider_replay_retention_seconds,
        ),
        (
            settings.ai_companion_ark_provider_base_url,
            settings.ai_companion_ark_api_key,
            settings.ai_companion_ark_provider_release_id,
            settings.ai_companion_ark_model,
            settings.ai_companion_ark_provider_https_host_allowlist,
            settings.ai_companion_ark_provider_idempotency_capability,
            settings.ai_companion_ark_provider_idempotency_key_header,
            settings.ai_companion_ark_provider_request_id_header,
            settings.ai_companion_ark_provider_replay_retention_seconds,
        ),
    )
    chosen = next(
        (item for item in candidates if all(str(value).strip() for value in item[:5])),
        candidates[0],
    )
    (
        endpoint,
        api_key,
        release,
        model,
        allowlist,
        capability,
        key_header,
        request_source,
        retention,
    ) = chosen
    return TeachBackProviderConfig(
        endpoint=str(endpoint),
        api_key=str(api_key),
        provider_release_id=str(release),
        model=str(model),
        https_host_allowlist=tuple(
            part.strip() for part in str(allowlist).split(",") if part.strip()
        ),
        idempotency_capability=str(capability),
        idempotency_key_header=str(key_header),
        request_id_source=str(request_source),
        replay_retention_seconds=int(retention),
        max_input_bytes=max(int(settings.ai_companion_max_input_bytes), 800_000),
        max_output_bytes=max(int(settings.ai_companion_max_output_bytes), 100_000),
        timeout_seconds=float(settings.ai_learning_brain_agent_timeout_seconds),
    )


def _coach_message(state: str, student: TeachBackStudentResult | None) -> str:
    if state == "ready_for_transfer":
        return "我已经只按你刚才讲的步骤复现出来了。现在用一道新题验证能不能迁移。"
    if state == "blocked" and student and student.clarification_question:
        return student.clarification_question
    if state == "provider_failed":
        return "你的讲解已经保存，岸岸这次没能完成复现；稍后可用同一内容重试。"
    if state == "completed":
        return "这次讲解与迁移已完成，结果已经接回原来的复测计划。"
    return "这次讲解还不足以完成复现，请补充一个关键步骤。"


def _fingerprint(value: dict[str, object]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require_fingerprint(row: RowMapping, expected: str) -> None:
    if str(row["request_fingerprint"]) != expected:
        raise TeachBackRuntimeError(
            "idempotency_key_reused", "同一命令不能用于不同的讲解内容。", status_code=409
        )


def _assembly_error(
    reason: CoachBrickAssemblyReason | None,
) -> TeachBackRuntimeError:
    if reason == CoachBrickAssemblyReason.REVIEW_NOT_FOUND:
        return TeachBackRuntimeError("xingce_review_not_found", "行测复盘不存在。", status_code=404)
    if reason == CoachBrickAssemblyReason.SOURCE_NOT_IN_REVIEW:
        return TeachBackRuntimeError(
            "teach_back_source_invalid",
            "只能从这次真实错题开始讲给岸岸。",
            status_code=409,
        )
    return TeachBackRuntimeError(
        "teach_back_unavailable",
        assembly_reason_message(reason) or "这个考点的教学积木还没齐，暂时不让 AI 猜。",
        status_code=409,
    )
