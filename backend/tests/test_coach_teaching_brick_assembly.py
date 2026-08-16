from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domains.coach_brick_assembly import (
    CoachBrickAssemblyReason,
    CoachTeachingBrickAssembler,
    assembly_reason_message,
    project_exam_target_scope,
)
from app.domains.teaching_packages import TeachingQuestionVisualBinding
from app.domains.xingce_learning.rescue.repository import (
    RescueMethodCardRow,
    RescueNodeLookup,
    RescueSimilarQuestionRow,
    RescueTargetProfile,
)


NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
SOURCE_ID = "wrong-source"


@pytest.mark.parametrize(
    ("profile", "expected"),
    (
        (
            RescueTargetProfile("guokao", "新疆", "行政执法岗位"),
            "guokao:national:2026:administrative_enforcement:xingce",
        ),
        (
            RescueTargetProfile("shengkao", "新疆维吾尔自治区", "县乡类"),
            "shengkao:xinjiang:2026:county_township:xingce",
        ),
        (
            RescueTargetProfile("事业单位", "新疆", "职测 A 类"),
            "sydw:xinjiang:2026:category_a:zhice",
        ),
    ),
)
def test_target_scope_comes_from_explicit_profile(
    profile: RescueTargetProfile,
    expected: str,
) -> None:
    scope = project_exam_target_scope(profile, year=NOW.year)
    assert scope is not None
    assert scope.scope_key == expected


def test_unknown_target_exam_does_not_silently_become_guokao() -> None:
    assert (
        project_exam_target_scope(
            RescueTargetProfile("还没想好", None, None),
            year=NOW.year,
        )
        is None
    )


def test_missing_target_uses_the_actual_active_task_release_without_a_profile_gate() -> None:
    class _NewLearnerRepository(_Repository):
        target_exam = None
        target_region = None
        position_group = None

    result = CoachTeachingBrickAssembler(
        rescue_repository=_NewLearnerRepository,
        visual_reader=_VisualReader(),
    ).assemble(
        object(),
        user_id="new-user",
        review_id="review-1",
        source_question_id=SOURCE_ID,
        now=NOW,
    )

    assert result.available is True
    assert result.target_scope is not None
    assert result.target_scope.scope_key == "guokao:national:2025:general:xingce"


class _Repository:
    target_exam = "shengkao"
    target_region = "新疆"
    position_group = "行政执法"
    visual_required = False

    def __init__(self, _session) -> None:
        pass

    def current_review(self, *, user_id: str, review_id: str):
        return {"session_id": "session-1"}

    def wrong_question_ids_for_review(self, *, user_id: str, session_id: str):
        return (SOURCE_ID,)

    def target_profile(self, *, user_id: str):
        return RescueTargetProfile(
            self.target_exam,
            self.target_region,
            self.position_group,
        )

    def resolve_fresh_release(self):
        return {
            "release_id": "release-1",
            "exam_scope": "guokao",
            "track": "xingce",
            "generated_at": datetime(2025, 12, 1, tzinfo=UTC),
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
                method_steps=("先求基期量", "再求增长率"),
                pitfall="不要用现期量做分母",
                fix_action="做迁移题",
                time_budget_sec=60,
            )
        }

    def select_similar_question(self, **_kwargs):
        return [
            _question("transfer-a", "B", visual_required=self.visual_required),
            _question("transfer-b", "C", visual_required=self.visual_required),
        ]


class _VisualReader:
    def binding_for_question(
        self,
        _session,
        *,
        question_ref: str,
        visual_required: bool,
    ):
        return TeachingQuestionVisualBinding(
            questionRef=question_ref,
            contentVersionId=f"content:{question_ref}",
            visualRequired=visual_required,
            sourceAssetSha256s=(),
            evidence=(),
        )


def _question(
    question_id: str,
    answer: str,
    *,
    visual_required: bool,
) -> RescueSimilarQuestionRow:
    return RescueSimilarQuestionRow(
        question_id=question_id,
        content_version_id=f"content:{question_id}",
        module_name="资料分析",
        stem=f"{question_id} 的增长率是多少？",
        options=tuple(
            {"label": label, "option_text": f"{index * 10}%"}
            for index, label in enumerate(("A", "B", "C", "D"), start=1)
        ),
        correct_option_ids=(answer,),
        explanation=f"{question_id} 的官方解析",
        visual_required=visual_required,
    )


def test_one_assembly_compiles_cross_exam_target_and_complete_text_bricks() -> None:
    result = CoachTeachingBrickAssembler(
        rescue_repository=_Repository,
        visual_reader=_VisualReader(),
    ).assemble(
        object(),
        user_id="user-1",
        review_id="review-1",
        source_question_id=SOURCE_ID,
        now=NOW,
    )

    assert result.available is True
    assert result.reason is None
    assert result.package is not None
    assert (
        result.package.target_scope_key
        == "shengkao:xinjiang:2026:administrative_enforcement:xingce"
    )
    assert result.package.student_question_ref != result.package.user_transfer_question_ref
    assert result.visual_sidecar is not None
    assert result.visual_sidecar.student_visual_evidence == ()


def test_missing_visual_semantics_closes_only_teach_back_enhancement() -> None:
    class _VisualRepository(_Repository):
        visual_required = True

    result = CoachTeachingBrickAssembler(
        rescue_repository=_VisualRepository,
        visual_reader=_VisualReader(),
    ).assemble(
        object(),
        user_id="user-1",
        review_id="review-1",
        source_question_id=SOURCE_ID,
        now=NOW,
    )

    assert result.available is False
    assert result.reason == CoachBrickAssemblyReason.VISUAL_EVIDENCE_UNAVAILABLE
    message = assembly_reason_message(result.reason)
    assert message is not None
    assert "原图和官方解析" in message
    assert "讲给岸岸" in message
