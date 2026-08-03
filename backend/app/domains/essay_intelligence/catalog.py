from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any

from app.contracts.guokao_shenlun import (
    GuokaoShenlunQuestionType as QuestionType,
    guokao_shenlun_category,
    guokao_shenlun_category_from_catalog_text,
)

NATIONAL_PAPER_MARKERS = ("国考", "国家公务员")
ESSAY_PAPER_MARKER = "申论"


class EssayCatalogValidationError(ValueError):
    def __init__(self, issues: list["CatalogValidationIssue"]) -> None:
        self.issues = tuple(issues)
        details = "; ".join(f"{item.question_id}: {item.code} ({item.message})" for item in issues)
        super().__init__(f"Guokao essay catalog validation failed: {details}")


@dataclass(frozen=True)
class CatalogValidationIssue:
    question_id: str
    code: str
    message: str


@dataclass(frozen=True)
class SourceQuestion:
    question_id: str
    question_type: str | None
    display_level1: str | None
    display_level2: str | None
    display_level3: str | None
    stem: str
    task_chunk_count: int = 1
    task_paper_title: str | None = None


@dataclass(frozen=True)
class SourceMaterialChunk:
    knowledge_chunk_id: str
    chunk_index: int | None
    title: str | None
    content: str
    metadata: dict[str, Any]
    material_order: int | None = None
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class CatalogMaterialSegment:
    material_id: str
    segment_id: str
    material_order: int
    segment_order: int
    text: str
    text_sha256: str
    source_metadata: dict[str, Any]


@dataclass(frozen=True)
class CompiledEssayCatalogEntry:
    task_key: str
    question_id: str
    question_type: QuestionType
    prompt: str
    prompt_sha256: str
    word_limit: int
    maximum_score: Decimal
    paper_title: str
    material_segments: tuple[CatalogMaterialSegment, ...]
    material_content_sha256: str
    aggregate_sha256: str


def is_national_paper_title(value: object) -> bool:
    return isinstance(value, str) and any(marker in value for marker in NATIONAL_PAPER_MARKERS)


def is_essay_paper_title(value: object) -> bool:
    return isinstance(value, str) and ESSAY_PAPER_MARKER in value and bool(value.strip())


def compile_catalog_entry(
    question: SourceQuestion,
    chunks: list[SourceMaterialChunk],
) -> CompiledEssayCatalogEntry:
    issues: list[CatalogValidationIssue] = []
    prompt = _normalize_text(question.stem)
    if question.task_chunk_count != 1:
        issues.append(
            _issue(
                question,
                "task_chunk_count_invalid",
                f"必须且只能有一个 task knowledge chunk，实际为 {question.task_chunk_count}",
            )
        )
    elif not prompt:
        issues.append(_issue(question, "prompt_missing", "题干为空"))

    question_type = _map_question_type(question)
    if question_type is None:
        issues.append(
            _issue(
                question,
                "question_type_unsupported",
                f"无法映射题型: {question.question_type!r}",
            )
        )

    word_limit = _parse_word_limit(prompt)
    if word_limit is None:
        issues.append(_issue(question, "word_limit_missing", "题干没有唯一明确的字数要求"))

    maximum_score = _parse_maximum_score(prompt, chunks)
    if maximum_score is None:
        issues.append(
            _issue(question, "maximum_score_missing", "题干或材料元数据没有唯一明确的总分")
        )

    try:
        paper_title, segments = _compile_material_segments(question, chunks)
    except EssayCatalogValidationError as exc:
        issues.extend(exc.issues)
        paper_title = ""
        segments = ()
    if not is_essay_paper_title(question.task_paper_title):
        issues.append(
            _issue(question, "task_paper_scope_invalid", "task chunk 缺少唯一申论 paperTitle")
        )
    elif paper_title and str(question.task_paper_title).strip() != paper_title:
        issues.append(
            _issue(question, "task_material_paper_mismatch", "task 与材料不属于同一张申论试卷")
        )

    if issues:
        raise EssayCatalogValidationError(issues)
    assert question_type is not None
    assert word_limit is not None
    assert maximum_score is not None

    material_payload = {
        "paperTitle": paper_title,
        "questionId": question.question_id,
        "segments": [
            {
                "materialId": item.material_id,
                "segmentId": item.segment_id,
                "materialOrder": item.material_order,
                "segmentOrder": item.segment_order,
                "text": item.text,
                "textSha256": item.text_sha256,
                "sourceMetadata": item.source_metadata,
            }
            for item in segments
        ],
    }
    material_hash = _canonical_hash(material_payload)
    task_payload = {
        "questionId": question.question_id,
        "questionType": question_type,
        "prompt": prompt,
        "wordLimit": word_limit,
        "maximumScore": _decimal_json(maximum_score),
        "materialContentSha256": material_hash,
    }
    return CompiledEssayCatalogEntry(
        task_key=_task_key(question.question_id),
        question_id=question.question_id,
        question_type=question_type,
        prompt=prompt,
        prompt_sha256=_text_hash(prompt),
        word_limit=word_limit,
        maximum_score=maximum_score,
        paper_title=paper_title,
        material_segments=segments,
        material_content_sha256=material_hash,
        aggregate_sha256=_canonical_hash(task_payload),
    )


