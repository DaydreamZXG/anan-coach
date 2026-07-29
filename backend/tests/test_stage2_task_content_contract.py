from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.domains.task_content.service import (
    TaskContentContractError,
    freeze_learner_bundle,
)
from app.domains.mock_exam.service import (
    MockExamUnavailableError,
    _validate_single_choice_snapshot,
)
from app.domains.xingce_learning.content_version import question_content_version_id
from app.domains.xingce_learning.real_attempt.schemas import (
    AnswerCheckpoint,
    ReportQuestionDisputeCommand,
)


def test_rich_content_contract_supports_long_material_merged_8_column_table_and_original_image() -> None:
    header = '<tr><th colspan="8">2026年八区数据</th></tr>'
    columns = '<tr><th rowspan="2">列1</th>' + "".join(
        f"<th>列{index}</th>" for index in range(2, 9)
    ) + "</tr>"
    values = "<tr>" + "".join(
        f"<td>{index * 100}</td>" for index in range(2, 9)
    ) + "</tr>"
    long_material = "材料段落。" * 2_000
    source = _source(
        material_text=long_material,
        material_html=(
            f"<p>{long_material}</p><p>增长率为 $\\frac{{a-b}}{{b}}$</p>"
            f"<table>{header}{columns}{values}</table>"
        ),
        visual_required=True,
        assets=[
            {
                "asset_id": "asset-high-resolution",
                "part_kind": "chart",
                "part_index": 0,
                "image_ordinal": 0,
                "image_src": (
                    "https://kg.xingguoxian.cn/assets/questions/"
                    "q-stage2-4096x2160.png"
                ),
                "object_key": "questions/q-stage2-4096x2160.png",
                "is_representative_asset": True,
            }
        ],
    )

    bundle, scoring = freeze_learner_bundle(
        [source],
        scoring_policy_version="test-v1",
        content_version_id=question_content_version_id,
        published_explanation=lambda item: ("secret explanation", "verified"),
    )

    material = bundle.materials[0]
    table = next(block for block in material.content if block.type == "table")
    graph = next(block for block in material.content if block.type == "graph")
    formula = next(block for block in material.content if block.type == "formula")
    assert table.column_count == 8
    assert any(cell.column_span == 8 and cell.header for cell in table.cells)
    assert any(cell.row_span == 2 and cell.header for cell in table.cells)
    assert graph.original_ocr_url.endswith("q-stage2-4096x2160.png")
    assert graph.zoomable is True
    assert formula.expression == r"\frac{a-b}{b}"
    assert all(
        "$" not in block.text
        for block in material.content
        if block.type == "paragraph"
    )
    assert {anchor.block_id for anchor in material.anchors} == {
        block.block_id for block in material.content
    }

    learner_payload = bundle.model_dump(by_alias=True, mode="json")
    encoded = json.dumps(learner_payload, ensure_ascii=False)
    assert "secret explanation" not in encoded
    assert '"answer"' not in encoded
    assert '"correctOptionIds"' not in encoded
    assert scoring["questions"][0]["correctOptionIds"] == ["B"]


def test_rich_content_contract_publishes_a_valid_imported_question_object_key() -> None:
    object_key = (
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/资料分析/图 1.png"
    )
    source = _source(
        visual_required=True,
        assets=[
            {
                "asset_id": "asset-imported",
                "part_kind": "chart",
                "part_index": 0,
                "image_ordinal": 0,
                "image_src": "files/资料分析/图 1.png",
                "object_key": object_key,
                "is_representative_asset": True,
            }
        ],
    )

    bundle, _ = freeze_learner_bundle(
        [source],
        scoring_policy_version="test-v1",
        content_version_id=question_content_version_id,
        published_explanation=lambda item: ("secret explanation", "verified"),
    )

    assert bundle.materials[0].assets[0].url == (
        "https://kg.xingguoxian.cn/assets/"
        "data/question-bank-export/files/source-question-bank/"
        "fenbi-final-analysis-v1-20260613-assets/"
        "%E8%B5%84%E6%96%99%E5%88%86%E6%9E%90/%E5%9B%BE%201.png"
    )


