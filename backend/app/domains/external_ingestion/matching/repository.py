from __future__ import annotations

from typing import Protocol
from uuid import UUID
import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import ContentMatchVersion, RecognitionRevision
from app.domains.external_ingestion.matching.models import (
    AuthorizationDecision,
    AuthorizationPurpose,
    IndexedContent,
    MatchQuery,
)


class ContentSearchPort(Protocol):
    def search(self, *, query: MatchQuery, maximum_candidates: int) -> tuple[IndexedContent, ...]: ...

    def get(self, *, content_version_id: str) -> IndexedContent | None: ...


class MatchAuthorizationPort(Protocol):
    def authorize(
        self,
        *,
        owner_user_id: UUID,
        content: IndexedContent,
        purpose: AuthorizationPurpose,
    ) -> AuthorizationDecision: ...

    def snapshot_current(self, *, snapshot_ref: str) -> bool: ...


class RecognitionAuthorityPort(Protocol):
    def is_current(self, *, revision: RecognitionRevision) -> bool: ...


class SQLRecognitionAuthority:
    def __init__(self, session: Session) -> None:
        self._session = session

    def is_current(self, *, revision: RecognitionRevision) -> bool:
        current = self._session.execute(
            text(
                """
                select recognition_revision_id
                from learning.recognition_revisions
                where artifact_version_id = :artifact_version_id
                  and owner_user_id = :owner_user_id
                  and recognition_kind = :recognition_kind
                order by revision_no desc
                limit 1
                """
            ),
            {
                "artifact_version_id": str(revision.artifact_version_id),
                "owner_user_id": str(revision.owner_user_id),
                "recognition_kind": revision.recognition_kind.value,
            },
        ).scalar_one_or_none()
        return current == revision.recognition_revision_id


class ContentMatchRepositoryPort(Protocol):
    def lock(self, *, key: str) -> None: ...

    def version(self, *, content_match_version_id: UUID) -> ContentMatchVersion | None: ...

    def head(self, *, content_match_id: UUID, owner_user_id: UUID) -> ContentMatchVersion | None: ...

    def insert(self, version: ContentMatchVersion) -> None: ...


class SQLContentMatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock(self, *, key: str) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def version(
        self, *, content_match_version_id: UUID
    ) -> ContentMatchVersion | None:
        row = self._session.execute(
            text(
                """
                select content_match_version_id, content_match_id,
                       artifact_version_id, recognition_revision_id, question_unit_id,
                       owner_user_id,
                       version_no, state, candidates, confirmed_content_version_id,
                       evaluation_reason,
                       matching_policy_version, input_fingerprint,
                       supersedes_content_match_version_id, created_at,
                       contract_payload
                from learning.content_match_versions
                where content_match_version_id = :content_match_version_id
                """
            ),
            {"content_match_version_id": str(content_match_version_id)},
        ).mappings().one_or_none()
        return _version(row) if row is not None else None

    def head(
        self, *, content_match_id: UUID, owner_user_id: UUID
    ) -> ContentMatchVersion | None:
        row = self._session.execute(
            text(
                """
                select content_match_version_id, content_match_id,
                       artifact_version_id, recognition_revision_id, question_unit_id,
                       owner_user_id,
                       version_no, state, candidates, confirmed_content_version_id,
                       evaluation_reason,
                       matching_policy_version, input_fingerprint,
                       supersedes_content_match_version_id, created_at,
                       contract_payload
                from learning.content_match_versions
                where content_match_id = :content_match_id
                  and owner_user_id = :owner_user_id
                order by version_no desc
                limit 1
                """
            ),
            {
                "content_match_id": str(content_match_id),
                "owner_user_id": str(owner_user_id),
            },
        ).mappings().one_or_none()
        return _version(row) if row is not None else None

    def insert(self, version: ContentMatchVersion) -> None:
        payload = version.model_dump(by_alias=True, mode="json")
        self._session.execute(
            text(
                """
                insert into learning.content_match_versions (
                  content_match_version_id, content_match_id,
                  artifact_version_id, recognition_revision_id, question_unit_id,
                  owner_user_id,
                  version_no, state, candidates, confirmed_content_version_id,
                  evaluation_reason,
                  matching_policy_version, input_fingerprint,
                  supersedes_content_match_version_id, contract_payload, created_at
                ) values (
                  :content_match_version_id, :content_match_id,
                  :artifact_version_id, :recognition_revision_id, :question_unit_id,
                  :owner_user_id,
                  :version_no, :state, cast(:candidates as jsonb),
                  :confirmed_content_version_id, :evaluation_reason,
                  :matching_policy_version,
                  :input_fingerprint, :supersedes_content_match_version_id,
                  cast(:contract_payload as jsonb), :created_at
                )
                """
            ),
            {
                "content_match_version_id": str(version.content_match_version_id),
                "content_match_id": str(version.content_match_id),
                "artifact_version_id": str(version.artifact_version_id),
                "recognition_revision_id": str(version.recognition_revision_id),
                "question_unit_id": version.question_unit_id,
                "owner_user_id": str(version.owner_user_id),
                "version_no": version.version_no,
                "state": version.state,
                "candidates": json.dumps(
                    [item.model_dump(by_alias=True, mode="json") for item in version.candidates],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "confirmed_content_version_id": version.confirmed_content_version_id,
                "evaluation_reason": version.evaluation_reason,
                "matching_policy_version": version.matching_policy_version,
                "input_fingerprint": version.input_fingerprint,
                "supersedes_content_match_version_id": (
                    str(version.supersedes_content_match_version_id)
                    if version.supersedes_content_match_version_id
                    else None
                ),
                "contract_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "created_at": version.created_at,
            },
        )


def _version(row) -> ContentMatchVersion:
    payload = dict(row["contract_payload"])
    # The sealed contract payload is authoritative and must bind the indexed columns.
    bindings = {
        "contentMatchVersionId": str(row["content_match_version_id"]),
        "contentMatchId": str(row["content_match_id"]),
        "artifactVersionId": str(row["artifact_version_id"]),
        "recognitionRevisionId": str(row["recognition_revision_id"]),
        "questionUnitId": str(row["question_unit_id"]),
        "ownerUserId": str(row["owner_user_id"]),
        "versionNo": row["version_no"],
        "state": row["state"],
        "evaluationReason": row["evaluation_reason"],
        "matchingPolicyVersion": row["matching_policy_version"],
        "inputFingerprint": row["input_fingerprint"],
    }
    if any(payload.get(key) != value for key, value in bindings.items()):
        raise RuntimeError("content match contract payload does not bind indexed columns")
    return ContentMatchVersion.model_validate(payload)
