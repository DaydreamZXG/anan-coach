from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class FrozenXingceQuestionContext:
    """One user-owned question loaded only from its immutable task bundle.

    The first query deliberately does not reference ``scoring_bundle``.  A
    second scoring query is issued only after the database-owned session state
    proves that the session is no longer active.  This keeps answer keys out of
    the answering-stage data path instead of relying on prompt filtering.
    """

    session_id: str
    state: str
    revision: int
    bundle_version_id: str
    question: dict[str, Any]
    material: dict[str, Any] | None
    checkpoint_answer: dict[str, Any] | None
    scoring: dict[str, Any] | None

    @property
    def server_stage(self) -> str:
        return "answering" if self.state == "active" else "submitted"


@dataclass(frozen=True, slots=True)
class FrozenXingceMaterialContext:
    session_id: str
    state: str
    revision: int
    bundle_version_id: str
    material: dict[str, Any]

    @property
    def server_stage(self) -> str:
        return "answering" if self.state == "active" else "submitted"


def load_frozen_question_context(
    session: Session,
    *,
    user_id: str,
    question_id: str,
    content_version_id: str,
) -> FrozenXingceQuestionContext | None:
    row = (
        session.execute(
            text(
                """
                select task_session.session_id::text as session_id,
                       task_session.state,
                       task_session.revision,
                       task_session.bundle_version_id,
                       frozen_question.item as question_payload,
                       frozen_material.item as material_payload,
                       (
                         select answer.item
                         from jsonb_array_elements(
                           coalesce(task_session.checkpoint -> 'answers', '[]'::jsonb)
                         ) answer(item)
                         where answer.item ->> 'questionId' = :question_id
                         limit 1
                       ) as checkpoint_answer
                  from learning.xingce_task_sessions task_session
                  cross join lateral jsonb_array_elements(
                    coalesce(task_session.safe_bundle -> 'questions', '[]'::jsonb)
                  ) frozen_question(item)
                  left join lateral jsonb_array_elements(
                    coalesce(task_session.safe_bundle -> 'materials', '[]'::jsonb)
                  ) frozen_material(item)
                    on frozen_material.item ->> 'materialId' =
                       frozen_question.item ->> 'materialId'
                 where task_session.user_id = cast(:user_id as uuid)
                   and frozen_question.item ->> 'questionId' = :question_id
                   and frozen_question.item ->> 'contentVersionId' = :content_version_id
                 order by case task_session.state when 'active' then 0 else 1 end,
                          task_session.updated_at desc,
                          task_session.session_id desc
                 limit 1
                """
            ),
            {
                "user_id": user_id,
                "question_id": question_id,
                "content_version_id": content_version_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None

    state = str(row["state"])
    scoring: dict[str, Any] | None = None
    if state in {"submitted", "settled"}:
        scoring_row = (
            session.execute(
                text(
                    """
                    select frozen_scoring.item as scoring_payload
                      from learning.xingce_task_sessions task_session
                      cross join lateral jsonb_array_elements(
                        coalesce(task_session.scoring_bundle -> 'questions', '[]'::jsonb)
                      ) frozen_scoring(item)
                     where task_session.user_id = cast(:user_id as uuid)
                       and task_session.session_id = cast(:session_id as uuid)
                       and task_session.state in ('submitted', 'settled')
                       and frozen_scoring.item ->> 'questionId' = :question_id
                       and frozen_scoring.item ->> 'contentVersionId' = :content_version_id
                     limit 1
                    """
                ),
                {
                    "user_id": user_id,
                    "session_id": str(row["session_id"]),
                    "question_id": question_id,
                    "content_version_id": content_version_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if scoring_row is not None and isinstance(scoring_row["scoring_payload"], dict):
            scoring = dict(scoring_row["scoring_payload"])

    question = row["question_payload"]
    if not isinstance(question, dict):
        return None
    material = row["material_payload"]
    checkpoint_answer = row["checkpoint_answer"]
    return FrozenXingceQuestionContext(
        session_id=str(row["session_id"]),
        state=state,
        revision=int(row["revision"]),
        bundle_version_id=str(row["bundle_version_id"]),
        question=dict(question),
        material=dict(material) if isinstance(material, dict) else None,
        checkpoint_answer=(
            dict(checkpoint_answer) if isinstance(checkpoint_answer, dict) else None
        ),
        scoring=scoring,
    )


def load_frozen_material_context(
    session: Session,
    *,
    user_id: str,
    material_id: str,
    linked_content_version_id: str,
) -> FrozenXingceMaterialContext | None:
    row = (
        session.execute(
            text(
                """
                select task_session.session_id::text as session_id,
                       task_session.state,
                       task_session.revision,
                       task_session.bundle_version_id,
                       frozen_material.item as material_payload
                  from learning.xingce_task_sessions task_session
                  cross join lateral jsonb_array_elements(
                    coalesce(task_session.safe_bundle -> 'materials', '[]'::jsonb)
                  ) frozen_material(item)
                 where task_session.user_id = cast(:user_id as uuid)
                   and frozen_material.item ->> 'materialId' = :material_id
                   and exists (
                     select 1
                     from jsonb_array_elements(
                       coalesce(task_session.safe_bundle -> 'questions', '[]'::jsonb)
                     ) frozen_question(item)
                     where frozen_question.item ->> 'materialId' = :material_id
                       and frozen_question.item ->> 'contentVersionId' =
                           :linked_content_version_id
                   )
                 order by case task_session.state when 'active' then 0 else 1 end,
                          task_session.updated_at desc,
                          task_session.session_id desc
                 limit 1
                """
            ),
            {
                "user_id": user_id,
                "material_id": material_id,
                "linked_content_version_id": linked_content_version_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not isinstance(row["material_payload"], dict):
        return None
    return FrozenXingceMaterialContext(
        session_id=str(row["session_id"]),
        state=str(row["state"]),
        revision=int(row["revision"]),
        bundle_version_id=str(row["bundle_version_id"]),
        material=dict(row["material_payload"]),
    )
