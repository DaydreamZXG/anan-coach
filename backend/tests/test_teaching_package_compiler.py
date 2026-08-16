from __future__ import annotations

from copy import deepcopy

import pytest

from app.domains.teaching_packages import (
    CompileReasonCode,
    CompileState,
    CompileTeachingPackageInput,
    ExamTargetScope,
    ReusableMethodAsset,
    TeachingQuestionCandidate,
    compile_teaching_package,
)


SCOPES = (
    ExamTargetScope(
        examCode="guokao",
        regionCode="national",
        year=2026,
        categoryCode="general",
        subjectCode="xingce",
    ),
    ExamTargetScope(
        examCode="shengkao",
        regionCode="xinjiang",
        year=2026,
        categoryCode="general",
        subjectCode="xingce",
    ),
    ExamTargetScope(
        examCode="sydw",
        regionCode="xinjiang",
        year=2026,
        categoryCode="a",
        subjectCode="zhice",
    ),
)


def method_asset(
    **overrides: object,
) -> ReusableMethodAsset:
    values: dict[str, object] = {
        "methodCardRef": "method-card:growth-rate-base:v7",
        "excludedExamCodes": (),
        "microSkillId": "growth-rate-base",
        "title": "增长率分母选择",
        "recognition": "题目给出现期量和增长量，要求计算增长率。",
        "methodSteps": ("确认现期量与增长量", "计算基期量", "用增长量除以基期量"),
        "applicability": ("求同比或环比增长率",),
        "exclusions": ("题目直接给出基期量时不重复反推",),
        "pitfalls": ("把现期量误当成分母",),
    }
    values.update(overrides)
    return ReusableMethodAsset.model_validate(values)


def question(
    scope: ExamTargetScope,
    question_ref: str,
    **overrides: object,
) -> TeachingQuestionCandidate:
    values: dict[str, object] = {
        "questionRef": question_ref,
        "sourceScopeKey": scope.scope_key,
        "microSkillId": "growth-rate-base",
        "exposureState": "unseen",
        "stem": f"{question_ref}：某指标现期为 120，增长量为 20，增长率约为多少？",
        "options": ("A. 16.7%", "B. 20%", "C. 25%", "D. 30%"),
        "answerKey": ("B",),
        "explanation": "基期量为 100，增长率为 20/100=20%。",
    }
    values.update(overrides)
    return TeachingQuestionCandidate.model_validate(values)


def compile_input(
    scope: ExamTargetScope,
    *,
    method: ReusableMethodAsset | None = None,
    candidates: tuple[TeachingQuestionCandidate, ...] | None = None,
) -> CompileTeachingPackageInput:
    return CompileTeachingPackageInput(
        targetScopeKey=scope.scope_key,
        microSkillId="growth-rate-base",
        sourceQuestionRef="question:source:v1",
        methodAsset=method or method_asset(),
        questionCandidates=candidates
        or (
            question(scope, "question:student:v1"),
            question(scope, "question:transfer:v1"),
        ),
    )


@pytest.mark.parametrize("scope", SCOPES, ids=("guokao", "shengkao", "sydw"))
def test_exact_scopes_compile_with_one_shared_implementation(scope: ExamTargetScope) -> None:
    result = compile_teaching_package(compile_input(scope))

    assert result.state == CompileState.TEACHABLE
    assert result.reason_codes == ()
    assert len(result.package_candidates) == 1
    package = result.package_candidates[0]
    assert package.target_scope_key == scope.scope_key
    assert package.student_question_ref != package.user_transfer_question_ref
    assert package.fingerprint == compile_teaching_package(compile_input(scope)).package_candidates[0].fingerprint


