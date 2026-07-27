from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domains.knowledge.repository import (
    KnowledgeRepository,
    KnowledgeMapNodeRecord,
    ProductStatsRecord,
)
from app.domains.knowledge.schemas import (
    KnowledgeChunk,
    QuestionAsset,
    QuestionDetail,
    QuestionExplanation,
    QuestionOccurrence,
    QuestionOption,
    QuestionSearchResponse,
    QuestionSummary,
    QuestionTag,
)


class QuestionNotFoundError(LookupError):
    pass


class KnowledgeMapUnavailableError(RuntimeError):
    pass


class ExamPracticeQuestionsUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeightedPracticeSelection:
    release_id: str
    release_version: str
    source_data_version: str
    fresh_until: datetime
    node_id: str
    exam_scope: str
    track: str
    module_name: str
    node_type: str
    tag_type: str
    tag_value: str
    display_path: str
    window_start_year: int
    window_end_year: int
    occurrence_count: int
    weight: float
    items: list[QuestionSummary]

    @classmethod
    def from_node(
        cls,
        *,
        node: KnowledgeMapNodeRecord,
        items: list[QuestionSummary],
    ) -> WeightedPracticeSelection:
        if node.tag_type is None:
            raise KnowledgeMapUnavailableError(
                "current knowledge map node has no governed tag type"
            )
        return cls(
            release_id=node.release_id,
            release_version=node.release_version,
            source_data_version=node.source_data_version,
            fresh_until=node.fresh_until,
            node_id=node.node_id,
            exam_scope=node.exam_scope,
            track=node.track,
            module_name=node.module_name,
            node_type=node.node_type,
            tag_type=node.tag_type,
            tag_value=node.tag_value,
            display_path=node.display_path,
            window_start_year=node.near5_window_start_year,
            window_end_year=node.near5_window_end_year,
            occurrence_count=node.near5_occurrence_count,
            weight=node.near5_weight,
            items=items,
        )

    @property
    def label(self) -> str:
        return f"{self.module_name}/{self.tag_value}"

    @property
    def basis_sentence(self) -> str:
        return (
            f"知识点地图显示近5年国考「{self.label}」出现 {self.occurrence_count} 次，"
            f"按真实频次优先安排。"
        )


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self._repository = repository

    def get_product_stats(self) -> ProductStatsRecord:
        return self._repository.get_product_stats()

    def get_question(self, question_id: str) -> QuestionDetail:
        question = self._repository.get_question_row(question_id)
        if question is None:
            raise QuestionNotFoundError(question_id)

        explanation_row = self._repository.get_explanation(question_id)
        explanation = None
        if explanation_row is not None:
            explanation = QuestionExplanation(
                preferred_analysis=explanation_row["preferred_analysis"],
                original_analysis=explanation_row["original_analysis"],
                stored_analysis=explanation_row["stored_analysis"],
                analysis_source=explanation_row["analysis_source"],
                analysis_status=explanation_row["analysis_status"],
                prefer_ai_analysis=explanation_row["prefer_ai_analysis"],
                needs_review=explanation_row["analysis_needs_review"],
            )

        return QuestionDetail(
            **question,
            options=[
                QuestionOption(
                    option_index=row["option_index"],
                    label=row["label"],
                    option_text=row["option_text"],
                    option_html=row["option_html"],
                )
                for row in self._repository.get_options(question_id)
            ],
            explanation=explanation,
            occurrences=[
                QuestionOccurrence(**row)
                for row in self._repository.get_occurrences(question_id)
            ],
            tags=[QuestionTag(**row) for row in self._repository.get_tags(question_id)],
            knowledge_chunks=[
                KnowledgeChunk(**row)
                for row in self._repository.get_knowledge_chunks(question_id)
            ],
            assets=[
                _asset_from_row(row) for row in self._repository.get_assets(question_id)
            ],
        )

    def search_questions(
        self,
        *,
        domain: str | None,
        module_name: str | None,
        question_type: str | None,
        tag: str | None,
        province: str | None,
        year: int | None,
        requires_images: bool | None,
        limit: int,
    ) -> QuestionSearchResponse:
        items = self._repository.search_questions(
            domain=domain,
            module_name=module_name,
            question_type=question_type,
            tag=tag,
            province=province,
            year=year,
            requires_images=requires_images,
            limit=limit,
        )
        return QuestionSearchResponse(
            items=[QuestionSummary(**item) for item in items],
            limit=limit,
        )

    def select_weighted_xingce_practice(
        self,
        *,
        exam_scope: str = "guokao",
        limit: int = 3,
        module_name: str | None = None,
    ) -> WeightedPracticeSelection:
        now = datetime.now(UTC)
        node = self._repository.get_top_knowledge_map_node(
            exam_scope=exam_scope,
            track="xingce",
            node_type="knowledge_point",
            now=now,
            module_name=module_name,
        )
        if node is None:
            raise KnowledgeMapUnavailableError(
                "current fresh active knowledge map release has no eligible "
                "guokao/xingce knowledge_point rows"
            )

        rows = self._repository.select_weighted_practice_questions(
            release_id=node.release_id,
            node_id=node.node_id,
            limit=limit,
        )
        if len(rows) < limit:
            raise ExamPracticeQuestionsUnavailableError(
                "product.knowledge_map_nodes selected a node without enough practice questions"
            )
        return WeightedPracticeSelection.from_node(
            node=node,
            items=[QuestionSummary(**row) for row in rows],
        )

    def resolve_knowledge_map_node(
        self,
        *,
        exam_scope: str,
        track: str,
        module_name: str,
        tag_type: str,
        tag_value: str,
    ) -> KnowledgeMapNodeRecord | None:
        return self._repository.get_knowledge_map_node_by_tag(
            exam_scope=exam_scope,
            track=track,
            module_name=module_name,
            tag_type=tag_type,
            tag_value=tag_value,
            now=datetime.now(UTC),
        )


def _asset_from_row(row: dict[str, object]) -> QuestionAsset:
    object_key = row.get("object_key")
    tos_object_key = _safe_tos_object_key(object_key if isinstance(object_key, str) else None)
    return QuestionAsset(
        asset_id=str(row["asset_id"]),
        part_kind=row.get("part_kind") if isinstance(row.get("part_kind"), str) else None,
        part_index=row.get("part_index") if isinstance(row.get("part_index"), int) else None,
        image_ordinal=row.get("image_ordinal") if isinstance(row.get("image_ordinal"), int) else None,
        source_image_ref=row.get("image_src") if isinstance(row.get("image_src"), str) else None,
        tos_object_key=tos_object_key,
        storage_status="tos" if tos_object_key else "source_metadata_only",
        representative=bool(row.get("is_representative_asset")),
    )


def _safe_tos_object_key(object_key: str | None) -> str | None:
    if not object_key:
        return None
    blocked_prefixes = ("data/", "/", "./", "../")
    if object_key.startswith(blocked_prefixes):
        return None
    return object_key
