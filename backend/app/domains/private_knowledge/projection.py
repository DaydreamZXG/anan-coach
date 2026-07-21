from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.private_knowledge.schemas import (
    PrivateCitation,
    PrivateSearchResult,
)
from app.domains.private_knowledge.weknora import (
    WeKnoraClient,
    WeKnoraError,
    WeKnoraNotFound,
    WeKnoraSettings,
)
from app.domains.private_knowledge.weknora_upstream import PROVIDER_RELEASE
from app.platform.jobs import BackgroundJobRepository


PROJECTION_INDEX_JOB_TYPE = "private_knowledge_weknora_index@v1"
PROJECTION_INPUT_VERSION = "private-knowledge-weknora-index@v1"


class PrivateKnowledgeProjectionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class PrivateKnowledgeProjectionRepository:
    def __init__(
        self, session: Session, *, jobs: BackgroundJobRepository | None = None
    ) -> None:
        self.session = session
        self.jobs = jobs or BackgroundJobRepository()

    def enqueue_source(
        self,
        *,
        owner_user_id: str,
        source_version_id: UUID,
        created_at: datetime,
    ) -> None:
        space = self._ensure_space(owner_user_id=owner_user_id, now=created_at)
        rows = list(
            self.session.execute(
                text(
                    """
                    select source.source_id, chunk.chunk_id, chunk.content_sha256
                    from learning.private_knowledge_source_versions source
                    join learning.private_knowledge_chunks chunk
                      on chunk.source_version_id = source.source_version_id
                     and chunk.owner_user_id = source.owner_user_id
                    where source.owner_user_id = cast(:owner as uuid)
                      and source.source_version_id = cast(:version_id as uuid)
                    order by chunk.ordinal, chunk.chunk_id
                    """
                ),
                {"owner": owner_user_id, "version_id": str(source_version_id)},
            ).mappings()
        )
        if not rows:
            raise PrivateKnowledgeProjectionError("projection_source_has_no_chunks")
        for row in rows:
            chunk_id = UUID(str(row["chunk_id"]))
            self.session.execute(
                text(
                    """
                    insert into learning.private_knowledge_chunk_projections (
                      projection_id, owner_user_id, space_id, source_id,
                      source_version_id, chunk_id, provider_release,
                      deterministic_title, content_sha256, state,
                      created_at, updated_at
                    ) values (
                      cast(:projection_id as uuid), cast(:owner as uuid),
                      cast(:space_id as uuid), cast(:source_id as uuid),
                      cast(:version_id as uuid), cast(:chunk_id as uuid),
                      :release, :title, :digest, 'queued', :now, :now
                    )
                    on conflict (owner_user_id, chunk_id) do nothing
                    """
                ),
                {
                    "projection_id": str(uuid4()),
                    "owner": owner_user_id,
                    "space_id": str(space["space_id"]),
                    "source_id": str(row["source_id"]),
                    "version_id": str(source_version_id),
                    "chunk_id": str(chunk_id),
                    "release": PROVIDER_RELEASE,
                    "title": f"anan:{chunk_id}",
                    "digest": str(row["content_sha256"]),
                    "now": created_at,
                },
            )
        self.jobs.enqueue(
            self.session,
            job_key=f"private-knowledge-weknora-index:{source_version_id}",
            job_type=PROJECTION_INDEX_JOB_TYPE,
            input_version=PROJECTION_INPUT_VERSION,
            payload={
                "ownerUserId": owner_user_id,
                "sourceVersionId": str(source_version_id),
            },
            max_attempts=24,
        )

    def _ensure_space(self, *, owner_user_id: str, now: datetime):
        row = (
            self.session.execute(
                text(
                    "select * from learning.private_knowledge_projection_spaces "
                    "where owner_user_id = cast(:owner as uuid)"
                ),
                {"owner": owner_user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            if row["state"] == "deleted":
                raise PrivateKnowledgeProjectionError("projection_space_deleted")
            return row
        space_id = uuid4()
        return (
            self.session.execute(
                text(
                    """
                    insert into learning.private_knowledge_projection_spaces (
                      space_id, owner_user_id, provider_release, deterministic_name,
                      state, revision, created_at, updated_at
                    ) values (
                      cast(:space_id as uuid), cast(:owner as uuid), :release,
                      :name, 'provisioning', 1, :now, :now
                    ) returning *
                    """
                ),
                {
                    "space_id": str(space_id),
                    "owner": owner_user_id,
                    "release": PROVIDER_RELEASE,
                    "name": f"anan-private-{space_id}",
                    "now": now,
                },
            )
            .mappings()
            .one()
        )

    def space(self, *, owner_user_id: str):
        row = (
            self.session.execute(
                text(
                    "select * from learning.private_knowledge_projection_spaces "
                    "where owner_user_id = cast(:owner as uuid) for update"
                ),
                {"owner": owner_user_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["state"] == "deleted":
            raise PrivateKnowledgeProjectionError("projection_space_missing")
        return row

    def attach_space(
        self, *, owner_user_id: str, remote_knowledge_base_id: str, now: datetime
    ) -> None:
        updated = self.session.execute(
            text(
                """
                update learning.private_knowledge_projection_spaces
                set remote_knowledge_base_id = :remote_id, state = 'ready',
                    revision = revision + 1, last_error_code = null, updated_at = :now
                where owner_user_id = cast(:owner as uuid)
                  and state in ('provisioning', 'failed', 'ready')
                """
            ),
            {"owner": owner_user_id, "remote_id": remote_knowledge_base_id, "now": now},
        ).rowcount
        if updated != 1:
            raise PrivateKnowledgeProjectionError("projection_space_attach_conflict")

    def source_projections(self, *, owner_user_id: str, source_version_id: UUID):
        return tuple(
            self.session.execute(
                text(
                    """
                    select projection.*, chunk.content
                    from learning.private_knowledge_chunk_projections projection
                    join learning.private_knowledge_chunks chunk
                      on chunk.chunk_id = projection.chunk_id
                     and chunk.owner_user_id = projection.owner_user_id
                    where projection.owner_user_id = cast(:owner as uuid)
                      and projection.source_version_id = cast(:version_id as uuid)
                    order by projection.deterministic_title
                    """
                ),
                {"owner": owner_user_id, "version_id": str(source_version_id)},
            ).mappings()
        )

    def attach_knowledge(
        self,
        *,
        owner_user_id: str,
        projection_id: UUID,
        remote_knowledge_id: str,
        now: datetime,
    ) -> None:
        updated = self.session.execute(
            text(
                """
                update learning.private_knowledge_chunk_projections
                set remote_knowledge_id = :remote_id, state = 'indexing',
                    last_error_code = null, updated_at = :now
                where projection_id = cast(:projection_id as uuid)
                  and owner_user_id = cast(:owner as uuid)
                  and state in ('queued', 'failed', 'indexing')
                """
            ),
            {
                "projection_id": str(projection_id),
                "owner": owner_user_id,
                "remote_id": remote_knowledge_id,
                "now": now,
            },
        ).rowcount
        if updated != 1:
            raise PrivateKnowledgeProjectionError("projection_attach_conflict")

    def mark_ready(
        self, *, owner_user_id: str, projection_id: UUID, now: datetime
    ) -> None:
        self.session.execute(
            text(
                """
                update learning.private_knowledge_chunk_projections
                set state = 'ready', ready_at = :now, deleted_at = null,
                    last_error_code = null, updated_at = :now
                where projection_id = cast(:projection_id as uuid)
                  and owner_user_id = cast(:owner as uuid)
                  and state in ('indexing', 'ready')
                """
            ),
            {"projection_id": str(projection_id), "owner": owner_user_id, "now": now},
        )

    def mark_source_failed(
        self,
        *,
        owner_user_id: str,
        source_version_id: UUID,
        code: str,
        now: datetime,
    ) -> None:
        self.session.execute(
            text(
                """
                update learning.private_knowledge_chunk_projections
                set state = 'failed', ready_at = null, deleted_at = null,
                    last_error_code = :code, updated_at = :now
                where owner_user_id = cast(:owner as uuid)
                  and source_version_id = cast(:version_id as uuid)
                  and state <> 'deleted'
                """
            ),
            {
                "owner": owner_user_id,
                "version_id": str(source_version_id),
                "code": code,
                "now": now,
            },
        )

    def predecessor_version(
        self, *, owner_user_id: str, source_version_id: UUID
    ) -> UUID | None:
        value = self.session.execute(
            text(
                """
                select supersedes_source_version_id
                from learning.private_knowledge_source_versions
                where owner_user_id = cast(:owner as uuid)
                  and source_version_id = cast(:version_id as uuid)
                """
            ),
            {"owner": owner_user_id, "version_id": str(source_version_id)},
        ).scalar_one_or_none()
        return UUID(str(value)) if value is not None else None

    def deletion_targets_for_version(
        self, *, owner_user_id: str, source_version_id: UUID
    ):
        return tuple(
            self.session.execute(
                text(
                    """
                    select * from learning.private_knowledge_chunk_projections
                    where owner_user_id = cast(:owner as uuid)
                      and source_version_id = cast(:version_id as uuid)
                      and state <> 'deleted'
                    order by projection_id
                    """
                ),
                {"owner": owner_user_id, "version_id": str(source_version_id)},
            ).mappings()
        )

    def deletion_targets_for_source(self, *, owner_user_id: str, source_id: UUID):
        return tuple(
            self.session.execute(
                text(
                    """
                    select * from learning.private_knowledge_chunk_projections
                    where owner_user_id = cast(:owner as uuid)
                      and source_id = cast(:source_id as uuid)
                      and state <> 'deleted'
                    order by projection_id
                    """
                ),
                {"owner": owner_user_id, "source_id": str(source_id)},
            ).mappings()
        )

    def deletion_targets_for_owner(self, *, owner_user_id: str):
        return tuple(
            self.session.execute(
                text(
                    """
                    select * from learning.private_knowledge_chunk_projections
                    where owner_user_id = cast(:owner as uuid)
                      and state <> 'deleted'
                    order by projection_id
                    """
                ),
                {"owner": owner_user_id},
            ).mappings()
        )

    def mark_deleted(
        self, *, owner_user_id: str, projection_id: UUID, now: datetime
    ) -> None:
        self.session.execute(
            text(
                """
                update learning.private_knowledge_chunk_projections
                set state = 'deleted', ready_at = null, deleted_at = :now,
                    last_error_code = null, updated_at = :now
                where projection_id = cast(:projection_id as uuid)
                  and owner_user_id = cast(:owner as uuid)
                """
            ),
            {"projection_id": str(projection_id), "owner": owner_user_id, "now": now},
        )

    def mark_space_deleted(self, *, owner_user_id: str, now: datetime) -> None:
        self.session.execute(
            text(
                """
                update learning.private_knowledge_projection_spaces
                set state = 'deleted', deleted_at = :now, updated_at = :now,
                    last_error_code = null, revision = revision + 1
                where owner_user_id = cast(:owner as uuid)
                """
            ),
            {"owner": owner_user_id, "now": now},
        )

    def current_search_scope(self, *, owner_user_id: str):
        current_count = int(
            self.session.execute(
                text(
                    """
                    select count(*)
                    from learning.private_knowledge_chunks chunk
                    join learning.private_knowledge_source_versions source
                      on source.source_version_id = chunk.source_version_id
                     and source.owner_user_id = chunk.owner_user_id
                    where chunk.owner_user_id = cast(:owner as uuid)
                      and not exists (
                        select 1 from learning.private_knowledge_source_versions successor
                        where successor.supersedes_source_version_id = source.source_version_id
                      )
                      and not exists (
                        select 1 from privacy.private_material_deletion_requests request
                        where request.owner_user_id = source.owner_user_id
                          and request.source_id = source.source_id
                          and request.state in ('queued', 'running')
                      )
                    """
                ),
                {"owner": owner_user_id},
            ).scalar_one()
        )
        rows = tuple(
            self.session.execute(
                text(
                    """
                    select space.remote_knowledge_base_id, projection.remote_knowledge_id,
                           projection.chunk_id
                    from learning.private_knowledge_chunk_projections projection
                    join learning.private_knowledge_projection_spaces space
                      on space.space_id = projection.space_id
                     and space.owner_user_id = projection.owner_user_id
                    join learning.private_knowledge_source_versions source
                      on source.source_version_id = projection.source_version_id
                     and source.owner_user_id = projection.owner_user_id
                    where projection.owner_user_id = cast(:owner as uuid)
                      and projection.state = 'ready'
                      and space.state = 'ready'
                      and not exists (
                        select 1 from learning.private_knowledge_source_versions successor
                        where successor.supersedes_source_version_id = source.source_version_id
                      )
                      and not exists (
                        select 1 from privacy.private_material_deletion_requests request
                        where request.owner_user_id = source.owner_user_id
                          and request.source_id = source.source_id
                          and request.state in ('queued', 'running')
                      )
                    order by projection.remote_knowledge_id
                    """
                ),
                {"owner": owner_user_id},
            ).mappings()
        )
        return current_count, rows

    def current_failure_code(self, *, owner_user_id: str) -> str | None:
        value = self.session.execute(
            text(
                """
                select projection.last_error_code
                from learning.private_knowledge_chunk_projections projection
                join learning.private_knowledge_source_versions source
                  on source.source_version_id = projection.source_version_id
                 and source.owner_user_id = projection.owner_user_id
                where projection.owner_user_id = cast(:owner as uuid)
                  and projection.state = 'failed'
                  and not exists (
                    select 1 from learning.private_knowledge_source_versions successor
                    where successor.supersedes_source_version_id = source.source_version_id
                  )
                order by projection.updated_at desc
                limit 1
                """
            ),
            {"owner": owner_user_id},
        ).scalar_one_or_none()
        return str(value) if value is not None else None

    def citations_for_chunks(
        self,
        *,
        owner_user_id: str,
        ranked_chunks: tuple[tuple[UUID, float], ...],
        maximum_sources: int,
    ) -> tuple[PrivateCitation, ...]:
        if not ranked_chunks:
            return ()
        chunk_ids = [str(item[0]) for item in ranked_chunks]
        rows = self.session.execute(
            text(
                """
                select chunk.chunk_id, chunk.source_version_id, chunk.anchor,
                       chunk.content, page.page_no, source.source_id, source.title
                from learning.private_knowledge_chunks chunk
                join learning.private_knowledge_page_versions page
                  on page.page_version_id = chunk.page_version_id
                 and page.owner_user_id = chunk.owner_user_id
                join learning.private_knowledge_source_versions source
                  on source.source_version_id = chunk.source_version_id
                 and source.owner_user_id = chunk.owner_user_id
                where chunk.owner_user_id = cast(:owner as uuid)
                  and chunk.chunk_id = any(cast(:chunk_ids as uuid[]))
                  and not exists (
                    select 1 from learning.private_knowledge_source_versions successor
                    where successor.supersedes_source_version_id = source.source_version_id
                  )
                  and not exists (
                    select 1 from privacy.private_material_deletion_requests request
                    where request.owner_user_id = source.owner_user_id
                      and request.source_id = source.source_id
                      and request.state in ('queued', 'running')
                  )
                """
            ),
            {"owner": owner_user_id, "chunk_ids": chunk_ids},
        ).mappings()
        by_chunk = {UUID(str(row["chunk_id"])): row for row in rows}
        if any(chunk_id not in by_chunk for chunk_id, _score in ranked_chunks):
            raise PrivateKnowledgeProjectionError("projection_mapping_rejected")
        citations: list[PrivateCitation] = []
        for chunk_id, score in ranked_chunks[:maximum_sources]:
            row = by_chunk[chunk_id]
            normalized_score = round(max(0.0, min(1.0, score)), 6)
            citations.append(
                PrivateCitation(
                    sourceId=row["source_id"],
                    sourceVersionId=row["source_version_id"],
                    chunkId=row["chunk_id"],
                    pageNo=row["page_no"],
                    anchor=row["anchor"],
                    title=str(row["title"]),
                    excerpt=" ".join(str(row["content"]).split())[:2000],
                    lexicalScore=0.0,
                    vectorScore=normalized_score,
                    finalScore=normalized_score,
                )
            )
        return tuple(citations)

    def account_deletion_owners(self, *, limit: int) -> tuple[str, ...]:
        return tuple(
            str(value)
            for value in self.session.execute(
                text(
                    """
                    select distinct space.owner_user_id
                    from learning.private_knowledge_projection_spaces space
                    join privacy.account_deletion_requests request
                      on request.user_id = space.owner_user_id
                    where space.state <> 'deleted'
                      and request.state in ('queued', 'running')
                    order by space.owner_user_id
                    limit :limit
                    """
                ),
                {"limit": limit},
            ).scalars()
        )


class PrivateKnowledgeProjectionGateway:
    def __init__(
        self,
        session: Session,
        *,
        client: WeKnoraClient,
        settings: WeKnoraSettings,
        jobs: BackgroundJobRepository | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.settings = settings
        self.repository = PrivateKnowledgeProjectionRepository(session, jobs=jobs)

    def enqueue_source(self, *, owner_user_id: str, source_version_id: UUID) -> None:
        self.repository.enqueue_source(
            owner_user_id=owner_user_id,
            source_version_id=source_version_id,
            created_at=datetime.now(UTC),
        )

    def index_source(self, *, owner_user_id: str, source_version_id: UUID) -> dict[str, Any]:
        now = datetime.now(UTC)
        space = self.repository.space(owner_user_id=owner_user_id)
        remote_kb_id = space["remote_knowledge_base_id"]
        if not remote_kb_id:
            matches = [
                item
                for item in self.client.list_knowledge_bases()
                if item["name"] == str(space["deterministic_name"])
            ]
            if len(matches) > 1:
                raise PrivateKnowledgeProjectionError("projection_space_ambiguous")
            remote_kb_id = (
                matches[0]["id"]
                if matches
                else self.client.create_knowledge_base(
                    name=str(space["deterministic_name"])
                )
            )
            self.repository.attach_space(
                owner_user_id=owner_user_id,
                remote_knowledge_base_id=remote_kb_id,
                now=now,
            )
            self.session.commit()

        projections = self.repository.source_projections(
            owner_user_id=owner_user_id, source_version_id=source_version_id
        )
        if not projections:
            raise PrivateKnowledgeProjectionError("projection_source_missing")
        remote_knowledge = self.client.list_knowledge(str(remote_kb_id))
        remote_by_title = {item.title: item for item in remote_knowledge}
        if len(remote_by_title) != len(remote_knowledge):
            raise PrivateKnowledgeProjectionError("projection_remote_title_ambiguous")
        for row in projections:
            if row["remote_knowledge_id"]:
                continue
            title = str(row["deterministic_title"])
            existing = remote_by_title.get(title)
            knowledge = existing or self.client.create_manual_knowledge(
                knowledge_base_id=str(remote_kb_id),
                title=title,
                content=str(row["content"]),
            )
            self.repository.attach_knowledge(
                owner_user_id=owner_user_id,
                projection_id=UUID(str(row["projection_id"])),
                remote_knowledge_id=knowledge.knowledge_id,
                now=datetime.now(UTC),
            )
            self.session.commit()

        pending = 0
        for row in self.repository.source_projections(
            owner_user_id=owner_user_id, source_version_id=source_version_id
        ):
            if row["state"] == "ready":
                continue
            knowledge = self.client.get_knowledge(str(row["remote_knowledge_id"]))
            if knowledge.title != str(row["deterministic_title"]):
                raise PrivateKnowledgeProjectionError("projection_remote_identity_changed")
            if knowledge.parse_status == "completed":
                self.repository.mark_ready(
                    owner_user_id=owner_user_id,
                    projection_id=UUID(str(row["projection_id"])),
                    now=datetime.now(UTC),
                )
            elif knowledge.parse_status in {"failed", "cancelled"}:
                raise PrivateKnowledgeProjectionError("projection_parse_failed")
            else:
                pending += 1
        self.session.commit()
        if pending:
            raise PrivateKnowledgeProjectionError(
                "projection_parse_pending", retryable=True
            )

        predecessor = self.repository.predecessor_version(
            owner_user_id=owner_user_id, source_version_id=source_version_id
        )
        if predecessor is not None:
            self.delete_version(
                owner_user_id=owner_user_id, source_version_id=predecessor
            )
        return {
            "sourceVersionId": str(source_version_id),
            "projectionCount": len(projections),
            "providerRelease": PROVIDER_RELEASE,
        }

    def search(
        self, *, owner_user_id: str, query: str, maximum_sources: int
    ) -> PrivateSearchResult:
        current_count, rows = self.repository.current_search_scope(
            owner_user_id=owner_user_id
        )
        fingerprint = sha256(query.encode()).hexdigest()
        if current_count == 0:
            return PrivateSearchResult(queryFingerprint=fingerprint, citations=())
        if len(rows) != current_count:
            failure_code = self.repository.current_failure_code(
                owner_user_id=owner_user_id
            )
            if failure_code is not None:
                raise PrivateKnowledgeProjectionError(
                    f"projection_failed:{failure_code}"
                )
            raise PrivateKnowledgeProjectionError("projection_not_ready", retryable=True)
        knowledge_base_ids = {str(row["remote_knowledge_base_id"]) for row in rows}
        if len(knowledge_base_ids) != 1:
            raise PrivateKnowledgeProjectionError("projection_space_mismatch")
        remote_to_chunk = {
            str(row["remote_knowledge_id"]): UUID(str(row["chunk_id"])) for row in rows
        }
        remote_ids = tuple(remote_to_chunk)
        ranked: dict[str, float] = {}
        for offset in range(0, len(remote_ids), self.settings.search_batch_size):
            batch = remote_ids[offset : offset + self.settings.search_batch_size]
            candidates = self.client.hybrid_search(
                knowledge_base_id=next(iter(knowledge_base_ids)),
                query=query,
                knowledge_ids=batch,
                match_count=min(len(batch), max(maximum_sources * 4, 12)),
            )
            for candidate in candidates:
                if candidate.knowledge_id not in remote_to_chunk:
                    raise PrivateKnowledgeProjectionError("projection_mapping_rejected")
                ranked[candidate.knowledge_id] = max(
                    ranked.get(candidate.knowledge_id, float("-inf")), candidate.score
                )
        ordered = tuple(
            (remote_to_chunk[remote_id], score)
            for remote_id, score in sorted(
                ranked.items(), key=lambda item: (-item[1], item[0])
            )
        )
        return PrivateSearchResult(
            queryFingerprint=fingerprint,
            citations=self.repository.citations_for_chunks(
                owner_user_id=owner_user_id,
                ranked_chunks=ordered,
                maximum_sources=maximum_sources,
            ),
        )

    def delete_version(self, *, owner_user_id: str, source_version_id: UUID) -> int:
        targets = self.repository.deletion_targets_for_version(
            owner_user_id=owner_user_id, source_version_id=source_version_id
        )
        self._delete_targets(owner_user_id=owner_user_id, targets=targets)
        return len(targets)

    def delete_source(self, *, owner_user_id: str, source_id: UUID) -> int:
        targets = self.repository.deletion_targets_for_source(
            owner_user_id=owner_user_id, source_id=source_id
        )
        self._delete_targets(owner_user_id=owner_user_id, targets=targets)
        return len(targets)

    def delete_account(self, *, owner_user_id: str) -> int:
        targets = self.repository.deletion_targets_for_owner(
            owner_user_id=owner_user_id
        )
        for row in targets:
            self._ensure_remote_knowledge_deleted(row)
        space = self.repository.space(owner_user_id=owner_user_id)
        remote_kb_id = str(space["remote_knowledge_base_id"] or "")
        if remote_kb_id:
            try:
                self.client.get_knowledge_base(remote_kb_id)
            except WeKnoraNotFound:
                pass
            else:
                self.client.delete_knowledge_base(remote_kb_id)
                try:
                    self.client.get_knowledge_base(remote_kb_id)
                except WeKnoraNotFound:
                    pass
                else:
                    raise PrivateKnowledgeProjectionError(
                        "projection_space_deletion_pending", retryable=True
                    )
        now = datetime.now(UTC)
        for row in targets:
            self.repository.mark_deleted(
                owner_user_id=owner_user_id,
                projection_id=UUID(str(row["projection_id"])),
                now=now,
            )
        self.repository.mark_space_deleted(
            owner_user_id=owner_user_id, now=now
        )
        self.session.commit()
        return len(targets)

    def cleanup_account_deletions(self, *, limit: int = 10) -> int:
        completed = 0
        for owner in self.repository.account_deletion_owners(limit=limit):
            self.delete_account(owner_user_id=owner)
            completed += 1
        return completed

    def _delete_targets(self, *, owner_user_id: str, targets: tuple[Any, ...]) -> None:
        for row in targets:
            self._ensure_remote_knowledge_deleted(row)
            self.repository.mark_deleted(
                owner_user_id=owner_user_id,
                projection_id=UUID(str(row["projection_id"])),
                now=datetime.now(UTC),
            )
            self.session.commit()

    def _ensure_remote_knowledge_deleted(self, row: Any) -> None:
        remote_id = str(row["remote_knowledge_id"] or "")
        if not remote_id:
            return
        try:
            self.client.get_knowledge(remote_id)
        except WeKnoraNotFound:
            return
        self.client.delete_knowledge(remote_id)
        try:
            self.client.get_knowledge(remote_id)
        except WeKnoraNotFound:
            return
        raise PrivateKnowledgeProjectionError(
            "projection_deletion_pending", retryable=True
        )


def build_projection_gateway(
    session: Session, *, jobs: BackgroundJobRepository | None = None
) -> PrivateKnowledgeProjectionGateway | None:
    settings = WeKnoraSettings.from_environment()
    try:
        settings.validate()
    except WeKnoraError as exc:
        raise projection_error_from_provider(exc) from exc
    if settings.mode == "canonical":
        return None
    return PrivateKnowledgeProjectionGateway(
        session,
        client=WeKnoraClient(settings),
        settings=settings,
        jobs=jobs,
    )


def projection_error_from_provider(exc: WeKnoraError) -> PrivateKnowledgeProjectionError:
    return PrivateKnowledgeProjectionError(exc.code, retryable=exc.retryable or exc.uncertain)
