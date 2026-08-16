from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.domains.external_ingestion.recognition.models import (
    RecognitionCandidatePayload,
)
from app.domains.private_knowledge.repository import (
    PrivateKnowledgeConflict,
    PrivateKnowledgeNotFound,
    PrivateKnowledgeRepository,
)
from app.domains.private_knowledge.schemas import (
    MarkdownExport,
    PrivateSourceDeletionView,
)
from app.domains.private_knowledge.service import (
    _fingerprint,
    _markdown_from_recognition,
    _pages,
)
from app.domains.private_question_books.repository import (
    PrivateQuestionBookRepository,
)
from app.domains.private_question_books.schemas import (
    CreatePrivateQuestionBookCommand,
    PrivateQuestionBookDetail,
    PrivateQuestionBookItem,
    PrivateQuestionBookList,
    PrivateQuestionBookPracticeLaunch,
    PrivateQuestionBookSummary,
    StartPrivateQuestionBookPracticeCommand,
)
from app.domains.xingce_learning.real_attempt.service import (
    ExplicitQuestionSelection,
    XingceAttemptError,
    XingceAttemptService,
)


class PrivateQuestionBookError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class PrivateQuestionBookService:
    def __init__(
        self,
        repository: PrivateQuestionBookRepository,
        private_knowledge: PrivateKnowledgeRepository,
        attempt_service: XingceAttemptService | None = None,
    ) -> None:
        self.repository = repository
        self.private_knowledge = private_knowledge
        self.attempt_service = attempt_service or XingceAttemptService()

    def create(
        self,
        *,
        owner_user_id: str,
        command: CreatePrivateQuestionBookCommand,
        idempotency_key: str,
    ) -> PrivateQuestionBookDetail:
        request_fingerprint = _fingerprint(
            command.model_dump(by_alias=True, mode="json")
        )
        replay = self.private_knowledge.command(
            owner_user_id=owner_user_id,
            command_type="source_create",
            key=idempotency_key,
        )
        if replay is not None:
            if (
                str(replay["request_fingerprint"]) != request_fingerprint
                or str(replay["document_kind"]) != "question_book"
            ):
                raise PrivateQuestionBookError(
                    "idempotency_conflict",
                    "这次保存请求已经用于其他内容。",
                )
            return self.get(
                owner_user_id=owner_user_id,
                book_id=UUID(str(replay["source_id"])),
            )

        context = self.repository.creation_context(
            owner_user_id=owner_user_id,
            import_id=command.import_id,
            recognition_revision_id=command.recognition_revision_id,
        )
        if context is None:
            raise PrivateQuestionBookError(
                "import_or_recognition_unavailable",
                "题本尚未完成确认，请先继续处理当前导入。",
                status_code=404,
            )
        artifact_version_id = UUID(str(context["artifact_version_id"]))
        self.repository.lock_artifact(
            owner_user_id=owner_user_id,
            artifact_version_id=artifact_version_id,
        )
        existing = self.repository.source_for_artifact(
            owner_user_id=owner_user_id,
            artifact_version_id=artifact_version_id,
        )
        if existing is not None:
            if str(existing["document_kind"]) != "question_book":
                raise PrivateQuestionBookError(
                    "artifact_already_registered",
                    "这份资料已以其他类型保存。",
                )
            return self.get(
                owner_user_id=owner_user_id,
                book_id=UUID(str(existing["source_id"])),
            )

        payload = _payload(context["candidate_payload"])
        markdown = _markdown_from_recognition(
            payload.model_dump(by_alias=True, mode="json")
        )
        source_id = uuid4()
        self.private_knowledge.insert_source(
            owner_user_id=owner_user_id,
            command_type="source_create",
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            source_id=source_id,
            version_no=1,
            title=command.title,
            document_kind="question_book",
            artifact_version_id=artifact_version_id,
            recognition_revision_id=command.recognition_revision_id,
            markdown=markdown,
            supersedes_source_version_id=None,
            pages=_pages(markdown),
            created_at=datetime.now(UTC),
        )
        return self.get(owner_user_id=owner_user_id, book_id=source_id)

    def list(self, *, owner_user_id: str) -> PrivateQuestionBookList:
        books = tuple(
            self._project(owner_user_id=owner_user_id, row=row, include_questions=False)
            for row in self.repository.current_heads(owner_user_id=owner_user_id)
        )
        return PrivateQuestionBookList(books=books, generatedAt=datetime.now(UTC))

    def get(
        self, *, owner_user_id: str, book_id: UUID
    ) -> PrivateQuestionBookDetail:
        row = self.repository.current_head(
            owner_user_id=owner_user_id, book_id=book_id
        )
        if row is None:
            raise PrivateQuestionBookError(
                "question_book_not_found", "题本不存在。", status_code=404
            )
        result = self._project(
            owner_user_id=owner_user_id, row=row, include_questions=True
        )
        assert isinstance(result, PrivateQuestionBookDetail)
        return result

    def start_practice(
        self,
        *,
        owner_user_id: str,
        book_id: UUID,
        command: StartPrivateQuestionBookPracticeCommand,
        idempotency_key: str,
    ) -> PrivateQuestionBookPracticeLaunch:
        try:
            started = self.attempt_service.start_or_resume_from_question_book(
                self.repository.session,
                user_id=owner_user_id,
                command_id=idempotency_key,
                plan_task_id=command.plan_task_id,
                book_id=book_id,
                selection_loader=lambda limit: self._practice_selection(
                    owner_user_id=owner_user_id,
                    book_id=book_id,
                    limit=limit,
                ),
            )
        except XingceAttemptError as exc:
            raise PrivateQuestionBookError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
            ) from exc
        context = started.selection_context
        selected = context["selectedItems"]
        excluded = context["excludedCounts"]
        return PrivateQuestionBookPracticeLaunch(
            bookId=context["bookId"],
            sourceVersionId=context["sourceVersionId"],
            selectedQuestionCount=len(selected),
            excludedQuestionCount=sum(int(value) for value in excluded.values()),
            excludedCounts=excluded,
            sessionId=started.session.session_id,
            planTaskId=started.session.plan_task_id,
            startUrl=(
                "/xingce/xingce-task-intro/index?mode=targeted&planTaskId="
                f"{started.session.plan_task_id}"
            ),
        )

    def export_markdown(
        self, *, owner_user_id: str, book_id: UUID
    ) -> MarkdownExport:
        self._require_kind(owner_user_id=owner_user_id, book_id=book_id)
        try:
            return self.private_knowledge.markdown_export(
                owner_user_id=owner_user_id, source_id=book_id
            )
        except PrivateKnowledgeNotFound as exc:
            raise PrivateQuestionBookError(
                "question_book_not_found", "题本不存在。", status_code=404
            ) from exc

    def request_delete(
        self,
        *,
        owner_user_id: str,
        book_id: UUID,
        idempotency_key: str,
    ) -> PrivateSourceDeletionView:
        kind = self.repository.question_book_head_kind(
            owner_user_id=owner_user_id, source_id=book_id
        )
        if kind is not None and kind != "question_book":
            raise PrivateQuestionBookError(
                "question_book_not_found", "题本不存在。", status_code=404
            )
        try:
            return self.private_knowledge.request_delete(
                owner_user_id=owner_user_id,
                source_id=book_id,
                idempotency_key=idempotency_key,
            )
        except PrivateKnowledgeNotFound as exc:
            raise PrivateQuestionBookError(
                "question_book_not_found", "题本不存在。", status_code=404
            ) from exc
        except PrivateKnowledgeConflict as exc:
            raise PrivateQuestionBookError(
                "idempotency_conflict", "删除请求已用于其他题本。"
            ) from exc

    def deletion(
        self, *, owner_user_id: str, deletion_id: UUID
    ) -> PrivateSourceDeletionView:
        try:
            return self.private_knowledge.deletion(
                owner_user_id=owner_user_id, deletion_id=deletion_id
            )
        except PrivateKnowledgeNotFound as exc:
            raise PrivateQuestionBookError(
                "deletion_not_found", "删除任务不存在。", status_code=404
            ) from exc

    def _require_kind(self, *, owner_user_id: str, book_id: UUID) -> None:
        if (
            self.repository.question_book_head_kind(
                owner_user_id=owner_user_id, source_id=book_id
            )
            != "question_book"
        ):
            raise PrivateQuestionBookError(
                "question_book_not_found", "题本不存在。", status_code=404
            )

    def _practice_selection(
        self,
        *,
        owner_user_id: str,
        book_id: UUID,
        limit: int,
    ) -> ExplicitQuestionSelection:
        detail = self.get(owner_user_id=owner_user_id, book_id=book_id)
        confirmed = tuple(
            item
            for item in detail.questions
            if item.state == "matched"
            and item.confirmed_content_version_id is not None
            and item.content_match_version_id is not None
        )
        resolved = self.repository.resolve_practice_content(
            content_version_ids=tuple(
                str(item.confirmed_content_version_id) for item in confirmed
            ),
            now=datetime.now(UTC),
        )
        excluded: dict[str, int] = {
            "needsConfirmation": sum(
                item.state == "needs_confirmation" for item in detail.questions
            ),
            "unmatched": sum(item.state == "unmatched" for item in detail.questions),
            "contentHeadChanged": 0,
            "notDeliveryEligible": 0,
            "contentContractInvalid": 0,
            "duplicate": 0,
            "overPlanLimit": 0,
        }
        selected_items: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        seen_question_ids: set[str] = set()
        reason_field = {
            "content_head_changed": "contentHeadChanged",
            "not_delivery_eligible": "notDeliveryEligible",
            "content_contract_invalid": "contentContractInvalid",
        }
        for item, resolution in zip(confirmed, resolved, strict=True):
            if resolution.exclusion_reason is not None:
                excluded[reason_field[resolution.exclusion_reason]] += 1
                continue
            assert resolution.question is not None
            assert resolution.question_id is not None
            assert resolution.delivery_release_id is not None
            if resolution.question_id in seen_question_ids:
                excluded["duplicate"] += 1
                continue
            if len(questions) >= limit:
                excluded["overPlanLimit"] += 1
                continue
            seen_question_ids.add(resolution.question_id)
            questions.append(resolution.question)
            selected_items.append(
                {
                    "questionUnitId": item.question_unit_id,
                    "contentMatchVersionId": str(item.content_match_version_id),
                    "questionId": resolution.question_id,
                    "contentVersionId": resolution.content_version_id,
                    "deliveryReleaseId": resolution.delivery_release_id,
                }
            )
        if not questions:
            raise PrivateQuestionBookError(
                "question_book_practice_content_unavailable",
                "这本题本暂时没有可直接练的已确认题；先确认一个歧义项，或等待对应题库发布后再试。",
                status_code=409,
            )
        context = {
            "contractVersion": "private-question-book-selection@v1",
            "sourceKind": "question_book",
            "bookId": str(detail.book_id),
            "sourceVersionId": str(detail.source_version_id),
            "artifactVersionId": str(detail.artifact_version_id),
            "recognitionRevisionId": str(detail.recognition_revision_id),
            "totalQuestionCount": detail.total_question_count,
            "selectedItems": selected_items,
            "excludedCounts": excluded,
        }
        return ExplicitQuestionSelection(
            questions=tuple(questions),
            selection_context=context,
        )

    def _project(
        self, *, owner_user_id: str, row, include_questions: bool
    ) -> PrivateQuestionBookSummary | PrivateQuestionBookDetail:
        payload = _payload(row["candidate_payload"])
        matches = self.repository.match_heads(
            owner_user_id=owner_user_id,
            artifact_version_id=UUID(str(row["artifact_version_id"])),
            recognition_revision_id=UUID(str(row["recognition_revision_id"])),
        )
        questions: list[PrivateQuestionBookItem] = []
        for unit in payload.question_units:
            match = matches.get(unit.question_unit_id)
            match_state = str(match["state"]) if match is not None else ""
            if match_state == "confirmed":
                state = "matched"
            elif match is not None and int(match["candidate_count"] or 0) > 0:
                state = "needs_confirmation"
            else:
                state = "unmatched"
            questions.append(
                PrivateQuestionBookItem(
                    ordinal=unit.ordinal,
                    questionUnitId=unit.question_unit_id,
                    state=state,
                    contentMatchVersionId=(
                        match["content_match_version_id"] if match is not None else None
                    ),
                    confirmedContentVersionId=(
                        match["confirmed_content_version_id"]
                        if match is not None
                        else None
                    ),
                    candidateCount=(
                        int(match["candidate_count"] or 0) if match is not None else 0
                    ),
                )
            )
        resolved = sum(item.state == "matched" for item in questions)
        unresolved = len(questions) - resolved
        next_unresolved = next(
            (item.question_unit_id for item in questions if item.state != "matched"),
            None,
        )
        common = dict(
            bookId=row["source_id"],
            sourceVersionId=row["source_version_id"],
            importId=row["import_id"],
            artifactVersionId=row["artifact_version_id"],
            recognitionRevisionId=row["recognition_revision_id"],
            title=row["title"],
            state="ready" if unresolved == 0 else "needs_review",
            totalQuestionCount=len(questions),
            resolvedQuestionCount=resolved,
            unresolvedQuestionCount=unresolved,
            nextUnresolvedQuestionUnitId=next_unresolved,
            createdAt=row["created_at"],
        )
        if include_questions:
            return PrivateQuestionBookDetail(**common, questions=tuple(questions))
        return PrivateQuestionBookSummary(**common)


def _payload(value: object) -> RecognitionCandidatePayload:
    try:
        payload = RecognitionCandidatePayload.model_validate(value)
    except ValueError as exc:
        raise PrivateQuestionBookError(
            "recognition_payload_invalid",
            "题本识别结果不完整，请重新处理。",
        ) from exc
    if payload.kind.value != "exam_segmentation" or not payload.question_units:
        raise PrivateQuestionBookError(
            "recognition_payload_invalid",
            "题本识别结果不包含可继续处理的题目。",
        )
    return payload
