from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.external_ingestion.answering.models import ExternalAIAnswerVersion


class ExternalAIAnswerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock(self, *, answer_id: UUID) -> None:
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"external-ai-answer:{answer_id}"},
        )

    def by_version(self, *, answer_version_id: UUID) -> ExternalAIAnswerVersion | None:
        payload = self._session.execute(
            text(
                "select contract_payload from learning.external_ai_answer_versions "
                "where answer_version_id = :answer_version_id"
            ),
            {"answer_version_id": str(answer_version_id)},
        ).scalar_one_or_none()
        return ExternalAIAnswerVersion.model_validate(payload) if payload else None

    def head(self, *, answer_id: UUID, for_update: bool = False) -> ExternalAIAnswerVersion | None:
        suffix = " for update" if for_update else ""
        payload = self._session.execute(
            text(
                "select contract_payload from learning.external_ai_answer_versions "
                "where answer_id = :answer_id order by version_no desc limit 1" + suffix
            ),
            {"answer_id": str(answer_id)},
        ).scalar_one_or_none()
        return ExternalAIAnswerVersion.model_validate(payload) if payload else None

    def current_for_unit(
        self, *, owner_user_id: UUID, import_id: UUID, question_unit_id: str
    ) -> ExternalAIAnswerVersion | None:
        payload = self._session.execute(
            text(
                """
                select contract_payload
                from learning.external_ai_answer_versions
                where owner_user_id = :owner_user_id
                  and import_id = :import_id
                  and question_unit_id = :question_unit_id
                order by version_no desc limit 1
                """
            ),
            {
                "owner_user_id": str(owner_user_id),
                "import_id": str(import_id),
                "question_unit_id": question_unit_id,
            },
        ).scalar_one_or_none()
        return ExternalAIAnswerVersion.model_validate(payload) if payload else None

    def insert(self, version: ExternalAIAnswerVersion) -> None:
        payload = version.model_dump(by_alias=True, mode="json")
        self._session.execute(
            text(
                """
                insert into learning.external_ai_answer_versions (
                  answer_version_id, answer_id, import_id, artifact_version_id,
                  recognition_revision_id, question_unit_id, owner_user_id,
                  version_no, state, learning_track, input_fingerprint,
                  output_fingerprint, answer_payload, provider, model,
                  prompt_version, schema_version, reservation_id,
                  reservation_source, job_id, model_call_id,
                  provider_request_id_fingerprint, failure_code,
                  supersedes_answer_version_id, contract_payload, created_at
                ) values (
                  :answer_version_id, :answer_id, :import_id, :artifact_version_id,
                  :recognition_revision_id, :question_unit_id, :owner_user_id,
                  :version_no, :state, :learning_track, :input_fingerprint,
                  :output_fingerprint, cast(:answer_payload as jsonb), :provider,
                  :model, :prompt_version, :schema_version, :reservation_id,
                  :reservation_source, :job_id, :model_call_id,
                  :provider_request_id_fingerprint, :failure_code,
                  :supersedes_answer_version_id, cast(:contract_payload as jsonb),
                  :created_at
                )
                """
            ),
            {
                "answer_version_id": str(version.answer_version_id),
                "answer_id": str(version.answer_id),
                "import_id": str(version.import_id),
                "artifact_version_id": str(version.artifact_version_id),
                "recognition_revision_id": str(version.recognition_revision_id),
                "question_unit_id": version.question_unit_id,
                "owner_user_id": str(version.owner_user_id),
                "version_no": version.version_no,
                "state": version.state,
                "learning_track": version.learning_track.value,
                "input_fingerprint": version.input_fingerprint,
                "output_fingerprint": version.output_fingerprint,
                "answer_payload": (
                    json.dumps(version.answer_payload.model_dump(by_alias=True, mode="json"), ensure_ascii=False, sort_keys=True)
                    if version.answer_payload else None
                ),
                "provider": version.provider,
                "model": version.model,
                "prompt_version": version.prompt_version,
                "schema_version": version.schema_version,
                "reservation_id": str(version.reservation_id) if version.reservation_id else None,
                "reservation_source": version.reservation_source,
                "job_id": str(version.job_id) if version.job_id else None,
                "model_call_id": str(version.model_call_id) if version.model_call_id else None,
                "provider_request_id_fingerprint": version.provider_request_id_fingerprint,
                "failure_code": version.failure_code,
                "supersedes_answer_version_id": (
                    str(version.supersedes_answer_version_id)
                    if version.supersedes_answer_version_id else None
                ),
                "contract_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "created_at": version.created_at,
            },
        )
