from __future__ import annotations

import pytest

from app.domains.essay_intelligence.catalog import (
    EssayCatalogValidationError,
    SourceMaterialChunk,
    SourceQuestion,
    compile_catalog_entry,
)


def test_compiler_maps_national_essay_and_pins_complete_materials() -> None:
    entry = compile_catalog_entry(
        SourceQuestion(
            question_id="essay-national-1",
            question_type="归纳概括",
            display_level1="申论",
            display_level2=None,
            display_level3=None,
            stem="请概括给定资料反映的问题，不超过200字。（20分）",
            task_paper_title="2025年国家公务员录用考试《申论》",
        ),
        [
            _chunk("chunk-1", 1, 1, "材料1（1/2）", "第一段上半部分。", 1, 2),
            _chunk("chunk-2", 2, 1, "材料1（2/2）", "第一段下半部分。", 2, 2),
            _chunk("chunk-3", 3, 2, "材料2", "第二段完整材料。", 1, 1),
        ],
    )

    assert entry.question_type == "summary"
    assert entry.word_limit == 200
    assert str(entry.maximum_score) == "20"
    assert entry.paper_title == "2025年国家公务员录用考试《申论》"
    assert [item.segment_id for item in entry.material_segments] == [
        "knowledge-chunk:chunk-1",
        "knowledge-chunk:chunk-2",
        "knowledge-chunk:chunk-3",
    ]
    assert [item.segment_order for item in entry.material_segments] == [1, 2, 1]
    assert len(entry.prompt_sha256) == 64
    assert len(entry.material_content_sha256) == 64
    assert len(entry.aggregate_sha256) == 64


def test_compiler_rejects_incomplete_material_parts_without_fallback() -> None:
    with pytest.raises(EssayCatalogValidationError) as error:
        compile_catalog_entry(
            SourceQuestion(
                question_id="essay-incomplete",
                question_type="summary",
                display_level1="申论",
                display_level2=None,
                display_level3=None,
                stem="请概括问题，不超过150字。（15分）",
                task_paper_title="2025年国家公务员录用考试《申论》",
            ),
            [_chunk("chunk-1", 1, 1, "材料1（1/2）", "只有第一片。", 1, 2)],
        )

    assert "material_parts_incomplete" in {item.code for item in error.value.issues}


def test_compiler_rejects_ambiguous_word_limit_and_score() -> None:
    with pytest.raises(EssayCatalogValidationError) as error:
        compile_catalog_entry(
            SourceQuestion(
                question_id="essay-ambiguous",
                question_type="composition",
                display_level1="申论",
                display_level2=None,
                display_level3=None,
                stem="先写200字提纲，再写1000字文章。（20分）（30分）",
                task_paper_title="2025年国家公务员录用考试《申论》",
            ),
            [_chunk("chunk-1", 1, 1, "材料1", "完整材料。", 1, 1)],
        )

    codes = {item.code for item in error.value.issues}
    assert "word_limit_missing" in codes
    assert "maximum_score_missing" in codes


@pytest.mark.parametrize(
    ("source_type", "prompt", "expected"),
    [
        ("proposal", "请提出对策，不超过300字。（20分）", "countermeasure"),
        ("essay", "请写一篇文章，1000字左右。（40分）", "composition"),
        ("mixed", "请为调研组拟写一份工作简报，500字以内。（30分）", "implementation"),
    ],
)
def test_compiler_maps_real_source_question_types(
    source_type: str,
    prompt: str,
    expected: str,
) -> None:
    entry = compile_catalog_entry(
        SourceQuestion(
            question_id=f"essay-{source_type}",
            question_type=source_type,
            display_level1="申论",
            display_level2=None,
            display_level3=None,
            stem=prompt,
            task_paper_title="2025年国家公务员录用考试《申论》",
        ),
        [_chunk("chunk-1", 1, 1, "材料1", "完整材料。", 1, 1)],
    )

    assert entry.question_type == expected


def test_compiler_requires_exactly_one_task_chunk() -> None:
    with pytest.raises(EssayCatalogValidationError) as error:
        compile_catalog_entry(
            SourceQuestion(
                question_id="essay-no-task",
                question_type="summary",
                display_level1="申论",
                display_level2=None,
                display_level3=None,
                stem="",
                task_chunk_count=0,
                task_paper_title="2025年国家公务员录用考试《申论》",
            ),
            [_chunk("chunk-1", 1, 1, "材料1", "完整材料。", 1, 1)],
        )

    assert "task_chunk_count_invalid" in {item.code for item in error.value.issues}


def _chunk(
    chunk_id: str,
    chunk_index: int,
    material_order: int,
    title: str,
    content: str,
    part_index: int,
    part_count: int,
) -> SourceMaterialChunk:
    return SourceMaterialChunk(
        knowledge_chunk_id=chunk_id,
        chunk_index=chunk_index,
        title=title,
        content=content,
        metadata={
            "paperTitle": "2025年国家公务员录用考试《申论》",
            "materialPartIndex": part_index,
            "materialPartCount": part_count,
        },
        material_order=material_order,
    )
