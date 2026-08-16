from __future__ import annotations

from scripts.import_sydw_ab_reference import (
    _question_assets,
    _render_material,
    build_plan,
    canonical_module,
    completeness_issues,
    native_section,
)


def _record(
    *,
    paper_id: str,
    category: str = "联考A类",
    number: int = 1,
    stem: str = "题干",
    images: list[str] | None = None,
    status: str = "unmatched",
    matches: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "sourcePaperFile": f"papers/{paper_id}.json",
        "sourcePaperId": paper_id,
        "sourcePaperName": "2025年全国事业单位联考",
        "category": category,
        "sourceNumber": number,
        "sourceQuestionType": "单选题",
        "sourceStem": stem,
        "sourceOptions": [
            {"label": "A", "content": "甲"},
            {"label": "B", "content": "乙"},
        ],
        "sourceAnswer": "A",
        "sourceExplanation": "因为甲正确。",
        "sourceKnowledgePoints": "常识",
        "sourceImages": images or [],
        "sourceMaterial": None,
        "matchStatus": status,
        "matches": matches or [],
    }


def test_native_sections_keep_a_b_exam_semantics_but_map_to_runtime_modules() -> None:
    assert native_section("联考A类", 46) == "数量关系"
    assert native_section("联考A类", 86) == "资料分析"
    assert native_section("联考B类", 41) == "数学运算"
    assert native_section("联考B类", 46) == "资料分析"
    assert native_section("联考B类", 91) == "综合分析"
    assert canonical_module("综合分析") == "言语理解与表达"


def test_cross_paper_duplicate_creates_one_question_with_two_occurrences() -> None:
    first = _record(paper_id="paper-a")
    second = _record(paper_id="paper-b")

    plan = build_plan([first, second], {})

    assert len(plan.new_groups) == 1
    assert len(set(plan.target_by_source.values())) == 1
    assert plan.summary()["crossPaperDeduplicatedOccurrences"] == 1


def test_trusted_match_reuses_existing_question_for_same_source_group() -> None:
    trusted = _record(
        paper_id="paper-a",
        status="exact_disambiguated",
        matches=[{"questionId": "pp_existing"}],
    )
    duplicate = _record(paper_id="paper-b")

    plan = build_plan([trusted, duplicate], {})

    assert not plan.new_groups
    assert set(plan.target_by_source.values()) == {"pp_existing"}


def test_missing_material_and_missing_visual_are_quarantined_reasons() -> None:
    data_question = _record(
        paper_id="paper-a",
        category="联考A类",
        number=86,
        stem="根据上述资料可以推出的是（    ）。",
    )
    graphic_question = _record(
        paper_id="paper-b",
        category="联考B类",
        number=56,
        stem="从所给的四个选项中选择最合适的图形。",
    )
    graphic_question["sourceKnowledgePoints"] = "图形推理"

    assert "missing_shared_material" in completeness_issues(data_question)
    assert "missing_required_image" in completeness_issues(graphic_question)


def test_material_images_become_assets_and_public_material_urls() -> None:
    record = _record(paper_id="paper-a", category="联考A类", number=86)
    record["sourceMaterial"] = (
        '<p>给定材料</p><img src="images/_remote/material.png" />'
    )
    payloads = {"images/_remote/material.png": b"image-bytes"}

    material_text, material_html = _render_material(record, payloads)
    assets = _question_assets("pp_test", record, payloads)

    assert material_text == "给定材料"
    assert material_html is not None
    assert "https://img.xingguoxian.cn/data/question-bank-export/files/" in material_html
    assert len(assets) == 1
    assert assets[0]["part_kind"] == "material_html"


def test_material_image_enrichment_does_not_merge_old_visual_quarantine_ids() -> None:
    first = _record(
        paper_id="paper-a",
        category="联考A类",
        number=86,
        stem="根据下图回答问题。",
    )
    second = _record(
        paper_id="paper-b",
        category="联考A类",
        number=86,
        stem="根据下图回答问题。",
    )
    for record in (first, second):
        record["sourceMaterial"] = '<img src="images/material.png" />'
    payloads = {"images/material.png": b"image-bytes"}

    plan = build_plan([first, second], payloads)

    assert "missing_required_image" not in completeness_issues(first)
    assert len(plan.new_groups) == 2
