from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrivateQuestionBookModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CreatePrivateQuestionBookCommand(PrivateQuestionBookModel):
    import_id: UUID = Field(alias="importId")
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    title: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def normalized_title(self) -> "CreatePrivateQuestionBookCommand":
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("title cannot be blank")
        return self


class PrivateQuestionBookItem(PrivateQuestionBookModel):
    ordinal: int = Field(ge=1)
    question_unit_id: str = Field(
        alias="questionUnitId", pattern=r"^[0-9a-f]{64}$"
    )
    state: Literal["matched", "needs_confirmation", "unmatched"]
    content_match_version_id: UUID | None = Field(
        default=None, alias="contentMatchVersionId"
    )
    confirmed_content_version_id: str | None = Field(
        default=None, alias="confirmedContentVersionId"
    )
    candidate_count: int = Field(alias="candidateCount", ge=0)


class PrivateQuestionBookSummary(PrivateQuestionBookModel):
    book_id: UUID = Field(alias="bookId")
    source_version_id: UUID = Field(alias="sourceVersionId")
    import_id: UUID = Field(alias="importId")
    artifact_version_id: UUID = Field(alias="artifactVersionId")
    recognition_revision_id: UUID = Field(alias="recognitionRevisionId")
    title: str
    state: Literal["ready", "needs_review"]
    total_question_count: int = Field(alias="totalQuestionCount", ge=1)
    resolved_question_count: int = Field(alias="resolvedQuestionCount", ge=0)
    unresolved_question_count: int = Field(alias="unresolvedQuestionCount", ge=0)
    next_unresolved_question_unit_id: str | None = Field(
        default=None,
        alias="nextUnresolvedQuestionUnitId",
        pattern=r"^[0-9a-f]{64}$",
    )
    created_at: datetime = Field(alias="createdAt")


class PrivateQuestionBookDetail(PrivateQuestionBookSummary):
    questions: tuple[PrivateQuestionBookItem, ...]


class PrivateQuestionBookList(PrivateQuestionBookModel):
    books: tuple[PrivateQuestionBookSummary, ...]
    generated_at: datetime = Field(alias="generatedAt")


class StartPrivateQuestionBookPracticeCommand(PrivateQuestionBookModel):
    plan_task_id: UUID = Field(alias="planTaskId")


class PrivateQuestionBookPracticeLaunch(PrivateQuestionBookModel):
    book_id: UUID = Field(alias="bookId")
    source_version_id: UUID = Field(alias="sourceVersionId")
    selected_question_count: int = Field(alias="selectedQuestionCount", ge=1)
    excluded_question_count: int = Field(alias="excludedQuestionCount", ge=0)
    excluded_counts: dict[str, int] = Field(alias="excludedCounts")
    session_id: UUID = Field(alias="sessionId")
    plan_task_id: UUID = Field(alias="planTaskId")
    start_url: str = Field(alias="startUrl", min_length=1)
