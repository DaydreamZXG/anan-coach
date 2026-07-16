from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.task_content.quality.repository import QuestionQualityRepository
from app.domains.task_content.quality.schemas import (
    QuestionQualityDecisionProjection,
    RecordQuestionQualityDecisionCommand,
)
from app.platform.outbox import OutboxRepository


class QuestionQualityDecisionError(RuntimeError):
    pass


class QuestionQualityDecisionService:
    """Internal moderation writer; intentionally not mounted as a public API."""

    def __init__(
        self,
        repository: QuestionQualityRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or QuestionQualityRepository()
        self._outbox = outbox or OutboxRepository()

    def record(
        self,
        session: Session,
        *,
        command_id: str,
        actor_ref: str,
        command: RecordQuestionQualityDecisionCommand,
        now: datetime | None = None,
    ) -> QuestionQualityDecisionProjection:
        recorded_at = _utc(now)
        actor = actor_ref.strip()
        if len(actor) < 8:
            raise QuestionQualityDecisionError("moderation actor reference is required")
        fingerprint = _fingerprint(command)
        self._repository.lock_question(session, question_id=command.question_id)
        replay = self._repository.command(session, command_id=command_id)
        if replay is not None:
            if (
                str(replay["actor_ref"]) != actor
                or str(replay["request_fingerprint"]) != fingerprint
            ):
                raise QuestionQualityDecisionError(
                    "question-quality command id was reused with different input"
                )
            row = self._repository.decision(
                session, decision_version_id=str(replay["decision_version_id"])
            )
            if row is None:
                raise QuestionQualityDecisionError(
                    "question-quality command lost its decision version"
                )
            return self._projection(session, row)

        content = self._repository.current_content(
            session, question_id=command.question_id
        )
        if content is None or str(content["content_version_id"]) != command.content_version_id:
            raise QuestionQualityDecisionError(
                "quality decision must bind the current published content version"
            )
        if str(content["status"]) not in {"active", "canonical"}:
            raise QuestionQualityDecisionError(
                "withdrawn content cannot be adjudicated back into circulation"
            )
        dispute_ids = [str(item) for item in command.dispute_ids]
        disputes = self._repository.disputes(
            session,
            dispute_ids=dispute_ids,
            question_id=command.question_id,
            content_version_id=command.content_version_id,
        )
        if len(disputes) != len(dispute_ids):
            raise QuestionQualityDecisionError(
                "every decision dispute must bind the same question content version"
            )

        current = self._repository.head(
            session,
            question_id=command.question_id,
            content_version_id=command.content_version_id,
        )
        if current is not None and str(current["decision"]) == "confirmed_invalid":
            raise QuestionQualityDecisionError(
                "confirmed invalid content is terminal; publish a corrected version"
            )
        decision_id = str(current["decision_id"]) if current else str(uuid4())
        decision_version_id = str(uuid4())
        version_no = int(current["version_no"]) + 1 if current else 1
        evidence_refs = [
            f"question-content-version:{command.content_version_id}",
            *[f"question-dispute:{item}" for item in sorted(dispute_ids)],
            *command.evidence_refs,
        ]
        evidence_refs = list(dict.fromkeys(evidence_refs))
        row = self._repository.insert_decision(
            session,
            values={
                "decision_version_id": decision_version_id,
                "decision_id": decision_id,
                "question_id": command.question_id,
                "content_version_id": command.content_version_id,
                "version_no": version_no,
                "decision": command.decision,
                "reason_code": command.reason_code,
                "rationale": command.rationale,
                "authority_version": command.authority_version,
                "actor_ref": actor,
                "evidence_refs": evidence_refs,
                "supersedes_decision_version_id": (
                    str(current["decision_version_id"]) if current else None
                ),
                "created_at": recorded_at,
            },
        )
        self._repository.bind_disputes(
            session,
            decision_version_id=decision_version_id,
            dispute_ids=sorted(dispute_ids),
            created_at=recorded_at,
        )
        self._repository.move_head(
            session,
            question_id=command.question_id,
            content_version_id=command.content_version_id,
            decision_id=decision_id,
            decision_version_id=decision_version_id,
            decision=command.decision,
            updated_at=recorded_at,
        )
        if command.decision == "confirmed_invalid":
            self._repository.invalidate_question(
                session, question_id=command.question_id
            )
        self._repository.insert_command(
            session,
            command_id=command_id,
            actor_ref=actor,
            request_fingerprint=fingerprint,
            decision_version_id=decision_version_id,
            created_at=recorded_at,
        )
        event_id = uuid4()
        self._outbox.append(
            session,
            event_id=event_id,
            deduplication_key=f"question-quality:{decision_version_id}",
            aggregate_type="question_content_quality",
            aggregate_id=decision_id,
            event_type="question-quality-decision-recorded@v1",
            payload={
                "eventId": str(event_id),
                "eventType": "question-quality-decision-recorded@v1",
                "aggregateId": decision_id,
                "aggregateRevision": version_no,
                "occurredAt": recorded_at.isoformat(),
                "producer": "task_content",
                "subjectRef": sha256(
                    f"question-content:{command.question_id}".encode("utf-8")
                ).hexdigest(),
                "trackId": "xingce",
                "correlationId": command_id,
                "causationId": command_id,
                "payload": {
                    "questionId": command.question_id,
                    "contentVersionId": command.content_version_id,
                    "decision": command.decision,
                    "reasonCode": command.reason_code,
                    "resourceVersionRefs": [
                        f"question-quality-decision:{decision_version_id}",
                        *evidence_refs,
                    ],
                },
                "schemaVersion": 1,
            },
        )
        return self._projection(session, row)

    def _projection(
        self, session: Session, row: RowMapping
    ) -> QuestionQualityDecisionProjection:
        return QuestionQualityDecisionProjection(
            decisionVersionId=str(row["decision_version_id"]),
            decisionId=str(row["decision_id"]),
            questionId=str(row["question_id"]),
            contentVersionId=str(row["content_version_id"]),
            versionNo=int(row["version_no"]),
            decision=str(row["decision"]),
            reasonCode=str(row["reason_code"]),
            authorityVersion=str(row["authority_version"]),
            disputeIds=self._repository.decision_dispute_ids(
                session, decision_version_id=str(row["decision_version_id"])
            ),
            evidenceRefs=list(row["evidence_refs"]),
            createdAt=row["created_at"],
        )


def _fingerprint(command: RecordQuestionQualityDecisionCommand) -> str:
    payload = json.dumps(
        command.model_dump(by_alias=True, mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("question-quality timestamps require a timezone")
    return current.astimezone(UTC)