def _compile_material_segments(
    question: SourceQuestion,
    chunks: list[SourceMaterialChunk],
) -> tuple[str, tuple[CatalogMaterialSegment, ...]]:
    issues: list[CatalogValidationIssue] = []
    if not chunks:
        raise EssayCatalogValidationError(
            [_issue(question, "materials_missing", "没有 material knowledge chunk")]
        )

    paper_titles: set[str] = set()
    ordered_chunks = sorted(
        chunks,
        key=lambda item: (
            item.material_order is None,
            item.material_order if item.material_order is not None else 0,
            item.chunk_index is None,
            item.chunk_index if item.chunk_index is not None else 0,
            item.knowledge_chunk_id,
        ),
    )
    seen_chunk_indexes: set[int] = set()
    groups: dict[str, list[tuple[SourceMaterialChunk, int, int]]] = {}
    group_order: list[str] = []
    for chunk in ordered_chunks:
        paper_title = chunk.metadata.get("paperTitle")
        if not is_essay_paper_title(paper_title):
            issues.append(
                _issue(
                    question,
                    "paper_scope_invalid",
                    f"材料 {chunk.knowledge_chunk_id} 缺少有效申论 paperTitle",
                )
            )
        else:
            paper_titles.add(str(paper_title).strip())

        if chunk.chunk_index is not None:
            if chunk.chunk_index < 1 or chunk.chunk_index in seen_chunk_indexes:
                issues.append(
                    _issue(
                        question,
                        "chunk_order_invalid",
                        f"材料 chunk_index 非正数或重复: {chunk.chunk_index}",
                    )
                )
            seen_chunk_indexes.add(chunk.chunk_index)

        text = _normalize_text(chunk.content)
        if not text:
            issues.append(
                _issue(question, "material_text_missing", f"材料 {chunk.knowledge_chunk_id} 为空")
            )
        title = _normalize_text(chunk.title or "")
        if not title:
            issues.append(
                _issue(
                    question, "material_title_missing", f"材料 {chunk.knowledge_chunk_id} 无标题"
                )
            )
            continue
        base_title, title_index, title_count = _split_material_title(title)
        if chunk.material_order is None or chunk.material_order < 1:
            issues.append(
                _issue(
                    question,
                    "material_order_invalid",
                    f"材料 {chunk.knowledge_chunk_id} 缺少有效 material_order",
                )
            )
        raw_metadata_index = chunk.metadata.get("materialPartIndex")
        raw_metadata_count = chunk.metadata.get("materialPartCount")
        metadata_index = _positive_int(raw_metadata_index)
        metadata_count = _positive_int(raw_metadata_count)
        if "materialPartIndex" in chunk.metadata and metadata_index is None:
            issues.append(
                _issue(
                    question,
                    "material_part_index_invalid",
                    f"材料 {chunk.knowledge_chunk_id} 的 partIndex 无效",
                )
            )
        if "materialPartCount" in chunk.metadata and metadata_count is None:
            issues.append(
                _issue(
                    question,
                    "material_part_count_invalid",
                    f"材料 {chunk.knowledge_chunk_id} 的 partCount 无效",
                )
            )
        if metadata_index is None or metadata_count is None:
            issues.append(
                _issue(
                    question,
                    "material_part_metadata_incomplete",
                    f"材料 {chunk.knowledge_chunk_id} 必须显式提供 partIndex/partCount",
                )
            )
        if metadata_index is not None and title_index is not None and metadata_index != title_index:
            issues.append(
                _issue(
                    question,
                    "material_part_index_conflict",
                    f"材料 {chunk.knowledge_chunk_id} 分片序号冲突",
                )
            )
        if metadata_count is not None and title_count is not None and metadata_count != title_count:
            issues.append(
                _issue(
                    question,
                    "material_part_count_conflict",
                    f"材料 {chunk.knowledge_chunk_id} 分片总数冲突",
                )
            )
        part_index = metadata_index or 0
        part_count = metadata_count or 0
        if part_index > part_count:
            issues.append(
                _issue(
                    question,
                    "material_part_range_invalid",
                    f"材料 {chunk.knowledge_chunk_id} 分片序号越界",
                )
            )
        if base_title not in groups:
            groups[base_title] = []
            group_order.append(base_title)
        groups[base_title].append((chunk, part_index, part_count))

    if len(paper_titles) != 1:
        issues.append(
            _issue(
                question,
                "paper_title_ambiguous",
                f"一道题必须且只能绑定一张申论试卷，实际为 {sorted(paper_titles)}",
            )
        )

    segments: list[CatalogMaterialSegment] = []
    explicit_material_orders: set[int] = set()
    for title in group_order:
        parts = groups[title]
        source_orders = {chunk.material_order for chunk, _, _ in parts}
        if len(source_orders) != 1 or None in source_orders:
            issues.append(
                _issue(question, "material_order_conflict", f"{title} 的 material_order 不一致")
            )
            continue
        material_order = int(next(iter(source_orders)))
        if material_order in explicit_material_orders:
            issues.append(
                _issue(
                    question, "material_order_duplicate", f"material_order {material_order} 重复"
                )
            )
            continue
        explicit_material_orders.add(material_order)
        expected_counts = {part_count for _, _, part_count in parts}
        indexes = [part_index for _, part_index, _ in parts]
        if (
            len(expected_counts) != 1
            or len(indexes) != len(set(indexes))
            or set(indexes) != set(range(1, max(expected_counts) + 1))
        ):
            issues.append(
                _issue(
                    question,
                    "material_parts_incomplete",
                    f"{title} 的分片不完整: indexes={sorted(indexes)}, counts={sorted(expected_counts)}",
                )
            )
            continue
        for chunk, part_index, _ in sorted(parts, key=lambda item: item[1]):
            text = _normalize_text(chunk.content)
            source_metadata = {
                "knowledgeChunkId": chunk.knowledge_chunk_id,
                "paperTitle": str(chunk.metadata.get("paperTitle") or "").strip(),
                "sourceQuestionId": question.question_id,
                "chunkIndex": chunk.chunk_index,
                "materialOrder": chunk.material_order,
                "title": chunk.title,
                "provenance": chunk.provenance or {},
                "sourceMetadata": chunk.metadata,
            }
            segments.append(
                CatalogMaterialSegment(
                    material_id=f"material-{material_order}",
                    segment_id=f"knowledge-chunk:{chunk.knowledge_chunk_id}",
                    material_order=material_order,
                    segment_order=part_index,
                    text=text,
                    text_sha256=_text_hash(text),
                    source_metadata=source_metadata,
                )
            )

    if explicit_material_orders and explicit_material_orders != set(
        range(1, max(explicit_material_orders) + 1)
    ):
        issues.append(
            _issue(
                question,
                "material_order_sequence_invalid",
                f"material_order 必须从1连续，实际为 {sorted(explicit_material_orders)}",
            )
        )
    if issues:
        raise EssayCatalogValidationError(issues)
    return next(iter(paper_titles)), tuple(segments)