def test_rich_content_contract_rejects_an_object_outside_the_published_prefix() -> None:
    source = _source(
        visual_required=True,
        assets=[
            {
                "asset_id": "asset-private",
                "part_kind": "chart",
                "part_index": 0,
                "image_ordinal": 0,
                "image_src": "files/private.png",
                "object_key": "private/users/user-1/private.png",
                "is_representative_asset": True,
            }
        ],
    )

    with pytest.raises(TaskContentContractError, match="no published learner-safe image"):
        freeze_learner_bundle(
            [source],
            scoring_policy_version="test-v1",
            content_version_id=question_content_version_id,
            published_explanation=lambda item: ("secret explanation", "verified"),
        )


def test_rich_content_contract_preserves_underlined_blanks_from_the_source_stem() -> None:
    source = _source(
        stem=(
            "除了至今的电车难题外，还有价值观问题——当 AI 拥有自主设定目标的能力时，"
            "如何确保目标符合人类利益、与人类价值观相符？"
        ),
        stem_html=(
            "<p>除了至今的“电车难题”外，还有日益重要的价值观问题"
            "<u>&nbsp;&nbsp;&nbsp;&nbsp;</u>当AI拥有自主设定目标的能力时，"
            "又该如何确保这些目标符合人类利益、与人类价值观相"
            "<u>&nbsp;&nbsp;</u>？依次填入画横线部分最恰当的一项是：</p>"
        ),
    )

    bundle, _ = freeze_learner_bundle(
        [source],
        scoring_policy_version="test-v1",
        content_version_id=question_content_version_id,
        published_explanation=lambda item: ("secret explanation", "verified"),
    )

    stem = bundle.questions[0].stem_content[0]
    assert stem.type == "paragraph"
    assert stem.html is not None
    assert stem.html.count("<u>") == 2
    assert stem.html.count("</u>") == 2
    assert stem.text.count("____") == 2


def test_dispute_command_rejects_client_supplied_answer_or_evidence() -> None:
    with pytest.raises(ValidationError):
        ReportQuestionDisputeCommand.model_validate(
            {
                "questionId": "q-stage2",
                "contentVersionId": "a" * 64,
                "category": "answer_error",
                "description": "答案与题意不一致",
                "answer": "B",
            }
        )


def test_answer_checkpoint_can_persist_material_position_mark_and_elimination_only() -> None:
    checkpoint = AnswerCheckpoint.model_validate(
        {
            "questionId": "q-stage2",
            "selectedOptionIds": [],
            "eliminatedOptionIds": ["A", "C"],
            "marked": True,
            "skipped": False,
            "elapsedSeconds": 8,
            "draftText": "",
            "materialPosition": {
                "anchorId": "anchor-table-1",
                "scrollTop": 1200,
                "zoom": 2.25,
            },
        }
    )
    assert checkpoint.selected_option_ids == []
    assert checkpoint.eliminated_option_ids == ["A", "C"]
    assert checkpoint.marked is True
    assert checkpoint.material_position is not None
    assert checkpoint.material_position.scroll_top == 1200


def test_mock_scoring_snapshot_rejects_multiple_correct_options() -> None:
    with pytest.raises(MockExamUnavailableError):
        _validate_single_choice_snapshot(
            {"questions": [{"questionId": "q-multi", "correctOptionIds": ["A", "B"]}]}
        )
    with pytest.raises(ValidationError):
        ReportQuestionDisputeCommand.model_validate(
            {
                "questionId": "q-stage2",
                "contentVersionId": "a" * 64,
                "category": "image_unreadable",
                "description": "图片无法辨认",
                "evidenceContext": {"sessionRevision": 999},
            }
        )


def _source(**overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "question_id": "q-stage2",
        "module_name": "资料分析",
        "question_type": "single_choice",
        "stem": "根据材料，下列哪项正确？",
        "stem_html": "<p>根据材料，下列哪项正确？</p>",
        "answer": "B",
        "material_text": None,
        "material_html": None,
        "shared_context_required": True,
        "visual_required": False,
        "preferred_analysis": "secret explanation",
        "analysis_source": "verified",
        "analysis_status": "verified",
        "prefer_ai_analysis": False,
        "analysis_needs_review": False,
        "options": [
            {"label": label, "option_text": f"选项{label}", "option_html": None}
            for label in "ABCD"
        ],
        "assets": [],
    }
    source.update(overrides)
    return source
