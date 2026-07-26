"""「存资料」回复指令：把最近一张已答复的图片转入私有阅读室摄取。

复用 private_knowledge 既有摄取链路的中间状态（上传产物 + 待核对识别修订），
以及其入库命令的幂等模式（命令回执 + 请求指纹重放比对）。转入终点是
候选（candidate）识别修订：用户在小程序核对确认前，不产生私有源版本，
也不产生任何检索片段；图片不进入公共题库，不用于模型训练。
"""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from hashlib import sha256
import json
from typing import Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    CandidateState,
    RecognitionKind,
    RecognitionRevision,
    SourceArtifactState,
    SourceArtifactVersion,
    SourceKind,
)
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    PageSegment,
    RecognitionCandidatePayload,
    TextSpan,
)
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.upload.repository import PostgresUploadRepository
from app.domains.ilink_channel.crypto import ILinkIdentityCipher
from app.domains.ilink_channel.repository import (
    ILinkConflict,
    ILinkNotFound,
    ILinkRepository,
)
from app.domains.ilink_channel.settings import ILinkSettings


STORE_MATERIAL_COMMAND = "存资料"
PRIVATE_READING_ROOM_PATH = "/pages/private-knowledge/index"
MATERIAL_INTAKE_POLICY_VERSION = "ilink-material-intake@v1"
MATERIAL_RECOGNITION_TTL = timedelta(days=7)

_SEGMENT_KINDS = frozenset(
    {"question", "options", "material", "answer_area", "essay_prompt", "unknown"}
)


def store_material_receipt_text() -> str:
    return (
        "已收进你的私有阅读室，去小程序核对识别结果。\n"
        f"路径：小程序 →「我的」→「私有阅读室」（{PRIVATE_READING_ROOM_PATH}）。\n"
        "核对确认前不会生成检索片段；图片不进入公共题库，也不会用于模型训练。"
    )


class ILinkMaterialIntake:
    def __init__(
        self,
        *,
        settings: ILinkSettings,
        cipher: ILinkIdentityCipher,
        storage,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._cipher = cipher
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))

    def store_latest(self, session: Session, *, user_id: UUID) -> str:
        now = self._clock()
        repository = ILinkRepository(session)
        query = repository.latest_query(user_id=user_id, for_update=True)
        if query is None:
            raise ILinkNotFound("query_not_found")
        query_id = UUID(str(query["query_id"]))
        command_id = f"ilink-store-material:{query_id}"
        request_fingerprint = self._cipher.fingerprint_payload(
            {
                "queryId": str(query_id),
                "sourceFingerprint": str(query["source_fingerprint"] or ""),
            },
            purpose="store-material-command",
        )
        replay = repository.command_receipt(command_id=command_id)
        if replay is not None:
            if replay["request_fingerprint"] != request_fingerprint:
                raise ILinkConflict("idempotency_conflict")
            stored_reply = dict(replay["result_payload"]).get("replyText")
            return str(stored_reply or store_material_receipt_text())
        if query["state"] != "answered" or not query["recognized_payload"]:
            raise ILinkConflict("material_query_not_answered")
        object_ref = query["transient_object_ref"]
        source_fingerprint = query["source_fingerprint"]
        mime_type = query["mime_type"]
        if not object_ref or not source_fingerprint or not mime_type:
            raise ILinkConflict("material_window_closed")
        candidate = _candidate_payload(
            recognized=dict(query["recognized_payload"]),
            query_id=query_id,
            request_hash=request_fingerprint,
        )
        byte_size = self._storage.stat(object_ref=str(object_ref))
        artifact = SourceArtifactVersion(
            artifactVersionId=uuid5(NAMESPACE_URL, f"ilink-material-artifact:{query_id}"),
            artifactId=uuid5(NAMESPACE_URL, f"ilink-material:{query_id}"),
            ownerUserId=user_id,
            versionNo=1,
            sourceKind=SourceKind.CAMERA_IMAGE,
            state=SourceArtifactState.AVAILABLE,
            objectRef=str(object_ref),
            mimeType=str(mime_type),
            contentSha256=str(source_fingerprint),
            byteSize=byte_size,
            pageCount=1,
            createdAt=now,
        )
        PostgresUploadRepository(session=session).insert_artifact(
            session_id=query_id, artifact=artifact
        )
        payload_dump = candidate.model_dump(by_alias=True, mode="json")
        revision = RecognitionRevision(
            recognitionRevisionId=uuid5(
                NAMESPACE_URL, f"ilink-material-recognition:{query_id}"
            ),
            artifactVersionId=artifact.artifact_version_id,
            ownerUserId=user_id,
            revisionNo=1,
            recognitionKind=RecognitionKind.PRINTED_OCR,
            state=CandidateState.CANDIDATE,
            processorPolicyVersion=MATERIAL_INTAKE_POLICY_VERSION,
            processorRoute=self._settings.recognition_route,
            inputSha256=str(source_fingerprint),
            outputSha256=_canonical_sha256(payload_dump),
            candidatePayload=payload_dump,
            confidence=_mean_confidence(candidate.text_spans),
            expiresAt=now + MATERIAL_RECOGNITION_TTL,
            createdAt=now,
        )
        SQLRecognitionRepository(session).insert(revision)
        repository.release_transient_for_material(
            query_id=query_id, user_id=user_id, now=now
        )
        repository.event(
            aggregate_type="query",
            aggregate_id=str(query_id),
            user_id=user_id,
            event_type="material_intake_transferred",
            from_state="answered",
            to_state="answered",
            correlation_fingerprint=str(source_fingerprint),
            details={
                "artifactVersionId": str(artifact.artifact_version_id),
                "recognitionRevisionId": str(revision.recognition_revision_id),
                "reviewState": "pending_confirmation",
                "abilityImpact": "none",
                "publicCorpus": "excluded",
                "modelTraining": "excluded",
            },
            now=now,
        )
        reply = store_material_receipt_text()
        repository.save_command_receipt(
            command_id=command_id,
            command_type="store_material",
            user_id=user_id,
            request_fingerprint=request_fingerprint,
            result_payload={
                "replyText": reply,
                "artifactVersionId": str(artifact.artifact_version_id),
                "recognitionRevisionId": str(revision.recognition_revision_id),
            },
            now=now,
        )
        return reply