def _map_question_type(question: SourceQuestion) -> QuestionType | None:
    raw_values = [
        question.question_type,
        question.display_level3,
        question.display_level2,
        question.display_level1,
    ]
    normalized = " ".join(str(item).strip().lower() for item in raw_values if item)
    direct = guokao_shenlun_category(str(question.question_type or ""))
    if direct is not None:
        return direct.code
    metadata_category = guokao_shenlun_category_from_catalog_text(normalized)
    if metadata_category is not None:
        return metadata_category.code
    return _infer_question_type_from_prompt(question.stem)


def _parse_word_limit(prompt: str) -> int | None:
    values: set[int] = set()
    for lower, upper in re.findall(r"(\d{2,4})\s*[—–\-~～至到]\s*(\d{2,4})\s*字", prompt):
        values.add(max(int(lower), int(upper)))
    patterns = (
        r"(?:不超过|控制在|限|不得超过)\s*(\d{2,4})\s*字",
        r"(\d{2,4})\s*字(?:以内|之内|左右)",
    )
    for pattern in patterns:
        values.update(int(item) for item in re.findall(pattern, prompt))
    values = {item for item in values if 1 <= item <= 5000}
    return next(iter(values)) if len(values) == 1 else None


def _infer_question_type_from_prompt(prompt: str) -> QuestionType | None:
    normalized = _normalize_text(prompt)
    marker_groups: tuple[tuple[QuestionType, tuple[str, ...]], ...] = (
        ("composition", ("写一篇文章", "撰写一篇文章", "自拟题目", "议论文")),
        (
            "implementation",
            (
                "拟写一份",
                "撰写一份",
                "讲话稿",
                "发言稿",
                "倡议书",
                "公开信",
                "宣传稿",
                "工作方案",
                "工作简报",
                "编者按",
            ),
        ),
        ("countermeasure", ("提出对策", "提出建议", "提出措施", "解决措施")),
        ("comprehensive_analysis", ("谈谈理解", "进行分析", "作出解释", "分析说明")),
        ("summary", ("概括", "归纳", "梳理")),
    )
    for question_type, markers in marker_groups:
        if any(marker in normalized for marker in markers):
            return question_type
    return None


