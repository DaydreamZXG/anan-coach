from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class LearnerProfileRecord:
    target_exam: str | None
    target_region: str | None
    position_group: str | None
    daily_minutes: int | None
    education: str | None = None
    major: str | None = None
    fresh_graduate: bool | None = None
    political_status: str | None = None
    grassroots_years: int | None = None
    study_mode: str | None = None
    target_position_id: str | None = None
    target_score: float | None = None
    target_position_data_version: str | None = None
    baseline_source: str | None = None
    baseline_xingce_score: float | None = None
    baseline_shenlun_score: float | None = None
    baseline_weak_modules: list[str] | None = None
    baseline_module_snapshot: dict[str, Any] | None = None
    onboarding_complete: bool = False


@dataclass(frozen=True)
class LearnerMemorySummaryRecord:
    practice_answers: int
    wrong_answers: int
    essay_attempts: int


@dataclass(frozen=True)
class LearnerMemoryRecord:
    profile: LearnerProfileRecord
    summary: LearnerMemorySummaryRecord


@dataclass(frozen=True)
class LearnerWorkbenchObjectRecord:
    id: str
    type: str
    title: str
    subtitle: str
    source_count: int
    payload: dict[str, object]


@dataclass(frozen=True)
class LearnerWorkbenchSectionRecord:
    id: str
    title: str
    objects: list[LearnerWorkbenchObjectRecord]


class LearnerMemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_memory(self, *, user_id: str) -> LearnerMemoryRecord:
        self.ensure_profile(user_id=user_id)
        profile = (
            self._session.execute(
                text(
                    """
                select
                  target_exam,
                  target_region,
                  position_group,
                  daily_minutes,
                  education,
                  major,
                  fresh_graduate,
                  political_status,
                  grassroots_years,
                  study_mode,
                  current_target.target_position_id,
                  current_target.target_score,
                  current_target.target_position_data_version,
                  baseline_source,
                  baseline_xingce_score,
                  baseline_shenlun_score,
                  baseline_weak_modules,
                  baseline_module_snapshot,
                  onboarding_complete
                from learner.profiles profile
                left join learner.current_profile_position_projections current_target
                  on current_target.user_id = profile.user_id
                where profile.user_id = cast(:user_id as uuid)
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .one()
        )
        summary = (
            self._session.execute(
                text(
                    """
                select
                  (
                    select count(*)
                    from learner.practice_answers
                    where user_id = cast(:user_id as uuid)
                  ) as practice_answers,
                  (
                    select count(*)
                    from learner.practice_answers
                    where user_id = cast(:user_id as uuid)
                      and correct = false
                  ) as wrong_answers,
                  (
                    select count(*)
                    from learner.essay_attempts
                    where learner_key = :learner_key
                  ) as essay_attempts
                """
                ),
                {"user_id": user_id, "learner_key": user_id},
            )
            .mappings()
            .one()
        )
        return LearnerMemoryRecord(
            profile=LearnerProfileRecord(
                target_exam=profile["target_exam"],
                target_region=profile["target_region"],
                position_group=profile["position_group"],
                daily_minutes=profile["daily_minutes"],
                education=profile["education"],
                major=profile["major"],
                fresh_graduate=profile["fresh_graduate"],
                political_status=profile["political_status"],
                grassroots_years=profile["grassroots_years"],
                study_mode=profile["study_mode"],
                target_position_id=(
                    str(profile["target_position_id"])
                    if profile["target_position_id"] is not None
                    else None
                ),
                target_score=(
                    float(profile["target_score"]) if profile["target_score"] is not None else None
                ),
                target_position_data_version=profile["target_position_data_version"],
                baseline_source=profile["baseline_source"],
                baseline_xingce_score=(
                    float(profile["baseline_xingce_score"])
                    if profile["baseline_xingce_score"] is not None
                    else None
                ),
                baseline_shenlun_score=(
                    float(profile["baseline_shenlun_score"])
                    if profile["baseline_shenlun_score"] is not None
                    else None
                ),
                baseline_weak_modules=list(profile["baseline_weak_modules"] or []),
                baseline_module_snapshot=profile["baseline_module_snapshot"],
                onboarding_complete=bool(profile["onboarding_complete"]),
            ),
            summary=LearnerMemorySummaryRecord(
                practice_answers=int(summary["practice_answers"]),
                wrong_answers=int(summary["wrong_answers"]),
                essay_attempts=int(summary["essay_attempts"]),
            ),
        )

    def update_profile(
        self,
        *,
        user_id: str,
        target_exam: str,
        target_region: str,
        position_group: str,
        daily_minutes: int,
    ) -> LearnerProfileRecord:
        del user_id, target_exam, target_region, position_group, daily_minutes
        raise RuntimeError("v1 learner profile mutation is retired")

    def ensure_profile(self, *, user_id: str) -> None:
        self._session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id,
                  created_at,
                  updated_at
                ) values (
                  cast(:user_id as uuid),
                  now(),
                  now()
                )
                on conflict (user_id) do nothing
                """
            ),
            {"user_id": user_id},
        )
        self._session.commit()

    def record_practice_answer(
        self,
        *,
        user_id: str,
        task_id: str,
        question_id: str,
        selected_answer: str,
        correct_answer: str | None,
        correct: bool,
    ) -> None:
        self._session.execute(
            text(
                """
                insert into learner.practice_answers (
                  answer_id,
                  user_id,
                  task_id,
                  question_id,
                  selected_answer,
                  correct_answer,
                  correct,
                  answered_at
                ) values (
                  gen_random_uuid(),
                  cast(:user_id as uuid),
                  :task_id,
                  :question_id,
                  :selected_answer,
                  :correct_answer,
                  :correct,
                  now()
                )
                """
            ),
            {
                "user_id": user_id,
                "task_id": task_id,
                "question_id": question_id,
                "selected_answer": selected_answer,
                "correct_answer": correct_answer,
                "correct": correct,
            },
        )
        self._session.commit()

    def list_completed_task_ids_for_date(
        self,
        *,
        user_id: str,
        completed_on: date,
    ) -> list[str]:
        rows = (
            self._session.execute(
                text(
                    """
                select distinct task_id
                from learner.practice_answers
                where user_id = cast(:user_id as uuid)
                  and (answered_at at time zone 'UTC')::date = :completed_on
                union
                select distinct task_id
                from learner.essay_attempts
                where learner_key = :learner_key
                  and (updated_at at time zone 'UTC')::date = :completed_on
                union
                select distinct 'weekly-guokao-mock-exam' as task_id
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and review_viewed_at is not null
                  and (review_viewed_at at time zone 'UTC')::date = :completed_on
                order by task_id
                """
                ),
                {
                    "user_id": user_id,
                    "learner_key": user_id,
                    "completed_on": completed_on,
                },
            )
            .mappings()
            .all()
        )
        return [str(row["task_id"]) for row in rows]

    def list_workbench_sections(
        self,
        *,
        user_id: str,
        limit_per_section: int = 12,
    ) -> list[LearnerWorkbenchSectionRecord]:
        practice_objects = self._list_practice_workbench_objects(
            user_id=user_id,
            limit=limit_per_section,
        )
        docket_objects = self._list_docket_workbench_objects(
            user_id=user_id,
            limit=limit_per_section,
        )
        essay_objects = self._list_essay_workbench_objects(
            user_id=user_id,
            limit=limit_per_section,
        )
        mock_objects = self._list_mock_exam_workbench_objects(
            user_id=user_id,
            limit=limit_per_section,
        )
        external_objects = self._list_external_wrong_workbench_objects(
            user_id=user_id,
            limit=limit_per_section,
        )

        sections: list[LearnerWorkbenchSectionRecord] = []
        if mock_objects:
            sections.append(
                LearnerWorkbenchSectionRecord(
                    id="mock_exam",
                    title="模考复盘",
                    objects=mock_objects,
                )
            )
        if docket_objects:
            sections.append(
                LearnerWorkbenchSectionRecord(
                    id="docket",
                    title="卷宗复盘",
                    objects=docket_objects,
                )
            )
        if practice_objects:
            sections.append(
                LearnerWorkbenchSectionRecord(
                    id="xingce",
                    title="行测复盘",
                    objects=practice_objects,
                )
            )
        if essay_objects:
            sections.append(
                LearnerWorkbenchSectionRecord(
                    id="shenlun",
                    title="申论复盘",
                    objects=essay_objects,
                )
            )
        if external_objects:
            sections.append(
                LearnerWorkbenchSectionRecord(
                    id="external_wrong",
                    title="外部错题",
                    objects=external_objects,
                )
            )
        return sections

    def has_mock_exam_this_week(
        self,
        *,
        user_id: str,
        week_start: date,
    ) -> bool:
        row = self._session.execute(
            text(
                """
                select 1
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and (created_at at time zone 'UTC')::date >= :week_start
                limit 1
                """
            ),
            {"user_id": user_id, "week_start": week_start},
        ).first()
        return row is not None

    def get_unviewed_mock_review(
        self,
        *,
        user_id: str,
    ) -> LearnerWorkbenchObjectRecord | None:
        objects = self._list_mock_exam_workbench_objects(
            user_id=user_id,
            limit=1,
            unviewed_only=True,
        )
        return objects[0] if objects else None

    def get_latest_mock_review_reason(
        self,
        *,
        user_id: str,
    ) -> str | None:
        row = (
            self._session.execute(
                text(
                    """
                select review_payload ->> 'nextWeekReason' as reason
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and review_payload ? 'nextWeekReason'
                order by submitted_at desc nulls last, created_at desc
                limit 1
                """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .first()
        )
        return str(row["reason"]) if row and row["reason"] else None

    def _list_practice_workbench_objects(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[LearnerWorkbenchObjectRecord]:
        rows = (
            self._session.execute(
                text(
                    """
                select
                  task_id,
                  max(answered_at) as latest_answered_at,
                  count(*) as answer_count,
                  count(*) filter (where correct = false) as wrong_count,
                  array_agg(question_id order by answered_at desc) as question_ids
                from learner.practice_answers
                where user_id = cast(:user_id as uuid)
                group by task_id
                order by latest_answered_at desc
                limit :limit
                """
                ),
                {"user_id": user_id, "limit": limit},
            )
            .mappings()
            .all()
        )
        objects: list[LearnerWorkbenchObjectRecord] = []
        for row in rows:
            latest_answered_at = row["latest_answered_at"]
            day = latest_answered_at.date().isoformat()
            answer_count = int(row["answer_count"])
            wrong_count = int(row["wrong_count"])
            objects.append(
                LearnerWorkbenchObjectRecord(
                    id=f"practice-{row['task_id']}-{day}",
                    type="practice_review",
                    title=f"行测答题复盘 · {day}",
                    subtitle=f"{answer_count} 题 · 错 {wrong_count} 题",
                    source_count=answer_count,
                    payload={
                        "taskId": str(row["task_id"]),
                        "latestAnsweredAt": latest_answered_at.isoformat(),
                        "questionIds": [str(item) for item in row["question_ids"]],
                        "wrongCount": wrong_count,
                    },
                )
            )
        return objects

    def _list_docket_workbench_objects(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[LearnerWorkbenchObjectRecord]:
        rows = (
            self._session.execute(
                text(
                    """
                select
                  d.docket_id::text as docket_id,
                  d.docket_date,
                  max(v.created_at) as latest_answered_at,
                  count(*) as answer_count,
                  count(*) filter (where v.correct = false) as wrong_count,
                  array_agg(v.question_id order by v.created_at desc) as question_ids
                from learner.dockets d
                join learner.verdicts v on v.docket_id = d.docket_id
                where d.user_id = cast(:user_id as uuid)
                group by d.docket_id, d.docket_date
                order by latest_answered_at desc
                limit :limit
                """
                ),
                {"user_id": user_id, "limit": limit},
            )
            .mappings()
            .all()
        )
        objects: list[LearnerWorkbenchObjectRecord] = []
        for row in rows:
            latest_answered_at = row["latest_answered_at"]
            answer_count = int(row["answer_count"])
            wrong_count = int(row["wrong_count"])
            objects.append(
                LearnerWorkbenchObjectRecord(
                    id=f"docket-{row['docket_id']}",
                    type="docket_review",
                    title=f"今日卷宗复盘 · {row['docket_date'].isoformat()}",
                    subtitle=f"{answer_count} 封 · 驳 {wrong_count} 封",
                    source_count=answer_count,
                    payload={
                        "docketId": str(row["docket_id"]),
                        "latestAnsweredAt": latest_answered_at.isoformat(),
                        "questionIds": [str(item) for item in row["question_ids"]],
                        "wrongCount": wrong_count,
                        "totalCount": answer_count,
                    },
                )
            )
        return objects

    def _list_essay_workbench_objects(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[LearnerWorkbenchObjectRecord]:
        rows = (
            self._session.execute(
                text(
                    """
                select
                  attempt_id,
                  task_id,
                  review_payload,
                  rewritten_answer is not null as has_rewrite,
                  created_at,
                  updated_at,
                  coalesce(jsonb_array_length(review_payload -> 'dimensions'), 0)
                    + coalesce(jsonb_array_length(review_payload -> 'missingPoints'), 0)
                    as evidence_count
                from learner.essay_attempts
                where learner_key = :learner_key
                order by updated_at desc
                limit :limit
                """
                ),
                {"learner_key": user_id, "limit": limit},
            )
            .mappings()
            .all()
        )
        objects: list[LearnerWorkbenchObjectRecord] = []
        for row in rows:
            updated_at = row["updated_at"]
            day = updated_at.date().isoformat()
            evidence_count = int(row["evidence_count"])
            status = "已重写" if row["has_rewrite"] else "已点评"
            objects.append(
                LearnerWorkbenchObjectRecord(
                    id=f"essay-{row['attempt_id']}",
                    type="essay_review",
                    title=f"申论复盘 · {day}",
                    subtitle=f"{status} · {row['task_id']}",
                    source_count=evidence_count,
                    payload={
                        "attemptId": str(row["attempt_id"]),
                        "taskId": str(row["task_id"]),
                        "updatedAt": updated_at.isoformat(),
                        "hasRewrite": bool(row["has_rewrite"]),
                    },
                )
            )
        return objects

    def _list_mock_exam_workbench_objects(
        self,
        *,
        user_id: str,
        limit: int,
        unviewed_only: bool = False,
    ) -> list[LearnerWorkbenchObjectRecord]:
        unviewed_sql = "and review_viewed_at is null" if unviewed_only else ""
        rows = (
            self._session.execute(
                text(
                    f"""
                select
                  session_id,
                  review_payload,
                  review_viewed_at,
                  submitted_at,
                  created_at
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and review_payload is not null
                  {unviewed_sql}
                order by submitted_at desc nulls last, created_at desc
                limit :limit
                """
                ),
                {"user_id": user_id, "limit": limit},
            )
            .mappings()
            .all()
        )
        objects: list[LearnerWorkbenchObjectRecord] = []
        for row in rows:
            review = dict(row["review_payload"] or {})
            submitted_at = row["submitted_at"] or row["created_at"]
            day = submitted_at.date().isoformat()
            score = review.get("estimatedScore")
            viewed = row["review_viewed_at"] is not None
            objects.append(
                LearnerWorkbenchObjectRecord(
                    id=f"mock-{row['session_id']}",
                    type="mock_exam_review",
                    title=f"行测模考复盘 · {day}",
                    subtitle=f"{'已查看' if viewed else '待查看'} · 预估 {score} 分",
                    source_count=len(review.get("wrongClusters", [])),
                    payload={
                        "sessionId": str(row["session_id"]),
                        "submittedAt": submitted_at.isoformat(),
                        "viewed": viewed,
                        "estimatedScore": score,
                        "nextWeekReason": review.get("nextWeekReason"),
                    },
                )
            )
        return objects

    def _list_external_wrong_workbench_objects(
        self,
        *,
        user_id: str,
        limit: int,
    ) -> list[LearnerWorkbenchObjectRecord]:
        rows = (
            self._session.execute(
                text(
                    """
                select
                  ewq.external_question_id,
                  release.release_id,
                  release.release_version,
                  ewq.node_id,
                  ewq.error_summary,
                  ewq.created_at,
                  kmn.display_path
                from learner.external_wrong_questions ewq
                join product.knowledge_map_releases release
                  on release.release_id = ewq.knowledge_release_id
                 and release.exam_scope = 'guokao'
                 and release.track = 'xingce'
                 and release.status in ('active', 'retired')
                join product.knowledge_map_nodes kmn
                  on kmn.release_id = release.release_id
                 and kmn.node_id = ewq.node_id
                where ewq.user_id = cast(:user_id as uuid)
                  and kmn.exam_scope = 'guokao'
                  and kmn.track = 'xingce'
                  and kmn.status = 'published'
                order by ewq.created_at desc
                limit :limit
                """
                ),
                {"user_id": user_id, "limit": limit},
            )
            .mappings()
            .all()
        )
        objects: list[LearnerWorkbenchObjectRecord] = []
        for row in rows:
            created_at = row["created_at"]
            day = created_at.date().isoformat()
            objects.append(
                LearnerWorkbenchObjectRecord(
                    id=f"external-{row['external_question_id']}",
                    type="external_wrong_question",
                    title=f"外部错题 · {row['display_path']}",
                    subtitle=f"{day} · {row['error_summary']}",
                    source_count=1,
                    payload={
                        "externalQuestionId": str(row["external_question_id"]),
                        "knowledgeReleaseId": str(row["release_id"]),
                        "knowledgeReleaseVersion": str(row["release_version"]),
                        "nodeId": str(row["node_id"]),
                        "displayPath": str(row["display_path"]),
                        "createdAt": created_at.isoformat(),
                    },
                )
            )
        return objects
