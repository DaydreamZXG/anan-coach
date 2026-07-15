from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import OfflinePacketVersion


@dataclass(frozen=True)
class StoredPacketCommand:
    request_hash: str
    response_payload: dict[str, Any]


class LearningPackageRepository:
    def lock(self, session: Session, *, key: str) -> None:
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def command_by_key(
        self, session: Session, *, user_id: str, command_type: str, idempotency_key: str
    ) -> StoredPacketCommand | None:
        row = session.execute(
            text(
                """
                select request_hash, response_payload
                from learning.external_learning_commands
                where owner_user_id = cast(:user_id as uuid)
                  and command_type = :command_type
                  and idempotency_key = :idempotency_key
                """
            ),
            {"user_id": user_id, "command_type": command_type, "idempotency_key": idempotency_key},
        ).mappings().one_or_none()
        return (
            StoredPacketCommand(str(row["request_hash"]), dict(row["response_payload"]))
            if row is not None
            else None
        )

    def insert_command(
        self,
        session: Session,
        *,
        command_id: UUID,
        user_id: str,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: dict[str, Any],
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.external_learning_commands (
                  command_id, owner_user_id, command_type, idempotency_key,
                  request_hash, response_payload, created_at
                ) values (
                  :command_id, cast(:user_id as uuid), :command_type, :idempotency_key,
                  :request_hash, cast(:response_payload as jsonb), :created_at
                )
                """
            ),
            {
                "command_id": str(command_id),
                "user_id": user_id,
                "command_type": command_type,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "response_payload": json.dumps(response_payload, ensure_ascii=False, sort_keys=True),
                "created_at": created_at,
            },
        )

    def question_snapshot(
        self, session: Session, *, question_id: str
    ) -> dict[str, Any] | None:
        row = session.execute(
            text(
                """
                select question.question_id, question.module_name, question.question_type,
                       question.stem, question.stem_html, question.answer,
                       question.material_text, question.material_html,
                       question.shared_context_required, question.visual_required,
                       explanation.preferred_analysis, explanation.analysis_source,
                       explanation.analysis_status, explanation.prefer_ai_analysis,
                       explanation.analysis_needs_review
                from product.questions question
                left join product.question_explanations explanation
                  on explanation.question_id = question.question_id
                where question.question_id = :question_id
                  and question.status in ('active', 'canonical')
                """
            ),
            {"question_id": question_id},
        ).mappings().one_or_none()
        if row is None:
            return None
        result = dict(row)
        result["options"] = [
            dict(item)
            for item in session.execute(
                text(
                    """
                    select label, option_text, option_html
                    from product.question_options
                    where question_id = :question_id order by option_index
                    """
                ),
                {"question_id": question_id},
            ).mappings()
        ]
        result["assets"] = [
            dict(item)
            for item in session.execute(
                text(
                    """
                    select asset_id, part_kind, part_index, image_ordinal,
                           image_src, object_key, is_representative_asset
                    from product.question_assets
                    where question_id = :question_id
                    order by part_kind nulls last, part_index nulls last,
                             image_ordinal nulls last, asset_id
                    """
                ),
                {"question_id": question_id},
            ).mappings()
        ]
        return result

    def insert_packet(self, session: Session, *, packet: OfflinePacketVersion) -> None:
        payload = packet.model_dump(by_alias=True, mode="json")
        session.execute(
            text(
                """
                insert into learning.offline_packet_versions (
                  packet_version_id, packet_id, owner_user_id, version_no, state,
                  render_format, manifest_sha256, items, license_snapshot_refs,
                  artifact_ref, expires_at, contract_payload, created_at
                ) values (
                  :packet_version_id, :packet_id, :owner_user_id, :version_no, :state,
                  :render_format, :manifest_sha256, cast(:items as jsonb),
                  cast(:license_snapshot_refs as jsonb), :artifact_ref, :expires_at,
                  cast(:contract_payload as jsonb), :created_at
                )
                """
            ),
            {
                "packet_version_id": str(packet.packet_version_id),
                "packet_id": str(packet.packet_id),
                "owner_user_id": str(packet.owner_user_id),
                "version_no": packet.version_no,
                "state": packet.state,
                "render_format": packet.render_format,
                "manifest_sha256": packet.manifest_sha256,
                "items": json.dumps(payload["items"], ensure_ascii=False, sort_keys=True),
                "license_snapshot_refs": json.dumps(payload["licenseSnapshotRefs"], ensure_ascii=False, sort_keys=True),
                "artifact_ref": packet.artifact_ref,
                "expires_at": packet.expires_at,
                "contract_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "created_at": packet.created_at,
            },
        )

    def insert_binding(
        self,
        session: Session,
        *,
        binding_digest: str,
        packet_id: UUID,
        packet_version_id: UUID,
        owner_user_id: UUID,
        packet_item_id: UUID,
        content_version_id: str,
        track: str,
        order_no: int,
        created_at: datetime,
    ) -> None:
        session.execute(
            text(
                """
                insert into learning.packet_answer_bindings (
                  binding_digest, packet_id, packet_version_id, owner_user_id, packet_item_id,
                  content_version_id, track, order_no, created_at
                ) values (
                  :binding_digest, :packet_id, :packet_version_id, :owner_user_id, :packet_item_id,
                  :content_version_id, :track, :order_no, :created_at
                )
                """
            ),
            {
                "binding_digest": binding_digest,
                "packet_id": str(packet_id),
                "packet_version_id": str(packet_version_id),
                "owner_user_id": str(owner_user_id),
                "packet_item_id": str(packet_item_id),
                "content_version_id": content_version_id,
                "track": track,
                "order_no": order_no,
                "created_at": created_at,
            },
        )

    def packet(
        self,
        session: Session,
        *,
        user_id: str,
        packet_version_id: UUID,
    ) -> OfflinePacketVersion | None:
        row = session.execute(
            text(
                """
                select contract_payload from learning.offline_packet_versions
                where packet_version_id = :packet_version_id
                  and owner_user_id = cast(:user_id as uuid)
                """
            ),
            {"packet_version_id": str(packet_version_id), "user_id": user_id},
        ).mappings().one_or_none()
        return OfflinePacketVersion.model_validate(row["contract_payload"]) if row else None

    def packet_by_version_id(
        self, session: Session, *, packet_version_id: UUID
    ) -> OfflinePacketVersion | None:
        row = session.execute(
            text("select contract_payload from learning.offline_packet_versions where packet_version_id = :id"),
            {"id": str(packet_version_id)},
        ).mappings().one_or_none()
        return OfflinePacketVersion.model_validate(row["contract_payload"]) if row else None

    def current_packet(
        self, session: Session, *, packet_id: UUID, for_update: bool = False
    ) -> OfflinePacketVersion | None:
        suffix = " for update" if for_update else ""
        row = session.execute(
            text(
                "select contract_payload from learning.offline_packet_versions "
                "where packet_id = :packet_id order by version_no desc limit 1" + suffix
            ),
            {"packet_id": str(packet_id)},
        ).mappings().one_or_none()
        return OfflinePacketVersion.model_validate(row["contract_payload"]) if row else None

    def list_packets(
        self, session: Session, *, user_id: str, limit: int = 50
    ) -> tuple[OfflinePacketVersion, ...]:
        rows = session.execute(
            text(
                """
                select distinct on (packet_id) contract_payload
                from learning.offline_packet_versions
                where owner_user_id = cast(:user_id as uuid)
                order by packet_id, version_no desc
                limit :limit
                """
            ),
            {"user_id": user_id, "limit": limit},
        ).mappings()
        return tuple(OfflinePacketVersion.model_validate(row["contract_payload"]) for row in rows)

    def render_job_payload(
        self, session: Session, *, packet_id: UUID
    ) -> dict[str, Any] | None:
        payload = session.execute(
            text(
                """
                select job.payload
                from learning.offline_packet_versions packet
                join platform.background_jobs job
                  on job.payload->>'packetVersionId' = packet.packet_version_id::text
                 and job.job_type = 'offline_packet_render_v1'
                where packet.packet_id = :packet_id
                order by packet.version_no desc, job.created_at desc
                limit 1
                """
            ),
            {"packet_id": str(packet_id)},
        ).scalar_one_or_none()
        return dict(payload) if payload is not None else None

    def renewal_requests(
        self, session: Session, *, snapshot_refs: tuple[str, ...]
    ) -> tuple[dict[str, Any], ...]:
        if not snapshot_refs:
            return ()
        rows = session.execute(
            text(
                """
                select snapshot.request_payload, head.authorization_epoch
                from product.content_license_authorization_snapshots snapshot
                join product.content_license_heads head
                  on head.content_license_id = snapshot.content_license_id
                 and head.content_version_id = snapshot.content_version_id
                where snapshot.snapshot_id = any(cast(:snapshot_refs as uuid[]))
                  and head.state = 'active'
                order by snapshot.snapshot_id
                """
            ),
            {"snapshot_refs": list(snapshot_refs)},
        ).mappings()
        result: list[dict[str, Any]] = []
        for row in rows:
            request = dict(row["request_payload"])
            request["expectedAuthorizationEpoch"] = int(row["authorization_epoch"])
            result.append(request)
        return tuple(result)

    def eligible_packet_content(
        self, session: Session, *, user_id: str, limit: int = 100
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    with current_matches as (
                      select distinct on (content_match_id)
                             content_match_id, confirmed_content_version_id, created_at
                      from learning.content_match_versions
                      where owner_user_id = cast(:user_id as uuid)
                      order by content_match_id, version_no desc
                    )
                    select version.question_id, version.content_version_id,
                           version.track, version.snapshot_payload,
                           license.content_license_id,
                           license.authorization_epoch,
                           license_version.record_payload,
                           match.created_at as confirmed_at
                    from current_matches match
                    join product.question_content_versions version
                      on version.content_version_id = match.confirmed_content_version_id
                    join product.question_content_heads content_head
                      on content_head.question_id = version.question_id
                     and content_head.content_version_id = version.content_version_id
                    join product.content_license_heads license
                      on license.content_version_id = version.content_version_id
                     and license.state = 'active'
                    join product.content_license_record_versions license_version
                      on license_version.record_version_id = license.record_version_id
                    where match.confirmed_content_version_id is not null
                    order by match.created_at desc, version.question_id
                    limit :limit
                    """
                ),
                {"user_id": user_id, "limit": limit},
            ).mappings()
        )

    def bindings(
        self, session: Session, *, packet_version_id: UUID, owner_user_id: UUID
    ) -> list[RowMapping]:
        return list(
            session.execute(
                text(
                    """
                    select * from learning.packet_answer_bindings
                    where packet_version_id = :packet_version_id
                      and owner_user_id = :owner_user_id
                    order by order_no
                    """
                ),
                {"packet_version_id": str(packet_version_id), "owner_user_id": str(owner_user_id)},
            ).mappings()
        )
