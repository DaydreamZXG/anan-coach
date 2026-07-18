from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.domains.essay_intelligence.rubric_compiler import (
    RubricCompilationSource,
)
from app.domains.essay_intelligence.schemas import (
    EssayContractModel,
    EssayRubricCriterion,
    EssayRubricPoint,
    QuestionType,
)


RUBRIC_AI_PIPELINE_VERSION = "guokao-essay-rubric-ai-pipeline-v1"
RUBRIC_AI_GENERATOR_PROMPT_VERSION = "guokao-essay-rubric-generator-prompt-v2"
RUBRIC_AI_GENERATOR_SCHEMA_VERSION = "guokao-essay-rubric-generator-schema-v1"
RUBRIC_AI_CRITIC_PROMPT_VERSION = "guokao-essay-rubric-critic-prompt-v2"
RUBRIC_AI_CRITIC_SCHEMA_VERSION = "guokao-essay-rubric-critic-schema-v1"
RUBRIC_AI_COMPILER_VERSION = "guokao-essay-rubric-compiler-v1"
RUBRIC_AI_COMPILATION_PROMPT_VERSION = (
    "guokao-essay-rubric-generator-v2+critic-v2"
)


class RubricAIProviderSegment(EssayContractModel):
    material_id: str = Field(alias="materialId", min_length=1, max_length=160)
    segment_id: str = Field(alias="segmentId", min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=50000)
    text_sha256: str = Field(alias="textSha256", pattern=r"^[0-9a-f]{64}$")


class RubricAIProviderDocument(EssayContractModel):
    pipeline_version: Literal["guokao-essay-rubric-ai-pipeline-v1"] = Field(
        alias="pipelineVersion"
    )
    task_version_id: UUID = Field(alias="taskVersionId")
    task_key: str = Field(alias="taskKey", min_length=1, max_length=160)
    question_type: QuestionType = Field(alias="questionType")
    prompt: str = Field(min_length=1, max_length=10000)
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[0-9a-f]{64}$")
    maximum_score: float = Field(alias="maximumScore", gt=0, le=100)
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    material_content_sha256: str = Field(
        alias="materialContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    material_completeness: Literal["complete"] = Field(
        alias="materialCompleteness"
    )
    segments: list[RubricAIProviderSegment] = Field(min_length=1)


class RubricAIGeneratorOutput(EssayContractModel):
    schema_version: Literal["guokao-essay-rubric-generator-schema-v1"] = Field(
        alias="schemaVersion"
    )
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[0-9a-f]{64}$")
    material_content_sha256: str = Field(
        alias="materialContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    total_maximum: float = Field(alias="totalMaximum", gt=0, le=100)
    criteria: list[EssayRubricCriterion] = Field(min_length=1)
    points: list[EssayRubricPoint] = Field(min_length=1)


class RubricAICriticOutput(EssayContractModel):
    schema_version: Literal["guokao-essay-rubric-critic-schema-v1"] = Field(
        alias="schemaVersion"
    )
    approved: bool
    rejection_codes: list[str] = Field(
        alias="rejectionCodes",
        max_length=20,
    )
    revision_summary: str = Field(
        alias="revisionSummary",
        min_length=1,
        max_length=2000,
    )
    task_version_id: UUID = Field(alias="taskVersionId")
    material_bundle_version_id: UUID = Field(alias="materialBundleVersionId")
    prompt_sha256: str = Field(alias="promptSha256", pattern=r"^[0-9a-f]{64}$")
    material_content_sha256: str = Field(
        alias="materialContentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    total_maximum: float = Field(alias="totalMaximum", gt=0, le=100)
    criteria: list[EssayRubricCriterion] = Field(min_length=1)
    points: list[EssayRubricPoint] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision(self) -> "RubricAICriticOutput":
        normalized = [item.strip() for item in self.rejection_codes]
        if any(not item for item in normalized) or len(normalized) != len(
            set(normalized)
        ):
            raise ValueError("critic rejection codes must be non-empty and unique")
        if self.approved and normalized:
            raise ValueError("approved critic output cannot include rejection codes")
        if not self.approved and not normalized:
            raise ValueError("unapproved critic output requires rejection codes")
        return self


def build_rubric_ai_provider_document(
    source: RubricCompilationSource,
) -> RubricAIProviderDocument:
    return RubricAIProviderDocument(
        pipeline_version=RUBRIC_AI_PIPELINE_VERSION,
        task_version_id=source.task_version_id,
        task_key=source.task_key,
        question_type=source.question_type,
        prompt=source.prompt,
        prompt_sha256=source.prompt_sha256,
        maximum_score=source.maximum_score,
        material_bundle_version_id=source.material_bundle_version_id,
        material_content_sha256=source.material_content_sha256,
        material_completeness="complete",
        segments=[
            RubricAIProviderSegment(
                material_id=item.material_id,
                segment_id=item.segment_id,
                text=item.text,
                text_sha256=item.text_sha256,
            )
            for item in source.segments
        ],
    )


def rubric_ai_generator_input_fingerprint(
    *,
    document: RubricAIProviderDocument,
    provider: str,
    model: str,
) -> str:
    return canonical_sha256(
        {
            "phase": "generator",
            "promptVersion": RUBRIC_AI_GENERATOR_PROMPT_VERSION,
            "schemaVersion": RUBRIC_AI_GENERATOR_SCHEMA_VERSION,
            "provider": provider,
            "model": model,
            "document": document.model_dump(by_alias=True, mode="json"),
        }
    )


def rubric_ai_critic_input_fingerprint(
    *,
    document: RubricAIProviderDocument,
    candidate: RubricAIGeneratorOutput,
    provider: str,
    model: str,
) -> str:
    return canonical_sha256(
        {
            "phase": "critic",
            "promptVersion": RUBRIC_AI_CRITIC_PROMPT_VERSION,
            "schemaVersion": RUBRIC_AI_CRITIC_SCHEMA_VERSION,
            "provider": provider,
            "model": model,
            "document": document.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )


def canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
