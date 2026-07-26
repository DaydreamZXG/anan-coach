from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app.domains.ai.agent_runtime.contracts import AgentDefinition


class CauseAgentRepository:
    def ensure_definition(
        self,
        session: Session,
        *,
        definition: AgentDefinition,
        created_at: datetime,
    ) -> None:
        payload = definition.model_dump(by_alias=True, mode="json")
        values = {
            "definition_release_id": definition.definition_release_id,
            "agent_key": definition.agent_key,
            "contract_version": definition.contract_version,
            "prompt_version": definition.prompt_version,
            "schema_version": definition.schema_version,
            "behavior_version": definition.behavior_version,
            "completion_condition_version": definition.completion_condition_version,
            "tool_policy_version": definition.tool_policy_version,
            "toolset_snapshot": _json(payload["tools"]),
            "budget_snapshot": _json(payload["budget"]),
            "definition_sha256": definition.fingerprint,
            "created_at": created_at,
        }
        row = session.execute(
            text(
                """
                insert into ai_audit.agent_definition_releases (
                  definition_release_id, agent_key, contract_version, prompt_version,
                  schema_version, behavior_version, completion_condition_version,
                  tool_policy_version, toolset_snapshot, budget_snapshot,
                  definition_sha256, created_at
                ) values (
                  :definition_release_id, :agent_key, :contract_version, :prompt_version,
                  :schema_version, :behavior_version, :completion_condition_version,
                  :tool_policy_version, cast(:toolset_snapshot as jsonb),
                  cast(:budget_snapshot as jsonb), :definition_sha256, :created_at
                ) on conflict (definition_release_id) do nothing
                returning definition_sha256
                """
            ),
            values,
        ).scalar_one_or_none()
        if row is None:
            existing = session.execute(
                text(
                    "select definition_sha256 from ai_audit.agent_definition_releases "
                    "where definition_release_id = :definition_release_id"
                ),
                {"definition_release_id": definition.definition_release_id},
            ).scalar_one()
            if str(existing) != definition.fingerprint:
                raise ValueError("agent definition release refers to different immutable input")

    def insert_run_version(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into ai_audit.agent_run_versions (
                      run_version_id, run_id, version_no, user_id, review_id, session_id,
                      reflection_version_id, definition_release_id, state, job_id,
                      input_version_refs, input_fingerprint, provider, model,
                      provider_release_id, prompt_version, schema_version,
                      toolset_snapshot, budget_snapshot, progress_fingerprint,
                      model_call_count, tool_call_count, consecutive_no_progress,
                      followup_round, termination_reason, candidate_version_id,
                      supersedes_run_version_id, created_at
                    ) values (
                      cast(:run_version_id as uuid), cast(:run_id as uuid), :version_no,
                      cast(:user_id as uuid), cast(:review_id as uuid),
                      cast(:session_id as uuid), cast(:reflection_version_id as uuid),
                      :definition_release_id, :state, cast(:job_id as uuid),
                      cast(:input_version_refs as jsonb), :input_fingerprint,
                      :provider, :model, :provider_release_id, :prompt_version,
                      :schema_version, cast(:toolset_snapshot as jsonb),
                      cast(:budget_snapshot as jsonb), :progress_fingerprint,
                      :model_call_count, :tool_call_count, :consecutive_no_progress,
                      :followup_round, :termination_reason,
                      cast(:candidate_version_id as uuid),
                      cast(:supersedes_run_version_id as uuid), :created_at
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def current_run(
        self,
        session: Session,
        *,
        run_id: str,
        user_id: str | None = None,
        for_update: bool = False,
    ) -> RowMapping | None:
        owner = " and run.user_id = cast(:user_id as uuid)" if user_id else ""
        suffix = " for update of run" if for_update else ""
        params: dict[str, object] = {"run_id": run_id}
        if user_id:
            params["user_id"] = user_id
        return (
            session.execute(
                text(
                    """
                    select run.* from ai_audit.agent_run_versions run
                    where run.run_id = cast(:run_id as uuid)
                    """
                    + owner
                    + """
                      and not exists (
                        select 1 from ai_audit.agent_run_versions successor
                        where successor.supersedes_run_version_id = run.run_version_id
                      )
                    """
                    + suffix
                ),
                params,
            )
            .mappings()
            .one_or_none()
        )

    def current_run_for_review(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select run.* from ai_audit.agent_run_versions run
                    where run.user_id = cast(:user_id as uuid)
                      and run.review_id = cast(:review_id as uuid)
                      and not exists (
                        select 1 from ai_audit.agent_run_versions successor
                        where successor.supersedes_run_version_id = run.run_version_id
                      )
                    order by run.created_at desc limit 1
                    """
                ),
                {"user_id": user_id, "review_id": review_id},
            )
            .mappings()
            .one_or_none()
        )

    def insert_tool_event(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> None:
        session.execute(
            text(
                """
                insert into ai_audit.agent_tool_call_events (
                  event_id, call_id, run_id, run_version_id, user_id, review_id,
                  session_id, sequence_no, event_type, tool_name, tool_schema_version,
                  tool_behavior_version, permission_policy_version, input_fingerprint,
                  output_fingerprint, progress_fingerprint, error_code, occurred_at
                ) values (
                  cast(:event_id as uuid), cast(:call_id as uuid), cast(:run_id as uuid),
                  cast(:run_version_id as uuid), cast(:user_id as uuid),
                  cast(:review_id as uuid), cast(:session_id as uuid), :sequence_no,
                  :event_type, :tool_name, :tool_schema_version,
                  :tool_behavior_version, :permission_policy_version,
                  :input_fingerprint, :output_fingerprint, :progress_fingerprint,
                  :error_code, :occurred_at
                )
                """
            ),
            values,
        )

    def insert_followup(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.xingce_cause_followup_versions (
                      followup_version_id, command_id, user_id, review_id, session_id,
                      run_id, candidate_version_id, version_no, question, answer,
                      payload_sha256, created_at
                    ) values (
                      cast(:followup_version_id as uuid), :command_id,
                      cast(:user_id as uuid), cast(:review_id as uuid),
                      cast(:session_id as uuid), cast(:run_id as uuid),
                      cast(:candidate_version_id as uuid), :version_no,
                      :question, :answer, :payload_sha256, :created_at
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def latest_followup_answer(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
    ) -> str | None:
        value = session.execute(
            text(
                """
                select answer from learning.xingce_cause_followup_versions
                where user_id = cast(:user_id as uuid)
                  and review_id = cast(:review_id as uuid)
                order by version_no desc limit 1
                """
            ),
            {"user_id": user_id, "review_id": review_id},
        ).scalar_one_or_none()
        return str(value) if value is not None else None

    def insert_confirmed_cause(
        self,
        session: Session,
        *,
        values: dict[str, Any],
    ) -> RowMapping:
        return (
            session.execute(
                text(
                    """
                    insert into learning.xingce_confirmed_cause_versions (
                      confirmed_cause_version_id, confirmed_cause_id, command_id,
                      user_id, review_id, session_id, decision_version_id,
                      candidate_version_id, version_no, source_kind, cause_code,
                      replacement_code, cause_text, evidence_refs,
                      supersedes_confirmed_cause_version_id, created_at
                    ) values (
                      cast(:confirmed_cause_version_id as uuid),
                      cast(:confirmed_cause_id as uuid), :command_id,
                      cast(:user_id as uuid), cast(:review_id as uuid),
                      cast(:session_id as uuid), cast(:decision_version_id as uuid),
                      cast(:candidate_version_id as uuid), :version_no, :source_kind,
                      :cause_code, :replacement_code, :cause_text,
                      cast(:evidence_refs as jsonb),
                      cast(:supersedes_confirmed_cause_version_id as uuid), :created_at
                    ) returning *
                    """
                ),
                values,
            )
            .mappings()
            .one()
        )

    def current_confirmed_cause(
        self,
        session: Session,
        *,
        user_id: str,
        review_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select cause.*
                    from learning.xingce_confirmed_cause_versions cause
                    join learning.xingce_attempt_review_versions review
                      on review.cause_decision_version_id = cause.decision_version_id
                     and review.user_id = cause.user_id
                     and review.review_id = cause.review_id
                     and review.session_id = cause.session_id
                    where cause.user_id = cast(:user_id as uuid)
                      and cause.review_id = cast(:review_id as uuid)
                      and not exists (
                        select 1 from learning.xingce_attempt_review_versions successor
                        where successor.supersedes_review_version_id = review.review_version_id
                      )
                    """
                ),
                {"user_id": user_id, "review_id": review_id},
            )
            .mappings()
            .one_or_none()
        )

    def attempt_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        rows = session.execute(
            text(
                """
                select assessment.question_id, assessment.result_code,
                       assessment.result_snapshot, answer.payload as answer_payload,
                       assessment.assessment_id, assessment.content_version_id,
                       exists (
                         select 1
                         from learning.xingce_question_disputes dispute
                         where dispute.user_id = assessment.user_id
                           and dispute.question_id = assessment.question_id
                           and not exists (
                             select 1
                             from product.question_quality_heads head
                             where head.question_id = dispute.question_id
                               and head.content_version_id = dispute.content_version_id
                               and head.decision = 'confirmed_valid'
                           )
                       ) as disputed
                from learning.xingce_assessments assessment
                join learning.xingce_answer_versions answer
                  on answer.answer_version_id = assessment.answer_version_id
                 and answer.user_id = assessment.user_id
                 and answer.session_id = assessment.session_id
                 and answer.question_id = assessment.question_id
                where assessment.user_id = cast(:user_id as uuid)
                  and assessment.session_id = cast(:session_id as uuid)
                order by assessment.created_at, assessment.question_id
                """
            ),
            {"user_id": user_id, "session_id": session_id},
        ).mappings().all()
        return [dict(row) for row in rows]

    def reflection_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        reflection_version_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    """
                    select * from learning.xingce_reflection_versions
                    where user_id = cast(:user_id as uuid)
                      and reflection_version_id = cast(:reflection_version_id as uuid)
                    """
                ),
                {
                    "user_id": user_id,
                    "reflection_version_id": reflection_version_id,
                },
            )
            .mappings()
            .one_or_none()
        )

    def session_bundle(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select safe_bundle, scoring_bundle from learning.xingce_task_sessions "
                    "where user_id = cast(:user_id as uuid) "
                    "and session_id = cast(:session_id as uuid)"
                ),
                {"user_id": user_id, "session_id": session_id},
            )
            .mappings()
            .one_or_none()
        )

    def session_checkpoint(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> RowMapping | None:
        return (
            session.execute(
                text(
                    "select checkpoint from learning.xingce_task_sessions "
                    "where user_id = cast(:user_id as uuid) "
                    "and session_id = cast(:session_id as uuid)"
                ),
                {"user_id": user_id, "session_id": session_id},
            )
            .mappings()
            .one_or_none()
        )

    def ink_formula_evidence(
        self,
        session: Session,
        *,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        rows = session.execute(
            text(
                """
                select formula.question_id, formula.formula_id,
                       formula.formula_version_id, formula.state, formula.latex,
                       formula.source, formula.version_no, formula.created_at,
                       formula.confirmed_at
                from learning.math_formula_versions formula
                where formula.owner_user_id = cast(:user_id as uuid)
                  and formula.session_id = cast(:session_id as uuid)
                  and formula.state in ('candidate', 'confirmed')
                  and not exists (
                    select 1 from learning.math_formula_versions successor
                    where successor.supersedes_formula_version_id =
                          formula.formula_version_id
                  )
                order by formula.question_id, formula.created_at,
                         formula.formula_version_id
                """
            ),
            {"user_id": user_id, "session_id": session_id},
        ).mappings().all()
        return [dict(row) for row in rows]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
