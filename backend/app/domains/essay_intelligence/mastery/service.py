from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Literal
from uuid import uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.essay_intelligence.learning_schemas import (
    EssayLearningArtifactVersion,
    ReviewAcknowledgementPayload,
)
from app.domains.essay_intelligence.mastery.repository import EssayMasteryRepository
from app.domains.essay_intelligence.mastery.schemas import (
    EssayMasteryEvidenceRef,
    EssayMasteryProjection,
    EssayMasteryStateVersion,
)
from app.domains.essay_intelligence.review_contracts import _fingerprint
from app.platform.outbox import OutboxRepository


POLICY_VERSION = "essay-mastery@v1"


class EssayMasteryContractError(RuntimeError):
    pass


class EssayMasteryService:
    def __init__(
        self,
        *,
        repository: EssayMasteryRepository | None = None,
        outbox: OutboxRepository | None = None,
    ) -> None:
        self._repository = repository or EssayMasteryRepository()
        self._outbox = outbox or OutboxRepository()

    def reconcile_finding_decisions(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        artifact: EssayLearningArtifactVersion,
        review: RowMapping,
        payload: ReviewAcknowledgementPayload,
        recorded_at: datetime,
    ) -> list[EssayMasteryStateVersion]:
        decisions = {item.finding_id: item.decision for item in payload.decisions}
        versions: list[EssayMasteryStateVersion] = []
        for finding_id, decision in sorted(decisions.items()):
            current = self._repository.current_for_finding(
                session,
                user_id=user_id,
                source_review_version_id=str(review["review_version_id"]),
                finding_id=finding_id,
                for_update=True,
            )
            if decision != "accepted" and current is None:
                continue
            state: Literal["rewrite_due", "transfer_due", "stable_mastered", "withdrawn"]
            due_at: datetime | None
            if decision == "accepted":
                state = (
                    str(current["state"])
                    if current is not None and current["state"] != "withdrawn"
                    else "rewrite_due"
                )  # type: ignore[assignment]
                due_at = current["due_at"] if current is not None else recorded_at
            else:
                if current is not None and current["state"] == "withdrawn":
                    continue
                state = "withdrawn"
                due_at = None
            versions.append(
                self._append(
                    session,
                    user_id=user_id,
                    command_id=f"{command_id}:{finding_id}",
                    finding_id=finding_id,
                    state=state,
                    due_at=due_at,
                    source_task_version_id=str(review["task_version_id"]),
                    source_attempt_id=str(review["attempt_id"]),
                    source_answer_version_id=str(review["answer_version_id"]),
                    source_review_version_id=str(review["review_version_id"]),
                    decision_artifact_version_id=str(artifact.artifact_version_id),
                    rewrite_version_id=(
                        str(current["rewrite_version_id"])
                        if current is not None and current["rewrite_version_id"]
                        else None
                    ),
                    transfer_verification_version_id=(
                        str(current["transfer_verification_version_id"])
                        if current is not None
                        and current["transfer_verification_version_id"]
                        else None
                    ),
                    current=current,
                    recorded_at=recorded_at,
                    transition_role="finding_decision_reconciled",
                )
            )
        return versions

    def advance_rewrite(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        decision_artifact_version_id: str,
        resolution_by_finding: dict[str, Literal["resolved", "unresolved"]],
        rewrite_version_id: str,
        recorded_at: datetime,
    ) -> list[EssayMasteryStateVersion]:
        current_rows = self._repository.current_for_decision(
            session,
            user_id=user_id,
            decision_artifact_version_id=decision_artifact_version_id,
            for_update=True,
        )
        active = {str(item["finding_id"]): item for item in current_rows if item["state"] != "withdrawn"}
        if not active or set(active) != set(resolution_by_finding):
            raise EssayMasteryContractError(
                "Rewrite resolutions must exactly cover current accepted finding mastery"
            )
        versions: list[EssayMasteryStateVersion] = []
        for finding_id, current in active.items():
            resolved = resolution_by_finding[finding_id] == "resolved"
            versions.append(
                self._append(
                    session,
                    user_id=user_id,
                    command_id=f"{command_id}:{finding_id}",
                    finding_id=finding_id,
                    state="transfer_due" if resolved else "rewrite_due",
                    due_at=recorded_at,
                    source_task_version_id=str(current["source_task_version_id"]),
                    source_attempt_id=str(current["source_attempt_id"]),
                    source_answer_version_id=str(current["source_answer_version_id"]),
                    source_review_version_id=str(current["source_review_version_id"]),
                    decision_artifact_version_id=decision_artifact_version_id,
                    rewrite_version_id=rewrite_version_id,
                    transfer_verification_version_id=(
                        str(current["transfer_verification_version_id"])
                        if current["transfer_verification_version_id"]
                        else None
                    ),
                    current=current,
                    recorded_at=recorded_at,
                    transition_role="rewrite_resolved" if resolved else "rewrite_unresolved",
                )
            )
        return versions

    def advance_transfer(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        decision_artifact_version_id: str,
        status: Literal["verified", "not_verified", "inconclusive"],
        transfer_verification_version_id: str,
        recorded_at: datetime,
    ) -> list[EssayMasteryStateVersion]:
        current_rows = self._repository.current_for_decision(
            session,
            user_id=user_id,
            decision_artifact_version_id=decision_artifact_version_id,
            for_update=True,
        )
        active = [item for item in current_rows if item["state"] != "withdrawn"]
        if not active or any(item["state"] != "transfer_due" for item in active):
            raise EssayMasteryContractError(
                "Transfer verification requires every accepted finding to be transfer_due"
            )
        state_by_status = {
            "verified": "stable_mastered",
            "not_verified": "rewrite_due",
            "inconclusive": "transfer_due",
        }
        versions: list[EssayMasteryStateVersion] = []
        for current in active:
            versions.append(
                self._append(
                    session,
                    user_id=user_id,
                    command_id=f"{command_id}:{current['finding_id']}",
                    finding_id=str(current["finding_id"]),
                    state=state_by_status[status],  # type: ignore[arg-type]
                    due_at=None if status == "verified" else recorded_at,
                    source_task_version_id=str(current["source_task_version_id"]),
                    source_attempt_id=str(current["source_attempt_id"]),
                    source_answer_version_id=str(current["source_answer_version_id"]),
                    source_review_version_id=str(current["source_review_version_id"]),
                    decision_artifact_version_id=decision_artifact_version_id,
                    rewrite_version_id=str(current["rewrite_version_id"]),
                    transfer_verification_version_id=transfer_verification_version_id,
                    current=current,
                    recorded_at=recorded_at,
                    transition_role=f"transfer_{status}",
                )
            )
        return versions

    def projection(self, session: Session, *, user_id: str) -> EssayMasteryProjection:
        rows = self._repository.current_for_user(session, user_id=user_id)
        items = [_from_row(item) for item in rows if item["state"] != "withdrawn"]
        now = datetime.now(UTC)
        return EssayMasteryProjection(
            contract_version=POLICY_VERSION,
            due_count=sum(1 for item in items if item.due_at is not None and item.due_at <= now),
            stable_count=sum(1 for item in items if item.state == "stable_mastered"),
            items=items,
        )

    def versions_for_command(
        self,
        session: Session,
        *,
        user_id: str,
        command_prefix: str,
    ) -> list[EssayMasteryStateVersion]:
        return [
            _from_row(item)
            for item in self._repository.by_command_prefix(
                session,
                user_id=user_id,
                command_prefix=command_prefix,
            )
        ]

    def _append(
        self,
        session: Session,
        *,
        user_id: str,
        command_id: str,
        finding_id: str,
        state: Literal["rewrite_due", "transfer_due", "stable_mastered", "withdrawn"],
        due_at: datetime | None,
        source_task_version_id: str,
        source_attempt_id: str,
        source_answer_version_id: str,
        source_review_version_id: str,
        decision_artifact_version_id: str,
        rewrite_version_id: str | None,
        transfer_verification_version_id: str | None,
        current: RowMapping | None,
        recorded_at: datetime,
        transition_role: str,
    ) -> EssayMasteryStateVersion:
        replay = self._repository.by_command(
            session,
            user_id=user_id,
            command_id=command_id,
        )
        evidence = _evidence_refs(
            source_attempt_id=source_attempt_id,
            source_answer_version_id=source_answer_version_id,
            source_review_version_id=source_review_version_id,
            decision_artifact_version_id=decision_artifact_version_id,
            rewrite_version_id=rewrite_version_id,
            transfer_verification_version_id=transfer_verification_version_id,
            transition_role=transition_role,
        )
        fingerprint = _fingerprint(
            {
                "contractVersion": POLICY_VERSION,
                "userId": user_id,
                "findingId": finding_id,
                "state": state,
                "dueAt": due_at.isoformat() if due_at else None,
                "evidenceRefs": evidence,
            }
        )
        if replay is not None:
            if replay["input_fingerprint"] != fingerprint:
                raise EssayMasteryContractError(
                    "Mastery idempotency key belongs to different evidence"
                )
            return _from_row(replay)
        mastery_id = str(current["mastery_id"]) if current else str(uuid4())
        row = self._repository.insert(
            session,
            values={
                "mastery_version_id": str(uuid4()),
                "mastery_id": mastery_id,
                "command_id": command_id,
                "user_id": user_id,
                "finding_id": finding_id,
                "version_no": int(current["version_no"]) + 1 if current else 1,
                "state": state,
                "due_at": due_at,
                "source_task_version_id": source_task_version_id,
                "source_attempt_id": source_attempt_id,
                "source_answer_version_id": source_answer_version_id,
                "source_review_version_id": source_review_version_id,
                "finding_decision_artifact_version_id": decision_artifact_version_id,
                "rewrite_version_id": rewrite_version_id,
                "transfer_verification_version_id": transfer_verification_version_id,
                "evidence_refs": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "policy_version": POLICY_VERSION,
                "input_fingerprint": fingerprint,
                "supersedes_mastery_version_id": (
                    str(current["mastery_version_id"]) if current else None
                ),
                "created_at": recorded_at,
            },
        )
        version = _from_row(row)
        self._outbox.append(
            session,
            deduplication_key=f"essay-mastery:{version.mastery_version_id}",
            aggregate_type="essay_mastery",
            aggregate_id=str(version.mastery_id),
            event_type="essay_mastery.state_changed",
            payload={
                "contractVersion": POLICY_VERSION,
                "masteryVersionId": str(version.mastery_version_id),
                "masteryId": str(version.mastery_id),
                "userId": user_id,
                "findingId": finding_id,
                "state": state,
                "dueAt": version.due_at.isoformat() if version.due_at else None,
                "inputFingerprint": fingerprint,
            },
        )
        return version


