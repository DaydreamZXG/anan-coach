from __future__ import annotations

from base64 import b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from hashlib import sha256
import json

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.xingce_learning.favorites.repository import (
    QuestionFavoriteRepository,
)
from app.domains.xingce_learning.favorites.schemas import (
    FAVORITE_PREVIEW_MODULE_MAX_LENGTH,
    FAVORITE_PREVIEW_STEM_MAX_LENGTH,
    QuestionFavoriteListItem,
    QuestionFavoritePage,
    QuestionFavoritePreview,
    QuestionFavoriteReceipt,
    QuestionFavoriteState,
    SetQuestionFavoriteCommand,
)


class QuestionFavoriteError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class QuestionFavoriteService:
    def __init__(
        self,
        *,
        repository: QuestionFavoriteRepository | None = None,
    ) -> None:
        self._repository = repository or QuestionFavoriteRepository()

    def set_state(
        self,
        session: Session,
        *,
        user_id: str,
        question_id: str,
        command_id: str,
        command: SetQuestionFavoriteCommand,
        now: datetime | None = None,
    ) -> QuestionFavoriteReceipt:
        canonical_question_id = _canonical_question_id(question_id)
        fingerprint = _fingerprint(
            question_id=canonical_question_id,
            command=command,
        )
        self._repository.lock_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        existing_command = self._repository.command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        if existing_command is not None:
            if str(existing_command["request_fingerprint"]) != fingerprint:
                raise QuestionFavoriteError(
                    "xingce_favorite_idempotency_key_reused",
                    "同一幂等键不能用于不同的收藏请求。",
                    status_code=409,
                )
            return QuestionFavoriteReceipt.model_validate(
                existing_command["response_payload"]
            )

        self._repository.lock_question(
            session,
            user_id=user_id,
            question_id=canonical_question_id,
        )
        recorded_at = _utc(now)
        catalog = self._required_catalog(session, canonical_question_id)
        current = self._repository.state(
            session,
            user_id=user_id,
            question_id=canonical_question_id,
        )

        if command.desired_state == "favorited":
            if not _is_available(catalog):
                raise QuestionFavoriteError(
                    "xingce_favorite_question_unavailable",
                    "当前题目已不可收藏。",
                    status_code=409,
                )
            content_version_id = command.content_version_id
            source_session_id = command.source_session_id
            if content_version_id is None or source_session_id is None:
                raise AssertionError("validated favorite provenance is missing")
            if not self._repository.source_session_contains(
                session,
                user_id=user_id,
                source_session_id=str(source_session_id),
                question_id=canonical_question_id,
                content_version_id=content_version_id,
            ):
                raise QuestionFavoriteError(
                    "xingce_favorite_source_not_found",
                    "当前账户没有该题目版本的行测作答来源。",
                    status_code=404,
                )
            if current is None or not bool(current["favorited"]):
                current = self._repository.set_favorited(
                    session,
                    user_id=user_id,
                    question_id=canonical_question_id,
                    content_version_id=content_version_id,
                    source_session_id=str(source_session_id),
                    revision=(int(current["revision"]) + 1 if current else 1),
                    now=recorded_at,
                )
        elif current is not None and bool(current["favorited"]):
            current = self._repository.set_not_favorited(
                session,
                user_id=user_id,
                question_id=canonical_question_id,
                revision=int(current["revision"]) + 1,
                now=recorded_at,
            )

        receipt = _receipt(
            question_id=canonical_question_id,
            state=current,
        )
        self._repository.insert_command(
            session,
            user_id=user_id,
            command_id=command_id,
            question_id=canonical_question_id,
            request_fingerprint=fingerprint,
            response_payload=receipt.model_dump(by_alias=True, mode="json"),
            created_at=recorded_at,
        )
        return receipt

    def get_state(
        self,
        session: Session,
        *,
        user_id: str,
        question_id: str,
    ) -> QuestionFavoriteState:
        canonical_question_id = _canonical_question_id(question_id)
        catalog = self._required_catalog(session, canonical_question_id)
        state = self._repository.state(
            session,
            user_id=user_id,
            question_id=canonical_question_id,
        )
        return _state_projection(
            question_id=canonical_question_id,
            state=state,
            catalog=catalog,
        )

    def list_favorites(
        self,
        session: Session,
        *,
        user_id: str,
        cursor: str | None,
        page_size: int,
        now: datetime | None = None,
    ) -> QuestionFavoritePage:
        cursor_at, cursor_question_id = _decode_cursor(cursor)
        rows = self._repository.list_favorited(
            session,
            user_id=user_id,
            cursor_at=cursor_at,
            cursor_question_id=cursor_question_id,
            limit=page_size + 1,
        )
        has_more = len(rows) > page_size
        page_rows = rows[:page_size]
        items = [_list_item(row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(
                favorited_at=last["favorited_at"],
                question_id=str(last["question_id"]),
            )
        return QuestionFavoritePage(
            items=items,
            nextCursor=next_cursor,
            generatedAt=_utc(now),
        )

    def _required_catalog(
        self,
        session: Session,
        question_id: str,
    ) -> RowMapping:
        catalog = self._repository.catalog(session, question_id=question_id)
        if catalog is None or not _is_xingce_question(catalog):
            raise QuestionFavoriteError(
                "xingce_favorite_question_not_found",
                "行测题目不存在。",
                status_code=404,
            )
        return catalog


def _receipt(
    *,
    question_id: str,
    state: RowMapping | None,
) -> QuestionFavoriteReceipt:
    favorited = state is not None and bool(state["favorited"])
    return QuestionFavoriteReceipt(
        questionId=question_id,
        favoriteState=("favorited" if favorited else "not_favorited"),
        revision=(int(state["revision"]) if state is not None else 0),
        savedContentVersionId=(
            str(state["saved_content_version_id"])
            if favorited and state["saved_content_version_id"] is not None
            else None
        ),
        favoritedAt=(state["favorited_at"] if favorited else None),
        updatedAt=(state["updated_at"] if state is not None else None),
    )


def _state_projection(
    *,
    question_id: str,
    state: RowMapping | None,
    catalog: RowMapping,
) -> QuestionFavoriteState:
    receipt = _receipt(question_id=question_id, state=state)
    current_content_version_id = catalog["current_content_version_id"]
    available = _is_available(catalog)
    return QuestionFavoriteState(
        **receipt.model_dump(by_alias=True),
        currentContentVersionId=(
            str(current_content_version_id)
            if current_content_version_id is not None
            else None
        ),
        availability=("available" if available else "unavailable"),
        contentChanged=bool(
            receipt.favorite_state == "favorited"
            and receipt.saved_content_version_id is not None
            and current_content_version_id is not None
            and receipt.saved_content_version_id != str(current_content_version_id)
        ),
    )


def _list_item(row: RowMapping) -> QuestionFavoriteListItem:
    state = _state_projection(
        question_id=str(row["question_id"]),
        state=row,
        catalog=row,
    )
    preview = _preview(row) if state.availability == "available" else None
    return QuestionFavoriteListItem(
        **state.model_dump(by_alias=True),
        preview=preview,
    )


def _preview(row: RowMapping) -> QuestionFavoritePreview | None:
    stem = _bounded_preview_text(
        row["current_stem"],
        max_length=FAVORITE_PREVIEW_STEM_MAX_LENGTH,
    )
    if stem is None:
        return None
    return QuestionFavoritePreview(
        moduleName=_bounded_preview_text(
            row["current_module_name"],
            max_length=FAVORITE_PREVIEW_MODULE_MAX_LENGTH,
        ),
        stem=stem,
    )


def _bounded_preview_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}…"


def _is_xingce_question(catalog: RowMapping) -> bool:
    return (
        str(catalog["current_track"]) == "xingce"
        if catalog["current_track"] is not None
        else str(catalog["domain"]) == "objective"
    )


def _is_available(catalog: RowMapping) -> bool:
    return (
        str(catalog["status"]) in {"active", "canonical"}
        and catalog["current_content_version_id"] is not None
        and str(catalog["current_track"]) == "xingce"
    )


def _canonical_question_id(question_id: str) -> str:
    canonical = question_id.strip()
    if not canonical or len(canonical) > 240:
        raise QuestionFavoriteError(
            "xingce_favorite_question_not_found",
            "行测题目不存在。",
            status_code=404,
        )
    return canonical


def _fingerprint(
    *,
    question_id: str,
    command: SetQuestionFavoriteCommand,
) -> str:
    payload = {
        "contractVersion": "xingce-question-favorite-command@v1",
        "questionId": question_id,
        "body": command.model_dump(by_alias=True, mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _encode_cursor(*, favorited_at: datetime, question_id: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "favoritedAt": favorited_at.isoformat(),
            "questionId": question_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            b64decode(
                (cursor + padding).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        )
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or not isinstance(payload.get("favoritedAt"), str)
            or not isinstance(payload.get("questionId"), str)
            or not payload["questionId"]
            or len(payload["questionId"]) > 240
        ):
            raise ValueError("cursor shape")
        cursor_at = datetime.fromisoformat(payload["favoritedAt"])
        if cursor_at.tzinfo is None:
            raise ValueError("cursor timezone")
        return cursor_at.astimezone(UTC), payload["questionId"]
    except (
        BinasciiError,
        UnicodeDecodeError,
        UnicodeEncodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise QuestionFavoriteError(
            "xingce_favorite_cursor_invalid",
            "收藏列表游标无效，请从第一页重新加载。",
            status_code=422,
        ) from exc


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("favorite timestamps must include timezone")
    return current.astimezone(UTC)
