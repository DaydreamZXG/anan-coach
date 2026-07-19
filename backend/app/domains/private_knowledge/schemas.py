from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrivateKnowledgeModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CreatePrivateSourceCommand(PrivateKnowledgeModel):
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_revision_id: UUID | None = Field(
        default=None, alias="recognitionRevisionId"
    )
    title: str = Field(min_length=1, max_length=200)
    document_kind: Literal["lecture", "note", "summary"] = Field(alias="documentKind")
    source_id: UUID | None = Field(default=None, alias="sourceId")
    expected_version_no: int | None = Field(
        default=None, alias="expectedVersionNo", ge=1
    )

    @model_validator(mode="after")
    def version_target_is_complete(self) -> "CreatePrivateSourceCommand":
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("title cannot be blank")
        if (self.source_id is None) != (self.expected_version_no is None):
            raise ValueError("sourceId and expectedVersionNo must be supplied together")
        return self


class PrivateSourceSummary(PrivateKnowledgeModel):
    source_id: UUID = Field(alias="sourceId")
    source_version_id: UUID = Field(alias="sourceVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    title: str
    document_kind: Literal["lecture", "note", "summary"] = Field(alias="documentKind")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_revision_id: UUID | None = Field(alias="recognitionRevisionId")
    page_count: int = Field(alias="pageCount", ge=1)
    chunk_count: int = Field(alias="chunkCount", ge=1)
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(alias="createdAt")


class PrivateSourceList(PrivateKnowledgeModel):
    sources: tuple[PrivateSourceSummary, ...]
    generated_at: datetime = Field(alias="generatedAt")


class PrivateCitation(PrivateKnowledgeModel):
    source_id: UUID = Field(alias="sourceId")
    source_version_id: UUID = Field(alias="sourceVersionId")
    chunk_id: UUID = Field(alias="chunkId")
    page_no: int = Field(alias="pageNo", ge=1)
    anchor: str
    title: str
    excerpt: str
    lexical_score: float = Field(alias="lexicalScore", ge=0, le=1)
    vector_score: float = Field(alias="vectorScore", ge=0, le=1)
    final_score: float = Field(alias="finalScore", ge=0, le=1)


class PrivateSearchResult(PrivateKnowledgeModel):
    query_fingerprint: str = Field(alias="queryFingerprint", pattern=r"^[0-9a-f]{64}$")
    policy_version: Literal["private-knowledge-search@v1"] = Field(
        default="private-knowledge-search@v1", alias="policyVersion"
    )
    citations: tuple[PrivateCitation, ...]


class MarkdownExport(PrivateKnowledgeModel):
    source_id: UUID = Field(alias="sourceId")
    source_version_id: UUID = Field(alias="sourceVersionId")
    version_no: int = Field(alias="versionNo", ge=1)
    filename: str
    content: str
    content_sha256: str = Field(alias="contentSha256", pattern=r"^[0-9a-f]{64}$")


class PrivateSourceDeletionView(PrivateKnowledgeModel):
    deletion_request_id: UUID = Field(alias="deletionRequestId")
    source_id: UUID = Field(alias="sourceId")
    state: Literal["queued", "running", "completed", "failed"]
    source_version_count: int = Field(alias="sourceVersionCount", ge=1)
    failure_code: str | None = Field(default=None, alias="failureCode")
    requested_at: datetime = Field(alias="requestedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