def _parse_maximum_score(
    prompt: str,
    chunks: list[SourceMaterialChunk],
) -> Decimal | None:
    values: set[Decimal] = set()
    patterns = (
        r"[（(]\s*(\d{1,3}(?:\.\d{1,2})?)\s*分\s*[）)]",
        r"(?:本题|满分|共计|共)\s*(?:满分|为)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*分",
    )
    for pattern in patterns:
        values.update(Decimal(item) for item in re.findall(pattern, prompt))
    for chunk in chunks:
        for key in ("maximumScore", "questionScore", "score"):
            value = chunk.metadata.get(key)
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                try:
                    parsed = Decimal(str(value))
                except ArithmeticError:
                    continue
                values.add(parsed)
    values = {item for item in values if Decimal("0") < item <= Decimal("100")}
    return next(iter(values)) if len(values) == 1 else None


def _task_key(question_id: str) -> str:
    candidate = f"guokao-essay:{question_id}"
    if len(candidate) <= 160:
        return candidate
    return f"guokao-essay:sha256:{_text_hash(question_id)}"


def _split_material_title(title: str) -> tuple[str, int | None, int | None]:
    match = re.search(r"[（(]\s*(\d+)\s*/\s*(\d+)\s*[）)]\s*$", title)
    if match is None:
        return title, None, None
    base = title[: match.start()].strip()
    return base or title, int(match.group(1)), int(match.group(2))


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _issue(question: SourceQuestion, code: str, message: str) -> CatalogValidationIssue:
    return CatalogValidationIssue(question.question_id, code, message)


def _normalize_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decimal_json(value: Decimal) -> int | float:
    normalized = value.normalize()
    return int(normalized) if normalized == normalized.to_integral() else float(normalized)
