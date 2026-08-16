from __future__ import annotations

import hashlib
import json
from app.domains.teaching_packages.contracts import (
    CompileReasonCode,
    CompileState,
    CompileTeachingPackageInput,
    CompileTeachingPackageResult,
    CompiledTeachingPackage,
    TeachingQuestionCandidate,
)


def _method_reasons(command: CompileTeachingPackageInput) -> set[CompileReasonCode]:
    exam_code = command.target_scope_key.split(":", maxsplit=1)[0]
    if exam_code in command.method_asset.excluded_exam_codes:
        return {CompileReasonCode.METHOD_EXCLUDED_FOR_EXAM}
    return set()


def _question_reasons(
    command: CompileTeachingPackageInput,
    candidate: TeachingQuestionCandidate,
) -> set[CompileReasonCode]:
    reasons: set[CompileReasonCode] = set()
    if candidate.exposure_state != "unseen":
        reasons.add(CompileReasonCode.QUESTION_EXPOSED)
    if candidate.micro_skill_id != command.micro_skill_id:
        reasons.add(CompileReasonCode.INSUFFICIENT_DISTINCT_QUESTIONS)
    if any(not value.strip() for value in candidate.answer_key):
        reasons.add(CompileReasonCode.ANSWER_UNAVAILABLE)
    if not candidate.explanation.strip():
        reasons.add(CompileReasonCode.EXPLANATION_UNAVAILABLE)
    return reasons


def _ordered_reasons(reasons: set[CompileReasonCode]) -> tuple[CompileReasonCode, ...]:
    return tuple(reason for reason in CompileReasonCode if reason in reasons)


def _unavailable(reasons: set[CompileReasonCode]) -> CompileTeachingPackageResult:
    return CompileTeachingPackageResult(
        state=CompileState.UNAVAILABLE,
        reasonCodes=_ordered_reasons(reasons),
        packageCandidates=(),
    )


def _fingerprint(
    command: CompileTeachingPackageInput,
    selected: tuple[TeachingQuestionCandidate, TeachingQuestionCandidate],
) -> str:
    canonical_input = {
        "targetScopeKey": command.target_scope_key,
        "microSkillId": command.micro_skill_id,
        "sourceQuestionRef": command.source_question_ref,
        "methodAsset": command.method_asset.model_dump(mode="json", by_alias=True),
        "selectedQuestions": [
            item.model_dump(mode="json", by_alias=True) for item in selected
        ],
    }
    payload = json.dumps(
        canonical_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compile_teaching_package(
    command: CompileTeachingPackageInput,
) -> CompileTeachingPackageResult:
    """Compile one complete package without I/O, fallback, mutation, or partial output."""

    method_reasons = _method_reasons(command)
    if method_reasons:
        return _unavailable(method_reasons)

    eligible_by_ref: dict[str, TeachingQuestionCandidate] = {}
    rejected_reasons: set[CompileReasonCode] = set()
    for candidate in command.question_candidates:
        if candidate.question_ref == command.source_question_ref:
            continue
        reasons = _question_reasons(command, candidate)
        if reasons:
            rejected_reasons.update(reasons)
            continue
        eligible_by_ref.setdefault(candidate.question_ref, candidate)

    ordered = tuple(eligible_by_ref[key] for key in sorted(eligible_by_ref))
    if len(ordered) < 2:
        rejected_reasons.add(CompileReasonCode.INSUFFICIENT_DISTINCT_QUESTIONS)
        return _unavailable(rejected_reasons)

    selected = (ordered[0], ordered[1])
    package = CompiledTeachingPackage(
        fingerprint=_fingerprint(command, selected),
        targetScopeKey=command.target_scope_key,
        microSkillId=command.micro_skill_id,
        methodCardRef=command.method_asset.method_card_ref,
        studentQuestionRef=selected[0].question_ref,
        studentStem=selected[0].stem,
        studentOptions=selected[0].options,
        studentAnswerKey=selected[0].answer_key,
        studentExplanation=selected[0].explanation,
        userTransferQuestionRef=selected[1].question_ref,
        userTransferStem=selected[1].stem,
        userTransferOptions=selected[1].options,
        userTransferAnswerKey=selected[1].answer_key,
        userTransferExplanation=selected[1].explanation,
    )
    return CompileTeachingPackageResult(
        state=CompileState.TEACHABLE,
        reasonCodes=(),
        packageCandidates=(package,),
    )
