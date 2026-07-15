from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CalibrationRawRecord:
    baseline_count: int
    baseline_xingce_score: float | None
    practice_answers: int
    practice_correct: int
    mock_exams: int
    latest_mock_score: float | None
    latest_mock_reason: str | None
    practice_modules: list[dict[str, Any]]
    mock_modules: list[dict[str, Any]]


class CalibrationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_raw(self, *, user_id: str) -> CalibrationRawRecord:
        summary = self._session.execute(
            text(
                """
                select
                  case
                    when p.baseline_source in ('self_report', 'placement_test')
                      and p.baseline_xingce_score is not null
                    then 1 else 0
                  end as baseline_count,
                  p.baseline_xingce_score,
                  (
                    select count(*)
                    from learner.practice_answers
                    where user_id = cast(:user_id as uuid)
                  ) as practice_answers,
                  (
                    select count(*)
                    from learner.practice_answers
                    where user_id = cast(:user_id as uuid)
                      and correct = true
                  ) as practice_correct,
                  (
                    select count(*)
                    from learner.mock_exam_sessions
                    where user_id = cast(:user_id as uuid)
                      and review_payload is not null
                  ) as mock_exams,
                  (
                    select cast(review_payload ->> 'estimatedScore' as numeric)
                    from learner.mock_exam_sessions
                    where user_id = cast(:user_id as uuid)
                      and review_payload ? 'estimatedScore'
                    order by submitted_at desc nulls last, created_at desc
                    limit 1
                  ) as latest_mock_score,
                  (
                    select review_payload ->> 'nextWeekReason'
                    from learner.mock_exam_sessions
                    where user_id = cast(:user_id as uuid)
                      and review_payload ? 'nextWeekReason'
                    order by submitted_at desc nulls last, created_at desc
                    limit 1
                  ) as latest_mock_reason
                from learner.profiles p
                where p.user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
        if summary is None:
            summary_data = {
                "baseline_count": 0,
                "baseline_xingce_score": None,
                "practice_answers": 0,
                "practice_correct": 0,
                "mock_exams": 0,
                "latest_mock_score": None,
                "latest_mock_reason": None,
            }
        else:
            summary_data = dict(summary)

        practice_modules = self._session.execute(
            text(
                """
                select
                  q.module_name,
                  count(*) as sample_size,
                  count(*) filter (where pa.correct = true) as correct_count
                from learner.practice_answers pa
                join product.questions q on q.question_id = pa.question_id
                where pa.user_id = cast(:user_id as uuid)
                  and q.module_name is not null
                group by q.module_name
                order by sample_size desc, q.module_name
                """
            ),
            {"user_id": user_id},
        ).mappings().all()

        mock_row = self._session.execute(
            text(
                """
                select review_payload -> 'modules' as modules
                from learner.mock_exam_sessions
                where user_id = cast(:user_id as uuid)
                  and review_payload ? 'modules'
                order by submitted_at desc nulls last, created_at desc
                limit 1
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
        mock_modules = list(mock_row["modules"] or []) if mock_row else []

        return CalibrationRawRecord(
            baseline_count=int(summary_data["baseline_count"] or 0),
            baseline_xingce_score=(
                float(summary_data["baseline_xingce_score"])
                if summary_data["baseline_xingce_score"] is not None
                else None
            ),
            practice_answers=int(summary_data["practice_answers"] or 0),
            practice_correct=int(summary_data["practice_correct"] or 0),
            mock_exams=int(summary_data["mock_exams"] or 0),
            latest_mock_score=(
                float(summary_data["latest_mock_score"])
                if summary_data["latest_mock_score"] is not None
                else None
            ),
            latest_mock_reason=summary_data["latest_mock_reason"],
            practice_modules=[dict(row) for row in practice_modules],
            mock_modules=[dict(item) for item in mock_modules if isinstance(item, dict)],
        )
