from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    AnswerIngestionVersion,
    OfflinePacketVersion,
)
from app.domains.answer_ingestion.repository import AnswerIngestionRepository
from app.domains.answer_ingestion.schemas import (
    AnswerCandidateItem,
    AnswerEntry,
    AnswerFactReceipt,
    AnswerIngestionWorkspace,
    AnswerTargetSnapshot,
    ConfirmAnswerCandidateCommand,
    CorrectAnswerCandidateCommand,
    CreateAnswerCandidateCommand,
    PacketAnswerTarget,
)
from app.domains.learning_packages.binding import answer_binding_digest
from app.domains.learning_packages.service import LicenseEvaluator
from app.platform.outbox import OutboxRepository


class AnswerIngestionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AnswerIngestionService:
    def __init__(
        self,
        *,
        session: Session,
        license_evaluator: LicenseEvaluator,
        repository: AnswerIngestionRepository | None = None,
        outbox: OutboxRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._licenses = license_evaluator
        self._repository = repository or AnswerIngestionRepository()
        self._outbox = outbox or OutboxRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_candidate(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        command: CreateAnswerCandidateCommand,
    ) -> AnswerIngestionVersion:
        request_hash = _hash(command.model_dump(by_alias=True, mode="json"))
        command_key = f"answer-candidate:{idempotency_key}"
        self._repository.lock(
            self._session, key=f"external-learning-command:{user_id}:{command_key}"
        )
        replay = self._command_replay(
            user_id=user_id,
            command_type="answer_candidate",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay

        packet_row = self._repository.packet(
            self._session,
            user_id=user_id,
            packet_version_id=command.packet_version_id,
        )
        if packet_row is None:
            raise AnswerIngestionError("packet_not_found", "学习包不存在。", status_code=404)
        packet = OfflinePacketVersion.model_validate(packet_row["contract_payload"])
        self._require_packet_admissible(packet)
        bindings = self._repository.packet_bindings(
            self._session, packet_id=UUID(str(packet_row["packet_id"])), user_id=user_id
        )
        track_bindings = [row for row in bindings if str(row["track"]) == command.track.value]
        if not track_bindings:
            raise AnswerIngestionError(
                "packet_track_missing", "学习包中没有该科目的题目。", status_code=409
            )
        if command.input_mode == "answer_string":
            assert command.answer_string is not None
            if len(command.answer_string) != len(track_bindings):
                raise AnswerIngestionError(
                    "answer_count_mismatch",
                    "答案串长度与学习包中的题目数量不一致。",
                    status_code=409,
                )
            entries = tuple(
                AnswerEntry(
                    bindingCode=_packet_binding_code(packet, int(row["order_no"])),
                    answer=answer,
                )
                for row, answer in zip(track_bindings, command.answer_string)
            )
        else:
            if not self._repository.artifacts_available(
                self._session,
                user_id=user_id,
                artifact_ids=command.source_artifact_version_ids,
            ):
                raise AnswerIngestionError(
                    "answer_artifact_unavailable",
                    "答案图片不存在、未通过安全检查或不属于当前用户。",
                    status_code=409,
                )
            if not self._repository.recognitions_admissible(
                self._session,
                user_id=user_id,
                recognition_ids=command.recognition_revision_ids,
                artifact_ids=command.source_artifact_version_ids,
            ):
                raise AnswerIngestionError(
                    "answer_recognition_unavailable",
                    "答案识别结果不是当前可复核版本。",
                    status_code=409,
                )
            entries = command.entries
        canonical_entries = self._validate_entries(entries=entries, bindings=track_bindings)
        now = self._clock()
        ingestion_id = uuid4()
        source_refs = (
            f"offline-packet:{packet.packet_version_id}",
            *tuple(f"artifact-version:{item}" for item in command.source_artifact_version_ids),
            *tuple(f"recognition-revision:{item}" for item in command.recognition_revision_ids),
            f"answer-candidate-command:{request_hash}",
        )
        ingestion = AnswerIngestionVersion(
            ingestionVersionId=uuid4(),
            ingestionId=ingestion_id,
            ownerUserId=user_id,
            versionNo=1,
            packetVersionId=packet.packet_version_id,
            sourceArtifactVersionIds=command.source_artifact_version_ids,
            track=command.track,
            inputMode=command.input_mode,
            state="candidate",
            candidatePayload={
                "schemaVersion": "answer-candidate-payload@v1",
                "entries": canonical_entries,
                "confirmedForLearning": False,
            },
            targetWriter=(
                "xingce_learning" if command.track.value == "xingce" else "essay_learning"
            ),
            writerContractVersion=(
                "xingce-external-answer-writer@v1"
                if command.track.value == "xingce"
                else "essay-external-answer-writer@v1"
            ),
            sourceLineageRefs=source_refs,
            confirmedAt=None,
            committedFactRef=None,
            createdAt=now,
        )
        self._repository.insert(self._session, ingestion=ingestion)
        self._record_command(
            user_id=user_id,
            command_type="answer_candidate",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            ingestion=ingestion,
            created_at=now,
        )
        return ingestion

    def correct_candidate(
        self,
        *,
        user_id: str,
        ingestion_id: UUID,
        idempotency_key: str,
        command: CorrectAnswerCandidateCommand,
    ) -> AnswerIngestionVersion:
        request_hash = _hash(
            {"ingestionId": str(ingestion_id), **command.model_dump(by_alias=True, mode="json")}
        )
        self._repository.lock(
            self._session,
            key=f"external-learning-command:{user_id}:answer_correction:{idempotency_key}",
        )
        replay = self._command_replay(
            user_id=user_id,
            command_type="answer_correction",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        self._repository.lock(self._session, key=f"answer-ingestion:{ingestion_id}")
        current = self._required_current(user_id=user_id, ingestion_id=ingestion_id)
        if current.state != "candidate" or current.version_no != command.expected_version_no:
            raise AnswerIngestionError(
                "answer_candidate_version_conflict", "答案候选已经变化，请刷新后重试。", status_code=409
            )
        assert current.packet_version_id is not None
        packet_row = self._repository.packet(
            self._session, user_id=user_id, packet_version_id=current.packet_version_id
        )
        if packet_row is None:
            raise AnswerIngestionError("packet_not_found", "学习包不存在。", status_code=404)
        bindings = self._repository.packet_bindings(
            self._session, packet_id=UUID(str(packet_row["packet_id"])), user_id=user_id
        )
        track_bindings = [row for row in bindings if str(row["track"]) == current.track.value]
        canonical_entries = self._validate_entries(entries=command.entries, bindings=track_bindings)
        corrected = current.model_copy(
            update={
                "ingestion_version_id": uuid4(),
                "version_no": current.version_no + 1,
                "candidate_payload": {
                    "schemaVersion": "answer-candidate-payload@v1",
                    "entries": canonical_entries,
                    "confirmedForLearning": False,
                },
                "source_lineage_refs": (
                    *current.source_lineage_refs,
                    f"answer-ingestion-version:{current.ingestion_version_id}",
                ),
                "created_at": self._clock(),
            }
        )
        self._repository.insert(self._session, ingestion=corrected)
        self._record_command(
            user_id=user_id,
            command_type="answer_correction",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            ingestion=corrected,
            created_at=corrected.created_at,
        )
        return corrected

    def confirm_candidate(
        self,
        *,
        user_id: str,
        ingestion_id: UUID,
        idempotency_key: str,
        command: ConfirmAnswerCandidateCommand,
    ) -> AnswerFactReceipt:
        request_hash = _hash(
            {"ingestionId": str(ingestion_id), **command.model_dump(by_alias=True, mode="json")}
        )
        self._repository.lock(
            self._session,
            key=f"external-learning-command:{user_id}:answer_confirm:{idempotency_key}",
        )
        command_row = self._command_payload(
            user_id=user_id,
            command_type="answer_confirm",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if command_row is not None:
            return AnswerFactReceipt.model_validate(command_row)
        self._repository.lock(self._session, key=f"answer-ingestion:{ingestion_id}")
        current = self._required_current(user_id=user_id, ingestion_id=ingestion_id)
        if current.state != "candidate" or current.version_no != command.expected_version_no:
            raise AnswerIngestionError(
                "answer_candidate_version_conflict", "答案候选已经变化，请刷新后重试。", status_code=409
            )
        assert current.packet_version_id is not None
        packet_row = self._repository.packet(
            self._session, user_id=user_id, packet_version_id=current.packet_version_id
        )
        if packet_row is None:
            raise AnswerIngestionError("packet_not_found", "学习包不存在。", status_code=404)
        packet = OfflinePacketVersion.model_validate(packet_row["contract_payload"])
        self._require_packet_admissible(packet)
        recognition_ids = tuple(
            UUID(ref.removeprefix("recognition-revision:"))
            for ref in current.source_lineage_refs
            if ref.startswith("recognition-revision:")
        )
        confirmed_recognition_ids = self._repository.confirmed_recognition_lineage(
            self._session,
            user_id=user_id,
            recognition_ids=recognition_ids,
            artifact_ids=current.source_artifact_version_ids,
        )
        if recognition_ids and confirmed_recognition_ids is None:
            raise AnswerIngestionError(
                "answer_recognition_unconfirmed",
                "答案识别结果尚未由用户确认。",
                status_code=409,
            )
        confirmed_recognition_refs = tuple(
            f"recognition-revision:{item}"
            for item in (confirmed_recognition_ids or ())
        )
        bindings = self._repository.packet_bindings(
            self._session, packet_id=UUID(str(packet_row["packet_id"])), user_id=user_id
        )
        by_digest = {str(row["binding_digest"]): row for row in bindings}
        entries = list(current.candidate_payload.get("entries", []))
        if any(bool(entry.get("requiresCorrection")) for entry in entries):
            raise AnswerIngestionError(
                "answer_correction_required",
                "仍有低置信或歧义答案需要人工修订。",
                status_code=409,
            )
        fact_batch_id = uuid4()
        committed_version_id = uuid4()
        created_at = self._clock()
        fact_refs: list[str] = []
        for entry in entries:
            binding = by_digest.get(str(entry["bindingDigest"]))
            if binding is None or str(binding["track"]) != current.track.value:
                raise AnswerIngestionError(
                    "answer_binding_changed", "学习包答题绑定已经变化，无法确认。", status_code=409
                )
            fact_id = uuid4()
            fact_ref = f"external-answer-fact:{fact_id}"
            fact_refs.append(fact_ref)
            self._repository.insert_fact(
                self._session,
                fact_id=fact_id,
                fact_batch_id=fact_batch_id,
                ingestion_version_id=committed_version_id,
                owner_user_id=current.owner_user_id,
                packet_version_id=current.packet_version_id,
                packet_item_id=UUID(str(binding["packet_item_id"])),
                content_version_id=str(binding["content_version_id"]),
                track=current.track.value,
                answer_payload={"answer": entry["answer"]},
                source_lineage_refs=(
                    *current.source_lineage_refs,
                    *confirmed_recognition_refs,
                    f"answer-ingestion-version:{current.ingestion_version_id}",
                ),
                target_writer=current.target_writer,
                created_at=created_at,
            )
            self._outbox.append(
                self._session,
                deduplication_key=f"external-answer-fact:{fact_id}",
                aggregate_type="external_answer_fact",
                aggregate_id=str(fact_id),
                event_type=f"{current.target_writer}.external_answer_confirmed",
                payload={
                    "factRef": fact_ref,
                    "factBatchId": str(fact_batch_id),
                    "ownerSubjectRef": _subject_ref(user_id),
                    "contentVersionId": str(binding["content_version_id"]),
                    "track": current.track.value,
                    "evidencePolicyVersion": "external-confirmed-lower-weight-v1",
                    "reviewState": "user_confirmed",
                    "sourceLineageRefs": [
                        *current.source_lineage_refs,
                        *confirmed_recognition_refs,
                    ],
                },
            )
        committed = current.model_copy(
            update={
                "ingestion_version_id": committed_version_id,
                "version_no": current.version_no + 1,
                "state": "committed",
                "candidate_payload": {
                    **current.candidate_payload,
                    "confirmedForLearning": True,
                },
                "confirmed_at": created_at,
                "committed_fact_ref": f"external-answer-batch:{fact_batch_id}",
                "source_lineage_refs": (
                    *current.source_lineage_refs,
                    *confirmed_recognition_refs,
                    f"answer-ingestion-version:{current.ingestion_version_id}",
                ),
                "created_at": created_at,
            }
        )
        self._repository.insert(self._session, ingestion=committed)
        receipt = AnswerFactReceipt(
            factBatchId=fact_batch_id,
            factRefs=tuple(fact_refs),
            ingestion=committed,
        )
        self._record_raw_command(
            user_id=user_id,
            command_type="answer_confirm",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=receipt.model_dump(by_alias=True, mode="json"),
            created_at=created_at,
        )
        return receipt

    def get_current(self, *, user_id: str, ingestion_id: UUID) -> AnswerIngestionVersion:
        return self._required_current(user_id=user_id, ingestion_id=ingestion_id)

    def workspace(
        self, *, user_id: str, ingestion: AnswerIngestionVersion
    ) -> AnswerIngestionWorkspace:
        if str(ingestion.owner_user_id) != user_id or ingestion.packet_version_id is None:
            raise AnswerIngestionError(
                "answer_ingestion_not_found", "答案录入不存在。", status_code=404
            )
        packet_row = self._repository.packet(
            self._session,
            user_id=user_id,
            packet_version_id=ingestion.packet_version_id,
        )
        if packet_row is None:
            raise AnswerIngestionError("packet_not_found", "学习包不存在。", status_code=404)
        packet = OfflinePacketVersion.model_validate(packet_row["contract_payload"])
        bindings = self._repository.packet_bindings(
            self._session,
            packet_id=UUID(str(packet_row["packet_id"])),
            user_id=user_id,
        )
        by_digest = {str(row["binding_digest"]): row for row in bindings}
        packet_by_order = {item.order_no: item for item in packet.items}
        items: list[AnswerCandidateItem] = []
        for entry in ingestion.candidate_payload.get("entries", []):
            if not isinstance(entry, dict):
                raise AnswerIngestionError(
                    "answer_payload_invalid", "答案候选数据不完整。", status_code=409
                )
            binding = by_digest.get(str(entry.get("bindingDigest", "")))
            if binding is None:
                raise AnswerIngestionError(
                    "answer_binding_changed", "学习包答题绑定已经变化。", status_code=409
                )
            order_no = int(binding["order_no"])
            packet_item = packet_by_order.get(order_no)
            if packet_item is None:
                raise AnswerIngestionError(
                    "packet_binding_missing", "学习包答题编号不完整。", status_code=409
                )
            confidence_value = entry.get("confidence")
            items.append(
                AnswerCandidateItem(
                    itemId=str(binding["packet_item_id"]),
                    orderNo=order_no,
                    answerBindingCode=packet_item.answer_binding_code,
                    answer=str(entry.get("answer", "")),
                    confidence=(
                        float(confidence_value) if confidence_value is not None else None
                    ),
                    requiresCorrection=bool(entry.get("requiresCorrection", False)),
                )
            )
        return AnswerIngestionWorkspace(
            ingestion=ingestion,
            items=tuple(items),
            packetTitle=f"学习包 {str(packet.packet_id)[:8]}",
            packetItemCount=len(packet.items),
            partialReasons=(
                ("外部作答证据已确认并写入学习事实。",)
                if ingestion.state == "committed"
                else ()
            ),
            expiresAt=packet.expires_at,
            diagnosticEligibility=(
                "eligible_committed"
                if ingestion.state == "committed"
                else "blocked_unconfirmed"
            ),
            generatedAt=self._clock(),
        )

    def list_targets(self, *, user_id: str) -> AnswerTargetSnapshot:
        now = self._clock()
        targets: list[PacketAnswerTarget] = []
        for row in self._repository.current_ready_packets(
            self._session, user_id=user_id, at=now
        ):
            packet = OfflinePacketVersion.model_validate(row["contract_payload"])
            if any(
                not self._licenses.snapshot_status(snapshot_id=UUID(ref)).valid
                for ref in packet.license_snapshot_refs
            ):
                continue
            counts: dict[str, int] = {}
            for item in packet.items:
                counts[item.track.value] = counts.get(item.track.value, 0) + 1
            for track, count in sorted(counts.items()):
                targets.append(
                    PacketAnswerTarget(
                        packetVersionId=packet.packet_version_id,
                        packetTitle=(
                            f"学习包 {str(packet.packet_id)[:8]}"
                            + (f" · {'行测' if track == 'xingce' else '申论'}" if len(counts) > 1 else "")
                        ),
                        track=track,
                        itemCount=count,
                        versionLabel=f"v{packet.version_no}",
                        expiresAt=packet.expires_at,
                    )
                )
        return AnswerTargetSnapshot(packets=tuple(targets), generatedAt=now)

    def _validate_entries(
        self, *, entries: tuple[AnswerEntry, ...], bindings: list[object]
    ) -> list[dict[str, object]]:
        by_digest = {str(row["binding_digest"]): row for row in bindings}  # type: ignore[index]
        canonical: list[dict[str, str]] = []
        for entry in entries:
            digest = answer_binding_digest(entry.binding_code)
            binding = by_digest.get(digest)
            if binding is None:
                raise AnswerIngestionError(
                    "answer_binding_invalid", "答案编号不属于当前学习包。", status_code=409
                )
            if str(binding["track"]) == "xingce" and not _valid_objective_answer(entry.answer):
                raise AnswerIngestionError(
                    "objective_answer_invalid", "行测答案必须是有效选项字母。", status_code=422
                )
            canonical.append(
                {
                    "bindingDigest": digest,
                    "contentVersionId": str(binding["content_version_id"]),
                    "answer": entry.answer.strip().upper() if str(binding["track"]) == "xingce" else entry.answer.strip(),
                    "confidence": entry.confidence,
                    "requiresCorrection": entry.requires_correction,
                }
            )
        if len(canonical) != len(bindings):
            raise AnswerIngestionError(
                "answer_count_mismatch", "答案数量与该科目的题目数量不一致。", status_code=409
            )
        return canonical

    def _require_packet_admissible(self, packet: OfflinePacketVersion) -> None:
        now = self._clock()
        if packet.state != "ready":
            raise AnswerIngestionError(
                "packet_not_ready", "学习包尚未生成完成，不能录入答案。", status_code=409
            )
        if packet.expires_at is None or now >= packet.expires_at:
            raise AnswerIngestionError("packet_expired", "学习包已经过期。", status_code=409)
        for snapshot_ref in packet.license_snapshot_refs:
            status = self._licenses.snapshot_status(snapshot_id=UUID(snapshot_ref))
            if not status.valid:
                raise AnswerIngestionError(
                    "packet_license_revoked", "学习包内容授权已经失效，不能继续回录。", status_code=403
                )

    def _required_current(self, *, user_id: str, ingestion_id: UUID) -> AnswerIngestionVersion:
        current = self._repository.current(
            self._session, user_id=user_id, ingestion_id=ingestion_id, for_update=True
        )
        if current is None:
            raise AnswerIngestionError("answer_ingestion_not_found", "答案录入不存在。", status_code=404)
        return current

    def _command_replay(
        self, *, user_id: str, command_type: str, idempotency_key: str, request_hash: str
    ) -> AnswerIngestionVersion | None:
        payload = self._command_payload(
            user_id=user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        return AnswerIngestionVersion.model_validate(payload) if payload is not None else None

    def _command_payload(
        self, *, user_id: str, command_type: str, idempotency_key: str, request_hash: str
    ) -> dict[str, object] | None:
        from app.domains.learning_packages.repository import LearningPackageRepository

        stored = LearningPackageRepository().command_by_key(
            self._session,
            user_id=user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
        )
        if stored is None:
            return None
        if stored.request_hash != request_hash:
            raise AnswerIngestionError(
                "idempotency_key_reused", "同一幂等键已经用于不同的答案命令。", status_code=409
            )
        return stored.response_payload

    def _record_command(
        self,
        *,
        user_id: str,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
        ingestion: AnswerIngestionVersion,
        created_at: datetime,
    ) -> None:
        self._record_raw_command(
            user_id=user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=ingestion.model_dump(by_alias=True, mode="json"),
            created_at=created_at,
        )

    def _record_raw_command(
        self,
        *,
        user_id: str,
        command_type: str,
        idempotency_key: str,
        request_hash: str,
        response_payload: dict[str, object],
        created_at: datetime,
    ) -> None:
        from app.domains.learning_packages.repository import LearningPackageRepository

        LearningPackageRepository().insert_command(
            self._session,
            command_id=uuid4(),
            user_id=user_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response_payload=response_payload,
            created_at=created_at,
        )


def _packet_binding_code(packet: OfflinePacketVersion, order_no: int) -> str:
    for item in packet.items:
        if item.order_no == order_no:
            return item.answer_binding_code
    raise AnswerIngestionError("packet_binding_missing", "学习包答题编号不完整。", status_code=409)


def _valid_objective_answer(value: str) -> bool:
    normalized = value.strip().upper()
    return bool(normalized) and len(normalized) <= 8 and all("A" <= item <= "H" for item in normalized)


def _subject_ref(user_id: str) -> str:
    return _hash({"scope": "external-learning-subject", "userId": user_id})


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
