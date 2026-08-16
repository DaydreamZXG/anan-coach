#!/usr/bin/env python3
"""Import A/B 类事业单位联考《综合应用能力》为私有隔离源数据。

这批 Markdown 只有卷面、材料与作答要求，没有答案和解析，且来源清单
明确要求 ``user_private``。因此脚本只写入 ``product.questions``、
``product.knowledge_chunks``、来源 occurrence、标签与私有图片对象；题目固定
为 ``quarantined``，不创建 content head、公共 essay task 或 rubric。

图片以 TOS ``private`` ACL 发布，数据库只保存 object key。脚本可重放，所有
身份都从数据集、卷面和题号确定性生成，并拒绝写入非 ``*_staging`` 库。
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterable

import psycopg
from psycopg.types.json import Jsonb


DATASET_ID = "sydw-zongying-ab-private-20260811"
OBJECT_PREFIX = f"private/source-papers/{DATASET_ID}/"
CHINESE_NUMERALS = "一二三四五六七八九十百零〇"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

_ANSWER_SECTION_RE = re.compile(r"^##\s+(?:二、回答问题|三、作答要求|作答要求)\s*$")
_BRACKET_TASK_RE = re.compile(
    rf"^\*\*【试题([{CHINESE_NUMERALS}\d]+)】\*\*$"
)
_DIRECT_TASK_RE = re.compile(rf"^([{CHINESE_NUMERALS}]+)[、．.]\s*(.*)$")
_MATERIAL_HEADING_RE = re.compile(
    rf"^(?:\*\*)?材料\s*([0-9{CHINESE_NUMERALS}]+)(?:\*\*)?\s*$"
)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_SCORE_RE = re.compile(r"[（(]\s*(\d{1,3})\s*分\s*[)）]")
_WORD_RANGE_RE = re.compile(r"(\d{2,4})\s*[—–\-~～至到]\s*(\d{2,4})\s*字")
_WORD_SINGLE_RE = re.compile(
    r"(?:不超过|不少于|字数在|总字数|控制在|限)\s*(\d{2,4})\s*字|"
    r"(?:字数|总字数)\s*(\d{2,4})\s*字"
)
_PAPER_ID_RE = re.compile(r"卷面ID：(\d+)")
_EXAM_RE = re.compile(r"考次：([^`·\n]+?)\s*·")
_CATEGORY_RE = re.compile(r"类别：([AB])\s*类")
_SOURCE_URL_RE = re.compile(r"原文：(https?://\S+)")
_YEAR_RE = re.compile(r"^(20\d{2})")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _clean_markdown(value: str) -> str:
    text = value.replace("**", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_image_ref(value: str) -> str:
    candidate = value.strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.casefold() not in IMAGE_SUFFIXES
    ):
        raise ValueError(f"unsafe or unsupported image reference: {value!r}")
    return path.as_posix()


def _object_key(payload: bytes, reference: str) -> str:
    digest = sha256(payload).hexdigest()
    suffix = PurePosixPath(reference).suffix.casefold()
    return f"{OBJECT_PREFIX}{digest[:2]}/{digest}{suffix}"


@dataclass(frozen=True, slots=True)
class Material:
    order: int
    title: str
    content: str
    image_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Task:
    order: int
    prompt: str
    question_type: str
    display_level2: str
    maximum_scores: tuple[int, ...]
    word_limits: tuple[int, ...]
    image_refs: tuple[str, ...]

    @property
    def issues(self) -> tuple[str, ...]:
        result: list[str] = []
        if not self.prompt:
            result.append("prompt_missing")
        if not self.maximum_scores:
            result.append("maximum_score_missing")
        elif len(self.maximum_scores) > 1:
            result.append("maximum_score_ambiguous")
        if not self.word_limits:
            result.append("word_limit_missing")
        elif len(self.word_limits) > 1:
            result.append("word_limit_ambiguous")
        return tuple(result)


@dataclass(frozen=True, slots=True)
class Paper:
    source_file: str
    title: str
    paper_id: str
    exam: str
    category: str
    source_url: str
    materials: tuple[Material, ...]
    tasks: tuple[Task, ...]


@dataclass(slots=True)
class ImportPlan:
    papers: list[Paper]
    image_payloads: dict[str, bytes]

    @property
    def tasks(self) -> list[tuple[Paper, Task]]:
        return [(paper, task) for paper in self.papers for task in paper.tasks]

    def summary(self) -> dict[str, object]:
        issues = Counter(
            issue for _paper, task in self.tasks for issue in task.issues
        )
        return {
            "datasetId": DATASET_ID,
            "distributionClass": "account_private",
            "publicationState": "quarantined_pending_owner_binding_and_rubric",
            "papers": len(self.papers),
            "tasks": len(self.tasks),
            "materials": sum(len(paper.materials) for paper in self.papers),
            "imageReferences": len(self.image_payloads),
            "uniqueImages": len(
                {sha256(value).hexdigest() for value in self.image_payloads.values()}
            ),
            "taskTypes": dict(
                sorted(Counter(task.display_level2 for _, task in self.tasks).items())
            ),
            "completenessIssues": dict(sorted(issues.items())),
        }


def _task_type(prompt: str) -> tuple[str, str]:
    if re.search(r"校阅改错|改正", prompt):
        return "proofreading", "校阅改错"
    if re.search(r"论证错误|论证评价", prompt):
        return "argument_evaluation", "论证评价"
    if re.search(r"议论文|文章|评论|作文|写一篇|解说词|宣传稿", prompt):
        return "article_writing", "文章写作"
    if re.search(
        r"通知|邮件|汇报|发言稿|讲话稿|简报|一封信|工作报告|"
        r"介绍|提纲|倡议书|讲述稿",
        prompt,
    ):
        return "official_document", "贯彻执行"
    if re.search(r"建议|措施|对策|改进|优化", prompt):
        return "countermeasure", "提出对策"
    if re.search(r"概括|归纳|总结|提炼|指出|填入", prompt):
        return "summary", "归纳概括"
    return "analysis", "综合分析"


def _task_limits(prompt: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    scores = tuple(sorted({int(value) for value in _SCORE_RE.findall(prompt)}))
    words: set[int] = set()
    for lower, upper in _WORD_RANGE_RE.findall(prompt):
        words.add(max(int(lower), int(upper)))
    for pair in _WORD_SINGLE_RE.findall(prompt):
        words.update(int(value) for value in pair if value)
    return scores, tuple(sorted(words))


def _parse_materials(lines: list[str]) -> tuple[Material, ...]:
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _MATERIAL_HEADING_RE.match(line.strip())
        if match:
            starts.append((index, match.group(1)))
    if not starts:
        content = _clean_markdown("\n".join(lines))
        refs = tuple(dict.fromkeys(_safe_image_ref(v) for v in _IMAGE_RE.findall(content)))
        return (Material(1, "给定材料", content, refs),) if content else ()

    materials: list[Material] = []
    for position, (start, _label) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        content = _clean_markdown("\n".join(lines[start + 1 : end]))
        refs = tuple(dict.fromkeys(_safe_image_ref(v) for v in _IMAGE_RE.findall(content)))
        materials.append(Material(position + 1, f"材料{position + 1}", content, refs))
    return tuple(materials)


def _task_starts(lines: list[str]) -> list[int]:
    bracket = [
        index for index, line in enumerate(lines) if _BRACKET_TASK_RE.match(line.strip())
    ]
    if bracket:
        return bracket
    result: list[int] = []
    for index, line in enumerate(lines):
        normalized = _clean_markdown(line)
        if _DIRECT_TASK_RE.match(normalized):
            result.append(index)
    return result


def _parse_tasks(lines: list[str]) -> tuple[Task, ...]:
    starts = _task_starts(lines)
    tasks: list[Task] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        prompt = _clean_markdown("\n".join(lines[start:end]))
        prompt = re.sub(r"^【试题[^】]+】\s*", "", prompt).strip()
        first_line, *remaining_lines = prompt.splitlines()
        direct = _DIRECT_TASK_RE.match(first_line)
        if direct:
            prompt = "\n".join(
                value
                for value in (direct.group(2).strip(), *remaining_lines)
                if value
            ).strip()
        scores, words = _task_limits(prompt)
        question_type, display_level2 = _task_type(prompt)
        refs = tuple(
            dict.fromkeys(_safe_image_ref(value) for value in _IMAGE_RE.findall(prompt))
        )
        tasks.append(
            Task(
                order=position + 1,
                prompt=prompt,
                question_type=question_type,
                display_level2=display_level2,
                maximum_scores=scores,
                word_limits=words,
                image_refs=refs,
            )
        )
    return tuple(tasks)


def parse_paper(path: Path, *, root: Path) -> Paper:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    answer_sections = [
        index for index, line in enumerate(lines) if _ANSWER_SECTION_RE.match(line.strip())
    ]
    if len(answer_sections) != 1:
        raise ValueError(f"{path.name}: expected one answer section, got {len(answer_sections)}")
    answer_index = answer_sections[0]
    material_section_candidates = [
        index
        for index, line in enumerate(lines[:answer_index])
        if line.startswith("##") and "给定材料" in line
    ]
    material_start = material_section_candidates[-1] + 1 if material_section_candidates else 0
    title = lines[0].removeprefix("#").strip()
    paper_id = _required_match(_PAPER_ID_RE, text, path, "paper id")
    exam = _required_match(_EXAM_RE, text, path, "exam").strip()
    category = _required_match(_CATEGORY_RE, text, path, "category")
    source_url = _required_match(_SOURCE_URL_RE, text, path, "source URL")
    paper = Paper(
        source_file=path.relative_to(root).as_posix(),
        title=title,
        paper_id=paper_id,
        exam=exam,
        category=category,
        source_url=source_url,
        materials=_parse_materials(lines[material_start:answer_index]),
        tasks=_parse_tasks(lines[answer_index + 1 :]),
    )
    if not paper.materials or not paper.tasks:
        raise ValueError(f"{path.name}: materials or tasks are missing")
    return paper


def _required_match(
    pattern: re.Pattern[str], text: str, path: Path, label: str
) -> str:
    match = pattern.search(text)
    if not match:
        raise ValueError(f"{path.name}: missing {label}")
    return match.group(1)


def build_plan(source_dir: Path) -> ImportPlan:
    papers = [
        parse_paper(path, root=source_dir)
        for path in sorted(source_dir.glob("*.md"))
        if path.name != "MANIFEST.md"
    ]
    references = {
        reference
        for paper in papers
        for material in paper.materials
        for reference in material.image_refs
    }
    references.update(
        reference
        for paper in papers
        for task in paper.tasks
        for reference in task.image_refs
    )
    image_payloads: dict[str, bytes] = {}
    for reference in sorted(references):
        path = source_dir / reference
        if not path.is_file():
            raise ValueError(f"missing referenced image: {reference}")
        image_payloads[reference] = path.read_bytes()
    return ImportPlan(papers, image_payloads)


def upload_assets(plan: ImportPlan) -> dict[str, int]:
    try:
        import tos
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("the official tos package is required") from exc
    required = {
        key: os.environ.get(key, "")
        for key in (
            "TOS_ACCESS_KEY",
            "TOS_SECRET_KEY",
            "TOS_ENDPOINT",
            "TOS_REGION",
            "TOS_BUCKET",
        )
    }
    if not all(required.values()):
        raise RuntimeError("TOS configuration is incomplete")
    client = tos.TosClientV2(
        required["TOS_ACCESS_KEY"],
        required["TOS_SECRET_KEY"],
        required["TOS_ENDPOINT"],
        required["TOS_REGION"],
    )
    unique: dict[str, tuple[bytes, str]] = {}
    for reference, payload in plan.image_payloads.items():
        unique.setdefault(_object_key(payload, reference), (payload, reference))
    counts = Counter()
    for key, (payload, reference) in sorted(unique.items()):
        try:
            client.head_object(required["TOS_BUCKET"], key)
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status != 404:
                raise
        else:
            counts["alreadyPresent"] += 1
            continue
        client.put_object(
            required["TOS_BUCKET"],
            key,
            content=payload,
            content_length=len(payload),
            content_type=mimetypes.guess_type(reference)[0] or "application/octet-stream",
            acl=tos.ACLType.ACL_Private,
            forbid_overwrite=True,
            meta={"content-sha256": sha256(payload).hexdigest()},
        )
        client.head_object(required["TOS_BUCKET"], key)
        counts["uploaded"] += 1
    counts["total"] = len(unique)
    return dict(counts)


def _question_id(paper: Paper, task: Task) -> str:
    return _stable_id("sydwzyq", DATASET_ID, paper.paper_id, str(task.order))


def _source_question_id(paper: Paper, task: Task) -> str:
    return f"sydw-zongying:{paper.paper_id}:{task.order}"


def _paper_material_text(paper: Paper, plan: ImportPlan) -> str:
    chunks: list[str] = []
    for material in paper.materials:
        content = _render_private_assets(material.content, material.image_refs, plan)
        chunks.append(f"{material.title}\n{content}")
    return "\n\n".join(chunks)


def _render_private_assets(
    content: str, references: tuple[str, ...], plan: ImportPlan
) -> str:
    rendered = content
    for reference in references:
        key = _object_key(plan.image_payloads[reference], reference)
        rendered = rendered.replace(
            f"]({reference})", f"](private-asset://{key})"
        )
    return rendered


def _iter_assets(
    paper: Paper, task: Task, plan: ImportPlan
) -> Iterable[dict[str, object]]:
    question_id = _question_id(paper, task)
    seen: set[str] = set()
    ordinal = 0
    parts = [
        ("task_html", task.order, reference)
        for reference in task.image_refs
    ]
    parts.extend(
        ("material_html", material.order, reference)
        for material in paper.materials
        for reference in material.image_refs
    )
    for part_kind, part_index, reference in parts:
        payload = plan.image_payloads[reference]
        key = _object_key(payload, reference)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "asset_id": _stable_id("sydwzya", question_id, key),
            "question_id": question_id,
            "source_question_id": _source_question_id(paper, task),
            "is_representative_asset": True,
            "part_kind": part_kind,
            "part_index": part_index,
            "image_ordinal": ordinal,
            "image_src": None,
            "object_key": key,
            "source_html": reference,
        }
        ordinal += 1


def _database_name(connection: psycopg.Connection) -> str:
    return str(connection.execute("select current_database()").fetchone()[0])


def apply_plan(plan: ImportPlan, database_url: str) -> dict[str, int]:
    counts = Counter()
    with psycopg.connect(database_url) as connection:
        database = _database_name(connection)
        if not database.endswith("_staging"):
            raise RuntimeError(f"refusing to import into non-staging database {database!r}")
        existing = {
            str(row[0])
            for row in connection.execute(
                "select question_id from product.questions where merge_evidence_source=%s",
                (DATASET_ID,),
            ).fetchall()
        }
        now = datetime.now(UTC)
        with connection.cursor() as cursor:
            for paper, task in plan.tasks:
                question_id = _question_id(paper, task)
                source_question_id = _source_question_id(paper, task)
                material_text = _paper_material_text(paper, plan)
                task_prompt = _render_private_assets(task.prompt, task.image_refs, plan)
                assets = list(_iter_assets(paper, task, plan))
                year_match = _YEAR_RE.match(paper.exam)
                year = int(year_match.group(1)) if year_match else None
                classification = {
                    "examSystem": "事业单位全国分类联考",
                    "examCategory": f"{paper.category}类综合应用能力",
                    "sourcePaperId": paper.paper_id,
                    "sourcePaperTitle": paper.title,
                    "sourceUrl": paper.source_url,
                    "rightsStatus": "user_private",
                    "distributionClass": "account_private",
                    "crossAccountUse": "prohibited",
                    "modelTrainingUse": "prohibited",
                    "publicationState": "quarantined_pending_owner_binding_and_rubric",
                    "maximumScores": list(task.maximum_scores),
                    "wordLimits": list(task.word_limits),
                    "completenessIssues": list(task.issues),
                }
                cursor.execute(
                    """
                    insert into product.questions (
                      question_id, domain, status, merge_policy, merge_evidence_source,
                      representative_source_question_id, module_name, question_type,
                      display_level1, display_level2, display_level3,
                      stem, stem_html, answer, shared_context_required,
                      visual_required, needs_multimodal, material_id,
                      material_text, source_member_count, merged_source_member_count,
                      occurrence_count, occurrence_province_count,
                      occurrence_paper_count, first_year, latest_year,
                      occurrence_provinces, source_question_ids,
                      source_question_keys, representative_classification,
                      representative_asset_refs, imported_at
                    ) values (
                      %s, 'essay', 'quarantined', 'source_exact', %s,
                      %s, '综合应用能力', %s,
                      '综合应用能力', %s, %s,
                      %s, %s, null, true,
                      %s, %s, %s,
                      %s, 1, 1,
                      1, 1,
                      1, %s, %s,
                      '全国联考', %s,
                      %s, %s,
                      %s, %s
                    ) on conflict (question_id) do update set
                      status=excluded.status,
                      module_name=excluded.module_name,
                      question_type=excluded.question_type,
                      display_level1=excluded.display_level1,
                      display_level2=excluded.display_level2,
                      display_level3=excluded.display_level3,
                      stem=excluded.stem,
                      stem_html=excluded.stem_html,
                      material_id=excluded.material_id,
                      material_text=excluded.material_text,
                      visual_required=excluded.visual_required,
                      needs_multimodal=excluded.needs_multimodal,
                      representative_classification=excluded.representative_classification,
                      representative_asset_refs=excluded.representative_asset_refs
                    where product.questions.merge_evidence_source=excluded.merge_evidence_source
                    """,
                    (
                        question_id,
                        DATASET_ID,
                        source_question_id,
                        task.question_type,
                        task.display_level2,
                        f"{paper.category}类",
                        task_prompt,
                        task_prompt,
                        bool(assets),
                        bool(assets),
                        _stable_id("sydwzym", DATASET_ID, paper.paper_id),
                        material_text,
                        year,
                        year,
                        source_question_id,
                        source_question_id,
                        Jsonb(classification),
                        Jsonb(
                            [
                                {"assetId": asset["asset_id"], "objectKey": asset["object_key"]}
                                for asset in assets
                            ]
                        ),
                        now,
                    ),
                )
                counts[
                    "questionsAlreadyPresent" if question_id in existing else "questionsInserted"
                ] += 1
                _upsert_chunks(cursor, paper, task, plan, now)
                _upsert_tags(cursor, question_id, paper, task)
                for asset in assets:
                    cursor.execute(
                        """
                        insert into product.question_assets (
                          asset_id, question_id, source_question_id,
                          is_representative_asset, part_kind, part_index,
                          image_ordinal, image_src, object_key, source_html
                        ) values (
                          %(asset_id)s, %(question_id)s, %(source_question_id)s,
                          %(is_representative_asset)s, %(part_kind)s, %(part_index)s,
                          %(image_ordinal)s, %(image_src)s, %(object_key)s, %(source_html)s
                        ) on conflict (asset_id) do update set
                          object_key=excluded.object_key,
                          source_html=excluded.source_html
                        """,
                        asset,
                    )
                occurrence_id = _stable_id(
                    "sydwzyocc", DATASET_ID, paper.paper_id, str(task.order)
                )
                cursor.execute(
                    """
                    insert into product.question_occurrences (
                      occurrence_id, question_id, source_question_id,
                      source_dataset_id, source_paper_row_id, source_paper_key,
                      source_occurrence_question_key, source_occurrence_paper_key,
                      source_path, cls, province, occurrence_paper_title,
                      paper_title, year, number, section
                    ) values (
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s,
                      %s, %s, '全国联考', %s,
                      %s, %s, %s, '综合应用能力'
                    ) on conflict (occurrence_id) do nothing
                    """,
                    (
                        occurrence_id,
                        question_id,
                        source_question_id,
                        DATASET_ID,
                        paper.paper_id,
                        paper.paper_id,
                        source_question_id,
                        paper.paper_id,
                        paper.source_file,
                        f"联考{paper.category}类综应",
                        paper.title,
                        paper.title,
                        year,
                        task.order,
                    ),
                )
                counts[
                    "occurrencesInserted"
                    if cursor.rowcount
                    else "occurrencesAlreadyPresent"
                ] += 1
        connection.commit()
    return dict(counts)


def _upsert_chunks(
    cursor: psycopg.Cursor,
    paper: Paper,
    task: Task,
    plan: ImportPlan,
    now: datetime,
) -> None:
    question_id = _question_id(paper, task)
    source_question_id = _source_question_id(paper, task)
    year_match = _YEAR_RE.match(paper.exam)
    year = int(year_match.group(1)) if year_match else None
    chunk_rows: list[tuple[str, int, int | None, str, str]] = [
        (
            "task",
            1,
            None,
            "作答要求",
            _render_private_assets(task.prompt, task.image_refs, plan),
        )
    ]
    chunk_rows.extend(
        (
            "material",
            index + 2,
            material.order,
            material.title,
            _render_private_assets(material.content, material.image_refs, plan),
        )
        for index, material in enumerate(paper.materials)
    )
    for chunk_kind, chunk_index, material_order, title, content in chunk_rows:
        chunk_id = _stable_id(
            "sydwzychunk",
            DATASET_ID,
            paper.paper_id,
            str(task.order),
            chunk_kind,
            str(chunk_index),
        )
        metadata = {
            "paperTitle": paper.title,
            "paperId": paper.paper_id,
            "exam": paper.exam,
            "category": f"{paper.category}类",
            "sourceUrl": paper.source_url,
            "rightsStatus": "user_private",
            "distributionClass": "account_private",
            "crossAccountUse": "prohibited",
            "modelTrainingUse": "prohibited",
            "materialPartIndex": 1 if chunk_kind == "material" else None,
            "materialPartCount": 1 if chunk_kind == "material" else None,
            "taskOrder": task.order,
            "maximumScores": list(task.maximum_scores),
            "wordLimits": list(task.word_limits),
        }
        cursor.execute(
            """
            insert into product.knowledge_chunks (
              knowledge_chunk_id, question_id, source_question_id,
              source_dataset_id, source_paper_row_id, source_question_key,
              source_paper_key, chunk_kind, chunk_index, material_order,
              title, content, content_sha256, token_estimate,
              display_path, question_type, province, year, tags, metadata
            ) values (
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, '全国联考', %s, %s, %s
            ) on conflict (knowledge_chunk_id) do update set
              title=excluded.title,
              content=excluded.content,
              content_sha256=excluded.content_sha256,
              token_estimate=excluded.token_estimate,
              metadata=excluded.metadata
            """,
            (
                chunk_id,
                question_id,
                source_question_id,
                DATASET_ID,
                paper.paper_id,
                source_question_id,
                paper.paper_id,
                chunk_kind,
                chunk_index,
                material_order,
                title,
                content,
                sha256(content.encode("utf-8")).hexdigest(),
                max(1, len(content) // 2),
                ["综合应用能力", f"{paper.category}类", task.display_level2],
                task.question_type,
                year,
                ["user_private", "事业单位联考", f"{paper.category}类"],
                Jsonb(metadata),
            ),
        )


def _upsert_tags(
    cursor: psycopg.Cursor, question_id: str, paper: Paper, task: Task
) -> None:
    tags = (
        ("exam_system", "事业单位全国分类联考"),
        ("exam_category", f"{paper.category}类综合应用能力"),
        ("module", "综合应用能力"),
        ("question_type", task.display_level2),
        ("rights_status", "user_private"),
        ("distribution_class", "account_private"),
        ("publication_state", "quarantined_pending_owner_binding_and_rubric"),
    )
    for sort_order, (tag_type, tag_value) in enumerate(tags):
        tag_id = _stable_id("sydwzytag", question_id, tag_type, tag_value)
        cursor.execute(
            """
            insert into product.question_tags (
              question_tag_id, question_id, source_tag_id, tag_type,
              tag_value, sort_order, evidence_link_count,
              source_question_count, evidence_sources
            ) values (%s, %s, null, %s, %s, %s, 1, 1, %s)
            on conflict (question_tag_id) do update set
              tag_value=excluded.tag_value,
              sort_order=excluded.sort_order
            """,
            (tag_id, question_id, tag_type, tag_value, sort_order, DATASET_ID),
        )


def verify_import(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        database = _database_name(connection)
        if not database.endswith("_staging"):
            raise RuntimeError(f"refusing to verify non-staging database {database!r}")
        row = connection.execute(
            """
            select
              count(*) as questions,
              count(*) filter (where status='quarantined') as quarantined,
              count(*) filter (where status<>'quarantined') as unexpectedly_visible
            from product.questions
            where merge_evidence_source=%s
            """,
            (DATASET_ID,),
        ).fetchone()
        chunks = connection.execute(
            "select count(*) from product.knowledge_chunks where source_dataset_id=%s",
            (DATASET_ID,),
        ).fetchone()[0]
        occurrences = connection.execute(
            "select count(*) from product.question_occurrences where source_dataset_id=%s",
            (DATASET_ID,),
        ).fetchone()[0]
        assets = connection.execute(
            """
            select count(*) from product.question_assets a
            join product.questions q using(question_id)
            where q.merge_evidence_source=%s
            """,
            (DATASET_ID,),
        ).fetchone()[0]
        heads = connection.execute(
            """
            select count(*) from product.question_content_heads h
            join product.questions q using(question_id)
            where q.merge_evidence_source=%s
            """,
            (DATASET_ID,),
        ).fetchone()[0]
        public_urls = connection.execute(
            """
            select count(*) from product.question_assets a
            join product.questions q using(question_id)
            where q.merge_evidence_source=%s and nullif(btrim(a.image_src),'') is not null
            """,
            (DATASET_ID,),
        ).fetchone()[0]
        task_versions = connection.execute(
            """
            select count(*) from product.essay_task_versions version
            join product.questions question using(question_id)
            where question.merge_evidence_source=%s
            """,
            (DATASET_ID,),
        ).fetchone()[0]
        rubric_versions = connection.execute(
            """
            select count(*) from product.essay_rubric_versions rubric
            join product.essay_task_versions version
              on version.task_version_id=rubric.task_version_id
            join product.questions question using(question_id)
            where question.merge_evidence_source=%s
            """,
            (DATASET_ID,),
        ).fetchone()[0]
        answers = connection.execute(
            """
            select count(*) from product.questions
            where merge_evidence_source=%s
              and nullif(btrim(answer), '') is not null
            """,
            (DATASET_ID,),
        ).fetchone()[0]
    return {
        "questions": int(row[0]),
        "quarantinedQuestions": int(row[1]),
        "unexpectedlyVisibleQuestions": int(row[2]),
        "knowledgeChunks": int(chunks),
        "occurrences": int(occurrences),
        "assetBindings": int(assets),
        "contentHeads": int(heads),
        "publicAssetUrls": int(public_urls),
        "essayTaskVersions": int(task_versions),
        "rubricVersions": int(rubric_versions),
        "answersPresent": int(answers),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "upload-assets", "apply", "verify"))
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument(
        "--database-url",
        default="postgresql:///gongkao_dazi_staging?host=/var/run/postgresql",
    )
    parser.add_argument("--report", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.mode == "verify":
        result: dict[str, object] = verify_import(args.database_url)
    else:
        if args.source_dir is None:
            raise SystemExit("--source-dir is required")
        plan = build_plan(args.source_dir)
        result = plan.summary()
        if args.mode == "upload-assets":
            result["assetPublication"] = upload_assets(plan)
        elif args.mode == "apply":
            result["databaseApply"] = apply_plan(plan, args.database_url)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