@pytest.mark.parametrize("target_scope", SCOPES[1:], ids=("shengkao", "sydw"))
def test_one_existing_method_asset_reuses_across_all_exam_projects(
    target_scope: ExamTargetScope,
) -> None:
    existing_guokao_asset = method_asset()
    guokao_questions = (
        question(SCOPES[0], "question:student:v1"),
        question(SCOPES[0], "question:transfer:v1"),
    )
    result = compile_teaching_package(
        compile_input(target_scope, method=existing_guokao_asset, candidates=guokao_questions)
    )

    assert result.state == CompileState.TEACHABLE
    assert result.package_candidates[0].target_scope_key == target_scope.scope_key
    assert result.package_candidates[0].method_card_ref == existing_guokao_asset.method_card_ref


def test_only_explicit_exam_exclusion_blocks_method_reuse() -> None:
    scope = SCOPES[1]
    result = compile_teaching_package(
        compile_input(scope, method=method_asset(excludedExamCodes=("shengkao",)))
    )

    assert result.state == CompileState.UNAVAILABLE
    assert result.package_candidates == ()
    assert result.reason_codes == (CompileReasonCode.METHOD_EXCLUDED_FOR_EXAM,)


def test_source_seen_and_duplicate_questions_cannot_fill_two_slots() -> None:
    scope = SCOPES[0]
    eligible = question(scope, "question:eligible:v1")
    result = compile_teaching_package(
        compile_input(
            scope,
            candidates=(
                question(scope, "question:source:v1"),
                question(scope, "question:seen:v1", exposureState="seen"),
                eligible,
                eligible,
            ),
        )
    )

    assert result.state == CompileState.UNAVAILABLE
    assert result.package_candidates == ()
    assert CompileReasonCode.QUESTION_EXPOSED in result.reason_codes
    assert CompileReasonCode.INSUFFICIENT_DISTINCT_QUESTIONS in result.reason_codes


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    (
        ({"answerKey": ("",)}, CompileReasonCode.ANSWER_UNAVAILABLE),
        ({"explanation": ""}, CompileReasonCode.EXPLANATION_UNAVAILABLE),
        ({"microSkillId": "another-skill"}, CompileReasonCode.INSUFFICIENT_DISTINCT_QUESTIONS),
    ),
)
def test_ineligible_question_fails_closed_without_partial_package(
    overrides: dict[str, object],
    expected_reason: CompileReasonCode,
) -> None:
    scope = SCOPES[0]
    result = compile_teaching_package(
        compile_input(
            scope,
            candidates=(
                question(scope, "question:student:v1"),
                question(scope, "question:invalid:v1", **overrides),
            ),
        )
    )

    assert result.state == CompileState.UNAVAILABLE
    assert result.package_candidates == ()
    assert expected_reason in result.reason_codes
    assert CompileReasonCode.INSUFFICIENT_DISTINCT_QUESTIONS in result.reason_codes


def test_safe_projections_never_contain_answer_or_explanation() -> None:
    package = compile_teaching_package(compile_input(SCOPES[0])).package_candidates[0]

    for safe_question in (package.student_question, package.user_transfer_question):
        payload = safe_question.model_dump(mode="json", by_alias=True)
        assert set(payload) == {"questionRef", "stem", "options"}
        assert "answerKey" not in payload
        assert "explanation" not in payload

    assert package.student_answer_context.question_ref == package.student_question.question_ref
    assert package.user_transfer_answer_context.question_ref == package.user_transfer_question.question_ref


def test_compilation_is_deterministic_order_independent_and_does_not_mutate_input() -> None:
    scope = SCOPES[0]
    candidates = (
        question(scope, "question:z:v1"),
        question(scope, "question:a:v1"),
        question(scope, "question:ignored:v1", exposureState="seen"),
    )
    command = compile_input(scope, candidates=candidates)
    before = deepcopy(command.model_dump(mode="json", by_alias=True))

    first = compile_teaching_package(command)
    second = compile_teaching_package(compile_input(scope, candidates=tuple(reversed(candidates))))

    assert first == second
    assert command.model_dump(mode="json", by_alias=True) == before
    package = first.package_candidates[0]
    assert package.student_question_ref == "question:a:v1"
    assert package.user_transfer_question_ref == "question:z:v1"
