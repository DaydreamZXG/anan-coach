from dataclasses import replace
from uuid import uuid4

import pytest

from app.domains.companion.repository import CompanionContextRecord
from app.domains.companion.schemas import CompanionAskRequest
from app.domains.companion.service import (
    _build_persona_prompt,
    _context_retrieval_candidates,
    _selected_citations,
)


def _context() -> CompanionContextRecord:
    return CompanionContextRecord(
        target_exam="guokao",
        position_group=None,
        target_score=131.8,
        baseline_xingce_score=None,
        baseline_shenlun_score=None,
        practice_answers=12,
        wrong_answers=3,
        mock_exams=1,
        latest_mock_score=118.5,
        top_wrong_modules=[{"module_name": "资料分析", "wrong_count": 2}],
        method_cards=[],
        current_shenlun_estimate=None,
        essay_attempts=0,
        grounded_essay_reviews=0,
        latest_essay_review=None,
        latest_essay_reflection=None,
        today_essay_plan=None,
        essay_plan_omission_reason=None,
        today_plan_tasks=[
            {
                "taskId": str(uuid4()),
                "track": "xingce",
                "stage": "objective_practice",
                "title": "行测提分练习：言语理解与表达",
                "reason": "当前模块需要继续训练",
                "estimatedMinutes": 60,
                "status": "pending",
            },
            {
                "taskId": str(uuid4()),
                "track": "essay",
                "stage": "essay_material_reading",
                "title": "建立申论材料地图",
                "reason": "先完成材料理解",
                "estimatedMinutes": 15,
                "status": "pending",
            },
        ],
        learning_brain_profile={
            "profileVersionId": str(uuid4()),
            "versionNo": 3,
            "ability": {
                "estimate": {
                    "total": {"recommended": 112},
                    "xingce": {"recommended": 62},
                    "shenlun": {"recommended": 50},
                }
            },
            "evidenceSummary": {
                "scoreEvidenceCount": 1,
                "xingceAttemptCount": 0,
                "xingceMasteryCount": 0,
                "essayMasteryCount": 0,
                "taskExecutionCount": 1,
                "externalAnswerCount": 0,
            },
            "decisionSummary": {"reviewDueCount": 0},
        },
    )


def _ask_body(question: str) -> dict[str, object]:
    return {
        "questionText": question,
        "context": {
            "routeId": "pages/anan/index",
            "workspace": "training",
            "objectRefs": [],
            "learnerStateVersion": "profile-1",
        },
    }


def test_short_meaningful_question_is_valid() -> None:
    request = CompanionAskRequest.model_validate(_ask_body("咋办"))

    assert request.question_text == "咋办"


def test_punctuation_only_question_is_invalid() -> None:
    with pytest.raises(ValueError, match="meaningful word or number"):
        CompanionAskRequest.model_validate(_ask_body("？？"))


def test_context_sources_separate_model_payload_from_public_summary() -> None:
    context = _context()
    candidates = _context_retrieval_candidates(context)
    summaries = {
        candidate.reference.kind: candidate.display_summary for candidate in candidates
    }

    assert summaries["learner_activity"] == (
        "历史累计已记录 12 道行测练习，其中 3 道错题；"
        "今日已记录 0 道作答，其中 0 道错题；已完成 1 次模考；"
        "最近一次模考记录为 118.5 分；目前错题较多的模块包括资料分析。"
    )
    assert summaries["learning_brain"] == (
        "当前综合能力参考值约 112 分（行测约 62 分、申论约 50 分）；"
        "本次判断汇总了 2 条已记录学习证据；当前没有待复测项目。"
    )
    assert summaries["daily_plan"] == (
        "今日共 2 项，预计 75 分钟：行测「行测提分练习：言语理解与表达」，60 分钟；"
        "申论「建立申论材料地图」，15 分钟。"
    )
    assert summaries["target_position"] == "国考：尚未确认具体主目标岗位，目标总分为 131.8 分。"
    assert all("{" not in (summary or "") for summary in summaries.values())


def test_today_plan_summary_groups_repeated_training() -> None:
    context = replace(
        _context(),
        today_plan_tasks=[
            {
                "track": "xingce",
                "title": "言语理解与表达",
                "estimatedMinutes": 30,
                "status": "pending",
            },
            {
                "track": "xingce",
                "title": "言语理解与表达（第 2 组）",
                "estimatedMinutes": 30,
                "status": "complete",
            },
            {
                "track": "xingce",
                "title": "言语理解与表达（第 3 组）",
                "estimatedMinutes": 30,
                "status": "pending",
            },
        ],
    )

    candidates = _context_retrieval_candidates(context)
    plan = next(candidate for candidate in candidates if candidate.reference.kind == "daily_plan")

    assert plan.reference.title == "今日计划概览"
    assert plan.display_summary == (
        "今日共 3 项，预计 90 分钟：行测「言语理解与表达」3 组，共 90 分钟，1 组已完成。"
    )


def test_selected_citation_uses_public_summary_instead_of_json_excerpt() -> None:
    source_id = "profile-current"
    bundle = {
        "bundleId": str(uuid4()),
        "policyVersion": "authorized-hybrid-retrieval@v1",
        "queryFingerprint": "a" * 64,
        "sources": [
            {
                "reference": {
                    "kind": "learning_brain",
                    "sourceId": source_id,
                    "version": "3",
                    "anchor": "profile-evidence",
                    "title": "当前学习档案",
                },
                "trustScope": "learning_brain",
                "excerpt": '{"algorithmVersion":"ability-estimate-v1"}',
                "displaySummary": "当前能力参考值约 112 分，依据仍较少。",
                "lexicalScore": 0.5,
                "semanticScore": 0.5,
                "sourceWeight": 1,
                "finalScore": 0.8,
                "untrustedInput": False,
            }
        ],
        "blockedSourceRefs": [],
        "securityDecision": "clear",
    }

    citations = _selected_citations(bundle, [source_id])

    assert citations[0].summary == "当前能力参考值约 112 分，依据仍较少。"
    assert "algorithmVersion" not in citations[0].summary


def test_persona_forbids_internal_identifiers_in_user_answers() -> None:
    prompt = _build_persona_prompt()

    assert "不得输出 JSON" in prompt
    assert "版本号或内部来源标识" in prompt
