from __future__ import annotations

from datetime import datetime
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import AnswerIngestionVersion


class AnswerIngestionRepository:
    def lock(self, session: Session, *, key: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    def current(
        self, session: Session, *, user_id: str, ingestion_id: UUID, for_update: bool = False
    ) -> AnswerIngestionVersion | None:
        suffix = " for update" if for_update else ""
        row = session.execute(
            text(
                "select contract_payload from learning.answer_ingestion_versions "
                "where ingestion_id = :ingestion_id "
                "and owner_user_id = cast(:user_id as uuid) "
                "order by version_no desc limit 1" + suffix
            ),
            {"ingestion_id": str(ingestion_id), "user_id": user_id},
        ).mappings().one_or_none()
        return AnswerIngestionVersion.model_validate(row["contract_payload"]) if row else None

    def insert(self, session: Session, *, ingestion: AnswerIngestionVersion) -> None:
        payload = ingestion.model_dump(by_alias=True, mode="json")
        session.execute(
            text(
                """
                insert into learning.answer_ingestion_versions (
                  ingestion_version_id, ingestion_id, owner_user_id, version_no,
                  packet_version_id, source_artifact_version_ids, track, input_mode,
                  state, candidate_payload, target_writer, writer_contract_version,
                  source_lineage_refs, confirmed_at, committed_fact_ref,
                  contract_payload, created_at
                ) values (
                  :ingestion_version_id, :ingestion_id, :owner_user_id, :version_no,
                  :packet_version_id, cast(:source_artifact_version_ids as jsonb),
                  :track, :input_mode, :state, cast(:candidate_payload as jsonb),
                  :target_writer, :writer_contract_version,
                  cast(:source_lineage_refs as jsonb), :confirmed_at,
                  :committed_fact_ref, cast(:contract_payload as jsonb), :created_at
                )
                """
            ),
            {
                "ingestion_version_id": str(ingestion.ingestion_version_id),
                "ingestion_id": str(ingestion.ingestion_id),
                "owner_user_id": str(ingestion.owner_user_id),
                "version_no": ingestion.version_no,
                "packet_version_id": str(ingestion.packet_version_id) if ingestion.packet_version_id else None,
                "source_artifact_version_ids": json.dumps(payload["sourceArtifactVersionIds"]),
                "track": ingestion.track.value,
                "input_mode": ingestion.input_mode,
                "state": ingestion.state,
                "candidate_payload": json.dumps(payload["candidatePayload"], ensure_ascii=False, sort_keys=True),
                "target_writer": ingestion.target_writer,
                "writer_contract_version": ingestion.writer_contract_version,
                "source_lineage_refs": json.dumps(payload["sourceLineageRefs"], ensure_ascii=False, sort_keys=True),
                "confirmed_at": ingestion.confirmed_at,
                "committed_fact_ref": ingestion.committed_fact_ref,
                "contract_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "created_at": ingestion.created_at,
            },
        )

    def packet(self, session: Session, *, user_id: str, packet_version_id: UUID) -> RowMapping | None:
        return session.execute(
            text(
                """
                select packet_id, contract_payload
                from learning.offline_packet_versions
                where packet_version_id = :packet_version_id
                  and owner_user_id = cast(:user_id as uuid)
                """
            ),
            {"packet_version_id": str(packet_version_id), "user_id": user_id},
        ).mappings().one_or_none()

    def packet_bindings(
        self, session: Session, *, packet_id: UUID, user_id: str
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from learning.packet_answer_bindings
                    where packet_id = :packet_id
                      and owner_user_id = cast(:user_id as uuid)
                    order by order_no
                    """
                ),
                {"packet_id": str(packet_id), "user_id": user_id},
            ).mappings()
        )

    def current_ready_packets(
        self, session: Session, *, user_id: str, at: datetime, limit: int = 50
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select packet_id, packet_version_id, version_no,
                           contract_payload, expires_at
                    from (
                      select distinct on (packet_id)
                             packet_id, packet_version_id, version_no, state,
                             contract_payload, expires_at, created_at
                      from learning.offline_packet_versions
                      where owner_user_id = cast(:user_id as uuid)
                      order by packet_id, version_no desc
                    ) current
                    where state = 'ready' and expires_at > :at
                    order by created_at desc
                    limit :limit
                    """
                ),
                {"user_id": user_id, "at": at, "limit": limit},
            ).mappings()
        )

    def artifacts_available(
        self, session: Session, *, user_id: str, artifact_ids: tuple[UUID, ...]
    ) -> bool:
        if not artifact_ids:
            return True
        count = session.execute(
            text(
                """
                select count(*) from learning.source_artifact_versions
                where owner_user_id = cast(:user_id as uuid)
                  and artifact_version_id = any(cast(:ids as uuid[]))
                  and state = 'available'
                """
            ),
            {"user_id": user_id, "ids": [str(item) for item in artifact_ids]},
        ).scalar_one()
        return int(count) == len(set(artifact_ids))

    def recognitions_confirmed(
        self,
        session: Session,
        *,
        user_id: str,
        recognition_ids: tuple[UUID, ...],
        artifact_ids: tuple[UUID, ...],
    ) -> bool:
        if not recognition_ids:
            return True
        count = session.execute(
            text(
                """
                select count(*) from learning.recognition_revisions
                where owner_user_id = cast(:user_id as uuid)
                  and recognition_revision_id = any(cast(:recognition_ids as uuid[]))
                  and artifact_version_id = any(cast(:artifact_ids as uuid[]))
                  and state = 'confirmed'
                """
            ),
            {
                "user_id": user_id,
                "recognition_ids": [str(item) for item in recognition_ids],
                "artifact_ids": [str(item) for item in artifact_ids],
            },
        ).scalar_one()
        return int(count) == len(set(recognition_ids))

    def recognitions_admissible(
        self,
        session: Session,
        *,
        user_id: str,
        recognition_ids: tuple[UUID, ...],
        artifact_ids: tuple[UUID, ...],
    ) -> bool:
        if not recognition_ids:
            return False
        count = session.execute(
            text(
                """
                select count(*)
                from learning.recognition_revisions revision
                where revision.owner_user_id = cast(:user_id as uuid)
                  and revision.recognition_revision_id = any(cast(:recognition_ids as uuid[]))
                  and revision.artifact_version_id = any(cast(:artifact_ids as uuid[]))
                  and revision.state in ('candidate', 'confirmed')
                  and revision.expires_at > now()
                  and not exists (
                    select 1 from learning.recognition_revisions successor
                    where successor.supersedes_recognition_revision_id =
                          revision.recognition_revision_id
                  )
                """
            ),
            {
                "user_id": user_id,
                "recognition_ids": [str(item) for item in recognition_ids],
                "artifact_ids": [str(item) for item in artifact_ids],
            },
        ).scalar_one()
        return int(count) == len(set(recognition_ids))

    def confirmed_recognition_lineage(
        self,
        session: Session,
        *,
        user_id: str,
        recognition_ids: tuple[UUID, ...],
        artifact_ids: tuple[UUID, ...],
    ) -> tuple[UUID, ...] | None:
        if not recognition_ids:
            return ()
        rows = session.execute(
            text(
                """
                with recursive lineage as (
                  select recognition_revision_id as origin_id,
                         recognition_revision_id, artifact_version_id,
                         owner_user_id, state, expires_at
                  from learning.recognition_revisions
                  where recognition_revision_id = any(cast(:recognition_ids as uuid[]))
                    and owner_user_id = cast(:user_id as uuid)
                    and artifact_version_id = any(cast(:artifact_ids as uuid[]))
                  union all
                  select lineage.origin_id, successor.recognition_revision_id,
                         successor.artifact_version_id, successor.owner_user_id,
                         successor.state, successor.expires_at
                  from lineage
                  join learning.recognition_revisions successor
                    on successor.supersedes_recognition_revision_id =
                       lineage.recognition_revision_id
                )
                select lineage.origin_id, lineage.recognition_revision_id
                from lineage
                where lineage.state = 'confirmed'
                  and lineage.expires_at > now()
                  and not exists (
                    select 1 from learning.recognition_revisions successor
                    where successor.supersedes_recognition_revision_id =
                          lineage.recognition_revision_id
                  )
                order by lineage.origin_id
                """
            ),
            {
                "user_id": user_id,
                "recognition_ids": [str(item) for item in recognition_ids],
                "artifact_ids": [str(item) for item in artifact_ids],
            },
        ).mappings()
        mapping = {
            UUID(str(row["origin_id"])): UUID(str(row["recognition_revision_id"]))
            for row in rows
        }
        if set(mapping) != set(recognition_ids):
            return None
        return tuple(mapping[item] for item in recognition_ids)

    def insert_fact(
        self,
        session: Session,
        *,
        fact_id: UUID,
        fact_batch_id: UUID,
        ingestion_version_id: UUID,
        owner_user_id: UUID,
        packet_version_id: UUID,
        packet_item_id: UUID,
        content_version_id: str,
        track: str,
        answer_payload: dict[str, Any],
        source_lineage_refs: tuple[str, ...],
        target_writer: str,
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.external_answer_facts (
                  fact_id, fact_batch_id, ingestion_version_id, owner_user_id,
                  packet_version_id, packet_item_id, content_version_id, track,
                  answer_payload, source_lineage_refs, target_writer,
                  evidence_policy_version, review_state, created_at
                ) values (
                  :fact_id, :fact_batch_id, :ingestion_version_id, :owner_user_id,
                  :packet_version_id, :packet_item_id, :content_version_id, :track,
                  cast(:answer_payload as jsonb), cast(:source_lineage_refs as jsonb),
                  :target_writer, 'external-confirmed-lower-weight-v1',
                  'user_confirmed', :created_at
                )
                """
            ),
            {
                "fact_id": str(fact_id),
                "fact_batch_id": str(fact_batch_id),
                "ingestion_version_id": str(ingestion_version_id),
                "owner_user_id": str(owner_user_id),
                "packet_version_id": str(packet_version_id),
                "packet_item_id": str(packet_item_id),
                "content_version_id": content_version_id,
                "track": track,
                "answer_payload": json.dumps(answer_payload, ensure_ascii=False, sort_keys=True),
                "source_lineage_refs": json.dumps(list(source_lineage_refs), ensure_ascii=False, sort_keys=True),
                "target_writer": target_writer,
                "created_at": created_at,
            },
        )
