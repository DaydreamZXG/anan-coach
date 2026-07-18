from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import secrets
from typing import Callable, Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    Audience,
    ContentComponent,
    ContentLicenseRecord,
    ContentTransform,
    ContentUse,
    DeliveryChannel,
    LicenseDecision,
    LicenseSnapshotStatus,
    LicenseUseRequest,
    OfflinePacketItem,
    OfflinePacketVersion,
    Territory,
)
from app.domains.learning_packages.repository import LearningPackageRepository
from app.domains.learning_packages.binding import answer_binding_digest
from app.domains.learning_packages.schemas import (
    CreatePacketCommand,
    PacketAccessProjection,
    PacketContentCandidate,
    PacketListSnapshot,
    PacketRenderContent,
    PacketRenderRequest,
    RenewPacketCommand,
    RetryPacketCommand,
)
from app.domains.xingce_learning.content_version import question_content_version_id
from app.platform.jobs import BackgroundJobRepository
from app.platform.outbox import OutboxRepository


class LicenseEvaluator(Protocol):
    def evaluate(self, request: LicenseUseRequest) -> LicenseDecision: ...
    def snapshot_status(self, *, snapshot_id: UUID) -> LicenseSnapshotStatus: ...


class PacketArtifactAccessSigner(Protocol):
    def sign_download(
        self, *, artifact_ref: str, expires_at: datetime
    ) -> str: ...


