from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class QuestionOption(KnowledgeBaseModel):
    option_index: int = Field(alias="optionIndex")
    label: str
    option_text: str = Field(alias="optionText")
    option_html: str | None = Field(default=None, alias="optionHtml")


class QuestionExplanation(KnowledgeBaseModel):
    preferred_analysis: str = Field(alias="preferredAnalysis")
    original_analysis: str | None = Field(default=None, alias="originalAnalysis")
    stored_analysis: str | None = Field(default=None, alias="storedAnalysis")
    analysis_source: str | None = Field(default=None, alias="analysisSource")
    analysis_status: str | None = Field(default=None, alias="analysisStatus")
    prefer_ai_analysis: bool = Field(alias="preferAiAnalysis")
    needs_review: bool = Field(alias="needsReview")


class QuestionOccurrence(KnowledgeBaseModel):
    occurrence_id: str = Field(alias="occurrenceId")
    source_question_id: str | None = Field(default=None, alias="sourceQuestionId")
    source_dataset_id: str | None = Field(default=None, alias="sourceDatasetId")
    province: str | None = None
    year: int | None = None
    number: int | None = None
    section: str | None = None
    paper_title: str | None = Field(default=None, alias="paperTitle")
    source_path: str | None = Field(default=None, alias="sourcePath")
    rank: int | None = None


class QuestionTag(KnowledgeBaseModel):
    tag_type: str = Field(alias="tagType")
    tag_value: str = Field(alias="tagValue")
    sort_order: int | None = Field(default=None, alias="sortOrder")
    evidence_link_count: int | None = Field(default=None, alias="evidenceLinkCount")


class KnowledgeChunk(KnowledgeBaseModel):
    knowledge_chunk_id: str = Field(alias="knowledgeChunkId")
    chunk_kind: str | None = Field(default=None, alias="chunkKind")
    chunk_index: int | None = Field(default=None, alias="chunkIndex")
    title: str | None = None
    content: str
    display_path: str | None = Field(default=None, alias="displayPath")
    token_estimate: int | None = Field(default=None, alias="tokenEstimate")
    metadata: dict[str, Any] | None = None


class QuestionAsset(KnowledgeBaseModel):
    asset_id: str = Field(alias="assetId")
    part_kind: str | None = Field(default=None, alias="partKind")
    part_index: int | None = Field(default=None, alias="partIndex")
    image_ordinal: int | None = Field(default=None, alias="imageOrdinal")
    source_image_ref: str | None = Field(default=None, alias="sourceImageRef")
    tos_object_key: str | None = Field(default=None, alias="tosObjectKey")
    storage_status: Literal["tos", "source_metadata_only"] = Field(alias="storageStatus")
    representative: bool = False


class QuestionDetail(KnowledgeBaseModel):
    question_id: str = Field(alias="questionId")
    domain: Literal["objective", "essay"] | str
    status: str | None = None
    module_name: str | None = Field(default=None, alias="moduleName")
    question_type: str | None = Field(default=None, alias="questionType")
    display_level1: str | None = Field(default=None, alias="displayLevel1")
    display_level2: str | None = Field(default=None, alias="displayLevel2")
    display_level3: str | None = Field(default=None, alias="displayLevel3")
    knowledge_family: str | None = Field(default=None, alias="knowledgeFamily")
    teaching_point: str | None = Field(default=None, alias="teachingPoint")
    micro_skill: str | None = Field(default=None, alias="microSkill")
    stem: str
    stem_html: str | None = Field(default=None, alias="stemHtml")
    answer: str | None = None
    material_text: str | None = Field(default=None, alias="materialText")
    material_html: str | None = Field(default=None, alias="materialHtml")
    difficulty: float | None = None
    visual_required: bool = Field(alias="visualRequired")
    needs_multimodal: bool = Field(alias="needsMultimodal")
    shared_context_required: bool = Field(alias="sharedContextRequired")
    occurrence_count: int | None = Field(default=None, alias="occurrenceCount")
    occurrence_province_count: int | None = Field(default=None, alias="occurrenceProvinceCount")
    first_year: int | None = Field(default=None, alias="firstYear")
    latest_year: int | None = Field(default=None, alias="latestYear")
    source_member_count: int | None = Field(default=None, alias="sourceMemberCount")
    options: list[QuestionOption]
    explanation: QuestionExplanation | None = None
    occurrences: list[QuestionOccurrence]
    tags: list[QuestionTag]
    knowledge_chunks: list[KnowledgeChunk] = Field(alias="knowledgeChunks")
    assets: list[QuestionAsset]


class QuestionSummary(KnowledgeBaseModel):
    question_id: str = Field(alias="questionId")
    domain: str
    module_name: str | None = Field(default=None, alias="moduleName")
    question_type: str | None = Field(default=None, alias="questionType")
    stem: str
    answer: str | None = None
    visual_required: bool = Field(alias="visualRequired")
    has_explanation: bool = Field(alias="hasExplanation")
    occurrence_count: int | None = Field(default=None, alias="occurrenceCount")
    latest_year: int | None = Field(default=None, alias="latestYear")


class QuestionSearchResponse(KnowledgeBaseModel):
    items: list[QuestionSummary]
    limit: int
