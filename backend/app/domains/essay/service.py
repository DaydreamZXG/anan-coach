from __future__ import annotations

import re

from app.domains.essay.repository import EssayRepository
from app.domains.essay.schemas import EssayMaterial, EssayTask
from app.domains.taskstream.service import TODAY_ESSAY_TASK_ID

ESSAY_DIMENSIONS = ["要点完整度", "概括抽象度", "结构层次", "语言压缩", "格式规范"]


class EssayTaskNotFoundError(LookupError):
    pass


class EssayService:
    """Deprecated v1 essay task reader.

    Essay writes belong exclusively to the essay_intelligence v2 domain. Keeping
    this service read-only prevents internal callers from bypassing the retired
    HTTP writers.
    """

    def __init__(self, *, essay_repository: EssayRepository) -> None:
        self._essay_repository = essay_repository

    def get_task(self, task_id: str) -> EssayTask:
        if task_id != TODAY_ESSAY_TASK_ID:
            raise EssayTaskNotFoundError(task_id)
        question = self._essay_repository.find_training_question()
        if question is None:
            raise EssayTaskNotFoundError(task_id)
        materials, incomplete = _materials_from_chunks(
            self._essay_repository.list_material_chunks(
                question_id=str(question["question_id"])
            )
        )
        material_text = "\n\n".join(
            f"{material.title}\n{material.text}" for material in materials
        )
        return EssayTask(
            task_id=task_id,
            question_id=str(question["question_id"]),
            title=_task_title(question),
            material=material_text,
            materials=materials,
            material_incomplete=incomplete,
            prompt=str(question["stem"]),
            word_limit=_word_limit(str(question["stem"])),
            dimensions=ESSAY_DIMENSIONS,
        )


def _materials_from_chunks(chunks: list[dict[str, object]]) -> tuple[list[EssayMaterial], bool]:
    grouped: dict[str, dict[str, object]] = {}
    order: list[str] = []
    incomplete = not chunks
    for chunk in chunks:
        raw_title = str(chunk.get("title") or f"材料{len(order) + 1}")
        title = _base_material_title(raw_title)
        if title not in grouped:
            grouped[title] = {
                "parts": [],
                "expected_parts": 0,
                "source": _metadata_source(chunk.get("metadata")),
            }
            order.append(title)
        grouped[title]["parts"].append(str(chunk.get("content") or ""))
        expected_parts = _expected_part_count(chunk.get("metadata"))
        if expected_parts:
            grouped[title]["expected_parts"] = max(
                int(grouped[title]["expected_parts"]),
                expected_parts,
            )

    materials: list[EssayMaterial] = []
    for index, title in enumerate(order, start=1):
        item = grouped[title]
        parts = [part for part in item["parts"] if part.strip()]
        expected_parts = int(item["expected_parts"])
        if expected_parts and len(parts) < expected_parts:
            incomplete = True
        if not parts:
            incomplete = True
        materials.append(
            EssayMaterial(
                index=index,
                title=title,
                text="\n".join(parts),
                source=item["source"] if isinstance(item["source"], str) else None,
            )
        )
    return materials, incomplete


def _base_material_title(title: str) -> str:
    return re.sub(r"（\d+/\d+）$", "", title).strip() or title


def _expected_part_count(metadata: object) -> int:
    if isinstance(metadata, dict):
        value = metadata.get("materialPartCount")
        if isinstance(value, int):
            return value
    return 0


def _metadata_source(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    paper = metadata.get("paperTitle")
    if isinstance(paper, str) and paper:
        return paper
    source_path = metadata.get("sourcePath")
    return source_path if isinstance(source_path, str) and source_path else None


def _task_title(question: dict[str, object]) -> str:
    question_type = str(question.get("question_type") or "essay")
    if question_type == "summary":
        return "申论概括题重写"
    if question_type == "countermeasure":
        return "申论对策题重写"
    if question_type == "comprehensive_analysis":
        return "申论综合分析题重写"
    return "申论小题重写"


def _word_limit(stem: str) -> int:
    matches = re.findall(r"不超过\s*(\d+)\s*字|(\d+)\s*字以内", stem)
    for first, second in matches:
        value = first or second
        if value:
            return int(value)
    return 300