def _evidence_refs(
    *,
    source_attempt_id: str,
    source_answer_version_id: str,
    source_review_version_id: str,
    decision_artifact_version_id: str,
    rewrite_version_id: str | None,
    transfer_verification_version_id: str | None,
    transition_role: str,
) -> list[dict[str, str]]:
    refs = [
        _ref("essay_attempt", source_attempt_id, "source_attempt"),
        _ref("essay_answer_version", source_answer_version_id, "source_original"),
        _ref("essay_review_version", source_review_version_id, "grounded_review"),
        _ref(
            "essay_finding_decision",
            decision_artifact_version_id,
            "learner_decision",
        ),
    ]
    if rewrite_version_id:
        refs.append(_ref("essay_rewrite_version", rewrite_version_id, transition_role))
    if transfer_verification_version_id:
        refs.append(
            _ref(
                "essay_transfer_verification",
                transfer_verification_version_id,
                transition_role,
            )
        )
    return refs


def _ref(entity_type: str, entity_id: str, role: str) -> dict[str, str]:
    return EssayMasteryEvidenceRef(
        entity_type=entity_type,
        entity_id=entity_id,
        version_id=entity_id,
        role=role,
    ).model_dump(by_alias=True, mode="json")


def _from_row(row: RowMapping) -> EssayMasteryStateVersion:
    return EssayMasteryStateVersion(
        mastery_version_id=row["mastery_version_id"],
        mastery_id=row["mastery_id"],
        finding_id=row["finding_id"],
        version_no=int(row["version_no"]),
        state=row["state"],
        due_at=row["due_at"],
        source_task_version_id=row["source_task_version_id"],
        source_attempt_id=row["source_attempt_id"],
        source_answer_version_id=row["source_answer_version_id"],
        source_review_version_id=row["source_review_version_id"],
        finding_decision_artifact_version_id=row[
            "finding_decision_artifact_version_id"
        ],
        rewrite_version_id=row["rewrite_version_id"],
        transfer_verification_version_id=row["transfer_verification_version_id"],
        evidence_refs=row["evidence_refs"],
        policy_version=row["policy_version"],
        input_fingerprint=row["input_fingerprint"],
        supersedes_mastery_version_id=row["supersedes_mastery_version_id"],
        created_at=row["created_at"],
    )