class LearningPackageError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LearningPackageService:
    def __init__(
        self,
        *,
        session: Session,
        license_evaluator: LicenseEvaluator,
        repository: LearningPackageRepository | None = None,
        jobs: BackgroundJobRepository | None = None,
        outbox: OutboxRepository | None = None,
        access_signer: PacketArtifactAccessSigner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._licenses = license_evaluator
        self._repository = repository or LearningPackageRepository()
        self._jobs = jobs or BackgroundJobRepository()
        self._outbox = outbox or OutboxRepository()
        self._access_signer = access_signer
        self._clock = clock or (lambda: datetime.now(UTC))

    def request_packet(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        command: CreatePacketCommand,
    ) -> OfflinePacketVersion:
        now = self._clock()
        request_hash = _hash(command.model_dump(by_alias=True, mode="json"))
        self._repository.lock(
            self._session, key=f"external-learning-command:{user_id}:packet_request:{idempotency_key}"
        )
        replay = self._repository.command_by_key(
            self._session,
            user_id=user_id,
            command_type="packet_request",
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            if replay.request_hash != request_hash:
                raise LearningPackageError(
                    "idempotency_key_reused",
                    "同一幂等键已经用于不同的学习包请求。",
                    status_code=409,
                )
            return OfflinePacketVersion.model_validate(replay.response_payload)

        packet_id = uuid4()
        packet_version_id = uuid4()
        expires_at = now + timedelta(days=command.expires_in_days)
        packet_items: list[OfflinePacketItem] = []
        license_refs: list[str] = []
        render_contents: list[PacketRenderContent] = []
        seen_snapshots: set[str] = set()
        for order_no, item in enumerate(command.items, start=1):
            source = self._repository.question_snapshot(
                self._session, question_id=item.question_id
            )
            if source is None:
                raise LearningPackageError(
                    "packet_content_missing", "学习包中的题目不存在或尚未发布。", status_code=404
                )
            actual_content_version = question_content_version_id(source)
            if actual_content_version != item.content_version_id:
                raise LearningPackageError(
                    "packet_content_version_stale",
                    "题目版本已经变化，请刷新后重新生成学习包。",
                    status_code=409,
                )
            for component in item.components:
                decision = self._licenses.evaluate(
                    LicenseUseRequest(
                        contentLicenseId=item.content_license_id,
                        contentVersionId=item.content_version_id,
                        expectedAuthorizationEpoch=item.expected_authorization_epoch,
                        component=component,
                        use=ContentUse.PRIVATE_PRINT,
                        channel=(
                            DeliveryChannel.PDF_EXPORT
                            if command.render_format == "pdf"
                            else DeliveryChannel.NOTE_APP_FILE
                        ),
                        audience=Audience.ACCOUNT_OWNER,
                        transform=ContentTransform.LAYOUT_REFLOW,
                        territory=Territory.MAINLAND_CHINA,
                        requestedAt=now,
                    )
                )
                if not decision.allowed:
                    raise LearningPackageError(
                        "packet_content_not_authorized",
                        "该题目的当前授权不允许生成私人学习包。",
                        status_code=403,
                    )
                snapshot_ref = str(decision.snapshot_id)
                if snapshot_ref not in seen_snapshots:
                    seen_snapshots.add(snapshot_ref)
                    license_refs.append(snapshot_ref)

            binding_code = _binding_code()
            packet_item_id = uuid4()
            packet_item = OfflinePacketItem(
                packetItemId=packet_item_id,
                contentVersionId=item.content_version_id,
                track=item.track,
                orderNo=order_no,
                answerBindingCode=binding_code,
            )
            packet_items.append(packet_item)
            render_contents.append(
                PacketRenderContent(
                    questionId=item.question_id,
                    contentVersionId=item.content_version_id,
                    orderNo=order_no,
                    answerBindingCode=binding_code,
                    track=item.track,
                    title=f"第 {order_no} 题",
                    stem=str(source["stem"]),
                    materialText=source.get("material_text"),
                    options=tuple(
                        {
                            "label": str(option["label"]),
                            "text": str(option["option_text"]),
                        }
                        for option in source["options"]
                    ),
                )
            )

        manifest = {
            "contractVersion": "offline-packet-manifest@v1",
            "packetId": str(packet_id),
            "renderFormat": command.render_format,
            "expiresAt": expires_at.isoformat(),
            "items": [item.model_dump(by_alias=True, mode="json") for item in packet_items],
            "licenseSnapshotRefs": license_refs,
            "contents": [item.model_dump(by_alias=True, mode="json") for item in render_contents],
        }
        packet = OfflinePacketVersion(
            packetVersionId=packet_version_id,
            packetId=packet_id,
            ownerUserId=user_id,
            versionNo=1,
            state="requested",
            renderFormat=command.render_format,
            items=tuple(packet_items),
            manifestSha256=_hash(manifest),
            licenseSnapshotRefs=tuple(license_refs),
            artifactRef=None,
            expiresAt=expires_at,
            createdAt=now,
        )
        self._repository.insert_packet(self._session, packet=packet)
        for packet_item in packet_items:
            self._repository.insert_binding(
                self._session,
                binding_digest=answer_binding_digest(packet_item.answer_binding_code),
                packet_id=packet.packet_id,
                packet_version_id=packet.packet_version_id,
                owner_user_id=packet.owner_user_id,
                packet_item_id=packet_item.packet_item_id,
                content_version_id=packet_item.content_version_id,
                track=packet_item.track.value,
                order_no=packet_item.order_no,
                created_at=now,
            )
        render_request = PacketRenderRequest(
            packetVersionId=packet.packet_version_id,
            ownerSubjectRef=_subject_ref(user_id),
            renderFormat=packet.render_format,
            manifestSha256=packet.manifest_sha256,
            expiresAt=expires_at.isoformat(),
            contents=tuple(render_contents),
        )
        self._jobs.enqueue(
            self._session,
            job_key=f"offline-packet-render:{packet.packet_version_id}",
            job_type="offline_packet_render_v1",
            input_version=packet.manifest_sha256,
            payload={
                "packetVersionId": str(packet.packet_version_id),
                "licenseSnapshotRefs": license_refs,
                "renderRequest": render_request.model_dump(by_alias=True, mode="json"),
            },
            max_attempts=3,
            run_after=now,
        )
        response_payload = packet.model_dump(by_alias=True, mode="json")
        self._repository.insert_command(
            self._session,
            command_id=uuid4(),
            user_id=user_id,
            command_type="packet_request",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response_payload,
            created_at=now,
        )
        self._outbox.append(
            self._session,
            deduplication_key=f"offline-packet:{packet.packet_version_id}:requested",
            aggregate_type="offline_packet",
            aggregate_id=str(packet.packet_id),
            event_type="offline_packet.requested",
            payload={
                "packetVersionId": str(packet.packet_version_id),
                "ownerSubjectRef": _subject_ref(user_id),
                "manifestSha256": packet.manifest_sha256,
                "itemCount": len(packet.items),
            },
        )
        return packet

    def get_packet(self, *, user_id: str, packet_version_id: UUID) -> OfflinePacketVersion:
        packet = self._repository.packet(
            self._session, user_id=user_id, packet_version_id=packet_version_id
        )
        if packet is None:
            raise LearningPackageError("packet_not_found", "学习包不存在。", status_code=404)
        return packet

    def get_render_request(
        self, *, user_id: str, packet_version_id: UUID
    ) -> PacketRenderRequest:
        """Returns the immutable render manifest behind an owned packet version."""

        packet = self.get_packet(user_id=user_id, packet_version_id=packet_version_id)
        payload = self._required_render_payload(packet.packet_id)
        request = PacketRenderRequest.model_validate(payload["renderRequest"])
        return request.model_copy(
            update={
                "packet_version_id": packet.packet_version_id,
                "manifest_sha256": packet.manifest_sha256,
                "expires_at": packet.expires_at.isoformat() if packet.expires_at else "",
            }
        )

    def list_packets(self, *, user_id: str) -> tuple[OfflinePacketVersion, ...]:
        return self._repository.list_packets(self._session, user_id=user_id)

    def list_snapshot(self, *, user_id: str) -> PacketListSnapshot:
        now = self._clock()
        candidates: list[PacketContentCandidate] = []
        for row in self._repository.eligible_packet_content(
            self._session, user_id=user_id
        ):
            record = ContentLicenseRecord.model_validate(row["record_payload"])
            snapshot = dict(row["snapshot_payload"])
            components = _packet_components(snapshot=snapshot, record=record)
            if not components:
                continue
            candidates.append(
                PacketContentCandidate(
                    questionId=str(row["question_id"]),
                    contentVersionId=str(row["content_version_id"]),
                    contentLicenseId=row["content_license_id"],
                    expectedAuthorizationEpoch=int(row["authorization_epoch"]),
                    components=components,
                    title=str(snapshot.get("stem") or row["question_id"])[:120],
                    track=str(row["track"]),
                    sourceLabel=(
                        "本人私有资料"
                        if record.rights.distribution_class == "account_private"
                        else "已授权题库"
                    ),
                    versionLabel=f"内容版本 {str(row['content_version_id'])[:8]}",
                    selectedByDefault=True,
                )
            )
        reasons = () if candidates else ("暂无已确认且当前授权允许导出的题目版本。",)
        return PacketListSnapshot(
            packets=self.list_packets(user_id=user_id),
            eligibleContent=tuple(candidates),
            partialReasons=reasons,
            generatedAt=now,
        )

    def retry_packet(
        self,
        *,
        user_id: str,
        packet_id: UUID,
        idempotency_key: str,
        command: RetryPacketCommand,
    ) -> OfflinePacketVersion:
        request_hash = _hash(
            {"packetId": str(packet_id), **command.model_dump(by_alias=True, mode="json")}
        )
        replay = self._begin_packet_command(
            user_id=user_id,
            command_type="packet_retry",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        self._repository.lock(self._session, key=f"offline-packet:{packet_id}")
        current = self._repository.current_packet(
            self._session, packet_id=packet_id, for_update=True
        )
        if (
            current is None
            or str(current.owner_user_id) != user_id
            or current.packet_version_id != command.failed_packet_version_id
        ):
            raise LearningPackageError("packet_not_found", "学习包版本不存在。", status_code=404)
        if current.state != "failed" or current.version_no != command.expected_version_no:
            raise LearningPackageError(
                "packet_retry_conflict", "学习包状态已经变化，请刷新后再试。", status_code=409
            )
        self._require_snapshots_current(current)
        render_payload = self._required_render_payload(current.packet_id)
        packet = current.model_copy(
            update={
                "packet_version_id": uuid4(),
                "version_no": current.version_no + 1,
                "state": "requested",
                "artifact_ref": None,
                "created_at": self._clock(),
            }
        )
        self._repository.insert_packet(self._session, packet=packet)
        self._enqueue_render(packet=packet, render_payload=render_payload)
        self._record_packet_command(
            user_id=user_id,
            command_type="packet_retry",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            packet=packet,
        )
        return packet

    def renew_packet(
        self,
        *,
        user_id: str,
        packet_id: UUID,
        idempotency_key: str,
        command: RenewPacketCommand,
    ) -> OfflinePacketVersion:
        request_hash = _hash(
            {"packetId": str(packet_id), **command.model_dump(by_alias=True, mode="json")}
        )
        replay = self._begin_packet_command(
            user_id=user_id,
            command_type="packet_renew",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        self._repository.lock(self._session, key=f"offline-packet:{packet_id}")
        current = self._repository.current_packet(
            self._session, packet_id=packet_id, for_update=True
        )
        if (
            current is None
            or str(current.owner_user_id) != user_id
            or current.packet_version_id != command.expired_packet_version_id
        ):
            raise LearningPackageError("packet_not_found", "学习包版本不存在。", status_code=404)
        now = self._clock()
        effectively_expired = current.expires_at is not None and now >= current.expires_at
        if (
            current.version_no != command.expected_version_no
            or (current.state not in {"expired", "revoked"} and not effectively_expired)
        ):
            raise LearningPackageError(
                "packet_renew_conflict", "只有已过期或授权失效的当前学习包才能续版。", status_code=409
            )
        requests = self._repository.renewal_requests(
            self._session, snapshot_refs=current.license_snapshot_refs
        )
        if len(requests) != len(current.license_snapshot_refs):
            raise LearningPackageError(
                "packet_content_not_authorized",
                "学习包中的内容已没有完整有效授权，无法续版。",
                status_code=403,
            )
        license_refs: list[str] = []
        for payload in requests:
            payload["requestedAt"] = now.isoformat()
            decision = self._licenses.evaluate(LicenseUseRequest.model_validate(payload))
            if not decision.allowed:
                raise LearningPackageError(
                    "packet_content_not_authorized",
                    "学习包中的内容当前不允许重新导出。",
                    status_code=403,
                )
            license_refs.append(str(decision.snapshot_id))
        old_payload = self._required_render_payload(current.packet_id)
        old_request = PacketRenderRequest.model_validate(old_payload["renderRequest"])
        expires_at = now + timedelta(days=command.expires_in_days)
        manifest = {
            "contractVersion": "offline-packet-manifest@v1",
            "packetId": str(current.packet_id),
            "renderFormat": current.render_format,
            "expiresAt": expires_at.isoformat(),
            "items": [item.model_dump(by_alias=True, mode="json") for item in current.items],
            "licenseSnapshotRefs": license_refs,
            "contents": [
                item.model_dump(by_alias=True, mode="json") for item in old_request.contents
            ],
        }
        packet = current.model_copy(
            update={
                "packet_version_id": uuid4(),
                "version_no": current.version_no + 1,
                "state": "requested",
                "manifest_sha256": _hash(manifest),
                "license_snapshot_refs": tuple(license_refs),
                "artifact_ref": None,
                "expires_at": expires_at,
                "created_at": now,
            }
        )
        self._repository.insert_packet(self._session, packet=packet)
        self._enqueue_render(packet=packet, render_payload=old_payload)
        self._record_packet_command(
            user_id=user_id,
            command_type="packet_renew",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            packet=packet,
        )
        return packet

    def access_packet(
        self, *, user_id: str, packet_version_id: UUID, ttl: timedelta
    ) -> PacketAccessProjection:
        if self._access_signer is None:
            raise LearningPackageError(
                "packet_access_not_configured", "学习包访问签发服务未配置。", status_code=503
            )
        packet = self.get_packet(user_id=user_id, packet_version_id=packet_version_id)
        now = self._clock()
        if (
            packet.state != "ready"
            or packet.artifact_ref is None
            or packet.expires_at is None
            or now >= packet.expires_at
        ):
            raise LearningPackageError(
                "packet_not_accessible", "学习包当前不可访问。", status_code=409
            )
        self._require_snapshots_current(packet)
        if ttl <= timedelta(0) or ttl > timedelta(minutes=15):
            raise LearningPackageError(
                "packet_access_policy_invalid", "学习包访问有效期配置无效。", status_code=503
            )
        expires_at = min(now + ttl, packet.expires_at)
        return PacketAccessProjection(
            packetVersionId=packet.packet_version_id,
            downloadUrl=self._access_signer.sign_download(
                artifact_ref=packet.artifact_ref, expires_at=expires_at
            ),
            fileType=packet.render_format,
            expiresAt=expires_at,
        )

    def _begin_packet_command(
        self,
        *,
        user_id: str,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
    ) -> OfflinePacketVersion | None:
        self._repository.lock(
            self._session,
            key=f"external-learning-command:{user_id}:{command_type}:{idempotency_key}",
        )
        replay = self._repository.command_by_key(
            self._session,
            user_id=user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
        )
        if replay is None:
            return None
        if replay.request_hash != request_hash:
            raise LearningPackageError(
                "idempotency_key_reused", "同一幂等键已经用于其他学习包命令。", status_code=409
            )
        return OfflinePacketVersion.model_validate(replay.response_payload)

    def _record_packet_command(
        self,
        *,
        user_id: str,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
        packet: OfflinePacketVersion,
    ) -> None:
        self._repository.insert_command(
            self._session,
            command_id=uuid4(),
            user_id=user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=packet.model_dump(by_alias=True, mode="json"),
            created_at=self._clock(),
        )
        self._outbox.append(
            self._session,
            deduplication_key=f"offline-packet:{packet.packet_version_id}:requested",
            aggregate_type="offline_packet",
            aggregate_id=str(packet.packet_id),
            event_type="offline_packet.requested",
            payload={
                "packetVersionId": str(packet.packet_version_id),
                "manifestSha256": packet.manifest_sha256,
                "operation": command_type,
            },
        )

    def _required_render_payload(self, packet_id: UUID) -> dict[str, object]:
        payload = self._repository.render_job_payload(self._session, packet_id=packet_id)
        if payload is None:
            raise LearningPackageError(
                "packet_render_manifest_missing", "学习包渲染清单缺失。", status_code=409
            )
        return payload

    def _enqueue_render(
        self, *, packet: OfflinePacketVersion, render_payload: dict[str, object]
    ) -> None:
        old_request = PacketRenderRequest.model_validate(render_payload["renderRequest"])
        request = old_request.model_copy(
            update={
                "packet_version_id": packet.packet_version_id,
                "manifest_sha256": packet.manifest_sha256,
                "expires_at": packet.expires_at.isoformat() if packet.expires_at else "",
            }
        )
        self._jobs.enqueue(
            self._session,
            job_key=f"offline-packet-render:{packet.packet_version_id}",
            job_type="offline_packet_render_v1",
            input_version=packet.manifest_sha256,
            payload={
                "packetVersionId": str(packet.packet_version_id),
                "licenseSnapshotRefs": list(packet.license_snapshot_refs),
                "renderRequest": request.model_dump(by_alias=True, mode="json"),
            },
            max_attempts=3,
        )

    def _require_snapshots_current(self, packet: OfflinePacketVersion) -> None:
        for snapshot_ref in packet.license_snapshot_refs:
            if not self._licenses.snapshot_status(snapshot_id=UUID(snapshot_ref)).valid:
                raise LearningPackageError(
                    "packet_content_not_authorized", "学习包内容授权已经失效。", status_code=403
                )


def _binding_code() -> str:
    return f"LP-{secrets.token_hex(4).upper()}"


def _subject_ref(user_id: str) -> str:
    return _hash({"scope": "external-learning-subject", "userId": user_id})


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _packet_components(
    *, snapshot: dict[str, object], record: ContentLicenseRecord
) -> tuple[ContentComponent, ...]:
    present: list[ContentComponent] = [ContentComponent.QUESTION_PROMPT]
    if snapshot.get("options"):
        present.append(ContentComponent.QUESTION_OPTIONS)
    if snapshot.get("material_text"):
        present.append(ContentComponent.MATERIAL_TEXT)
    allowed = {
        cell.component
        for cell in record.rights.grant_matrix.allowed_grant_cells
        if cell.use == ContentUse.PRIVATE_PRINT
        and cell.channel in {DeliveryChannel.PDF_EXPORT, DeliveryChannel.NOTE_APP_FILE}
        and cell.audience == Audience.ACCOUNT_OWNER
        and cell.transform == ContentTransform.LAYOUT_REFLOW
        and cell.territory == Territory.MAINLAND_CHINA
    }
    return tuple(component for component in present if component in allowed)
