from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.domains.data_rights.repository import DataRightsRepository
from app.domains.data_rights.schemas import (
    AccountDeletionCreateRequest,
    AccountDeletionView,
    DataExportCreateRequest,
    DataExportPayload,
    DataExportView,
    PrivacyOverview,
)


class DataRightsService:
    def __init__(
        self,
        repository: DataRightsRepository,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(UTC))

    def overview(self, *, user_id: str) -> PrivacyOverview:
        return self._repository.overview(user_id=user_id, now=self._now())

    def create_export(
        self, *, user_id: str, request: DataExportCreateRequest
    ) -> DataExportView:
        return self._repository.create_export(
            user_id=user_id, command_key=request.command_key, now=self._now()
        )

    def export_status(self, *, user_id: str, export_id: UUID) -> DataExportView:
        return self._repository.export_by_id(
            user_id=user_id, export_id=export_id, now=self._now()
        )

    def export_payload(self, *, user_id: str, export_id: UUID) -> DataExportPayload:
        return self._repository.export_payload(
            user_id=user_id, export_id=export_id, now=self._now()
        )

    def request_deletion(
        self, *, user_id: str, request: AccountDeletionCreateRequest
    ) -> AccountDeletionView:
        return self._repository.create_deletion(
            user_id=user_id,
            command_key=request.command_key,
            confirmation_phrase=request.confirmation_phrase,
            now=self._now(),
        )

    def deletion_status(
        self, *, user_id: str, deletion_id: UUID
    ) -> AccountDeletionView:
        return self._repository.deletion_by_id(
            user_id=user_id, deletion_id=deletion_id
        )
