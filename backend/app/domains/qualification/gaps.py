from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.qualification.repository import QualificationRepository
from app.domains.qualification.schemas import QualificationGap, QualificationGapSummary


class QualificationGapService:
    def __init__(self, repository: QualificationRepository | None = None) -> None:
        self._repository = repository or QualificationRepository()

    def summarize(self, session: Session, *, user_id: str) -> QualificationGapSummary:
        release = self._repository.active_release(session, exam_code="guokao")
        profile_version = self._repository.get_profile_version(session, user_id=user_id)
        rows = session.execute(
            text(_QUALIFICATION_GAPS_SQL),
            {
                "user_id": user_id,
                "profile_version": profile_version,
                "exam_year": release.exam_year,
                "rule_set_version": release.rule_set_version,
                "position_data_version": release.position_data_version,
            },
        ).mappings().all()
        gaps = [
            QualificationGap(
                fact_key=row["fact_key"],
                gap_type=row["gap_type"],
                affected_position_count=int(row["affected_position_count"]),
                sample_position_ids=list(row["sample_position_ids"]),
                sample_position_codes=list(row["sample_position_codes"]),
                action=(
                    "review_position_requirement"
                    if row["gap_type"] == "position_confirmation"
                    else "complete_profile"
                ),
            )
            for row in rows
        ]
        total_affected = int(rows[0]["total_affected_positions"]) if rows else 0
        return QualificationGapSummary(
            exam_year=release.exam_year,
            learner_profile_version=profile_version,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
            total_affected_positions=total_affected,
            gaps=gaps,
            complete=not gaps,
        )


_QUALIFICATION_GAPS_SQL = """
with latest_facts as (
  select distinct on (fact_key)
    fact_key, verification_status
  from learner.qualification_facts
  where user_id = cast(:user_id as uuid)
    and profile_version <= cast(:profile_version as bigint)
  order by fact_key, profile_version desc, recorded_at desc
),
latest_confirmations as (
  select distinct on (requirement_id)
    requirement_id, response
  from learner.qualification_requirement_confirmations
  where user_id = cast(:user_id as uuid)
    and profile_version <= cast(:profile_version as bigint)
  order by requirement_id, profile_version desc, confirmed_at desc
),
classified as (
  select
    requirement.position_id,
    position.position_code,
    requirement.fact_key,
    case
      when requirement.operator = 'manual_review'
       and confirmation.response in ('satisfied', 'not_satisfied') then null
      when requirement.operator = 'manual_review' then 'position_confirmation'
      when fact.fact_key is null then 'missing_fact'
      when fact.verification_status = 'conflicting' then 'conflicting_fact'
      else null
    end as gap_type
  from product.position_requirements requirement
  join product.positions position
    on position.position_id = requirement.position_id
   and position.year = cast(:exam_year as integer)
  join product.position_rule_compilations compilation
    on compilation.position_id = requirement.position_id
   and compilation.rule_set_version = :rule_set_version
   and compilation.position_data_version = :position_data_version
  left join latest_facts fact on fact.fact_key = requirement.fact_key
  left join latest_confirmations confirmation
    on confirmation.requirement_id = requirement.requirement_id
  where requirement.rule_set_version = :rule_set_version
    and requirement.position_data_version = :position_data_version
    and requirement.hard_condition = true
),
gaps as (
  select * from classified where gap_type is not null
)
select
  fact_key,
  gap_type,
  count(distinct position_id) as affected_position_count,
  (array_agg(distinct position_id::text order by position_id::text))[1:3]
    as sample_position_ids,
  (array_agg(distinct position_code order by position_code))[1:3]
    as sample_position_codes,
  (select count(distinct position_id) from gaps) as total_affected_positions
from gaps
group by fact_key, gap_type
order by
  case gap_type
    when 'conflicting_fact' then 0
    when 'missing_fact' then 1
    else 2
  end,
  affected_position_count desc,
  fact_key
"""