def _candidate_payload(
    *, recognized: dict[str, object], query_id: UUID, request_hash: str
) -> RecognitionCandidatePayload:
    raw_segments = recognized.get("segments")
    entries = [
        item
        for item in (raw_segments if isinstance(raw_segments, list) else [])
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    if not entries:
        raise ILinkConflict("material_text_missing")
    count = len(entries)
    spans: list[TextSpan] = []
    segments: list[PageSegment] = []
    for index, item in enumerate(entries):
        span_id = f"material-span-{index + 1}"
        segment_kind = str(item.get("segmentKind", "unknown"))
        confidence = _clamped_confidence(item.get("confidence"))
        box = BoundingBox(
            pageNo=1,
            x=0.0,
            y=index / (2 * count),
            width=1.0,
            height=1 / (4 * count),
        )
        spans.append(
            TextSpan(
                spanId=span_id,
                text=str(item["text"]).strip()[:100_000],
                box=box,
                confidence=confidence,
            )
        )
        segments.append(
            PageSegment(
                segmentId=f"material-seg-{index + 1}",
                segmentKind=(
                    segment_kind if segment_kind in _SEGMENT_KINDS else "unknown"
                ),
                box=box,
                textSpanRefs=(span_id,),
                confidence=confidence,
            )
        )
    return RecognitionCandidatePayload(
        kind=RecognitionKind.PRINTED_OCR,
        textSpans=tuple(spans),
        segments=tuple(segments),
        processorRequestIdHash=sha256(
            f"ilink-material:{query_id}".encode("utf-8")
        ).hexdigest(),
        processorOutputContract=str(
            recognized.get("contractVersion") or "ilink-query-recognition@v1"
        ),
        requestHash=request_hash,
    )


def _clamped_confidence(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(max(number, 0.0), 1.0)


def _mean_confidence(spans: tuple[TextSpan, ...]) -> float:
    return round(sum(span.confidence for span in spans) / len(spans), 6)


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
