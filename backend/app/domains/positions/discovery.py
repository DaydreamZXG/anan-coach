from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.ability_estimation.service import AbilityEstimateService
from app.domains.positions.fit import evaluate_position_fit
from app.domains.positions.schemas import (
    PositionComparisonHighlights,
    PositionComparisonItem,
    PositionComparisonResponse,
    PositionDiscoveryDetail,
    PositionDiscoveryItem,
    PositionDiscoveryResponse,
    PositionFitResponse,
    PositionWorkspaceItem,
    PositionWorkspaceResponse,
    TargetScoreRange,
)
from app.domains.positions.target_scores import (
    PositionTargetScoreOwner,
    TargetScoreReleaseContext,
    TargetScoreVersionConflictError,
)
from app.domains.qualification.repository import (
    PositionRuleRelease,
    QualificationRepository,
)


class PositionDiscoveryCursorError(ValueError):
    pass


class PositionComparisonError(ValueError):
    pass


@dataclass(frozen=True)
class DiscoveryCursor:
    context: str
    eligibility_rank: int
    headcount: int
    position_id: str


class PositionDiscoveryService:
    def __init__(
        self,
        qualification: QualificationRepository | None = None,
        target_scores: PositionTargetScoreOwner | None = None,
        ability: AbilityEstimateService | None = None,
    ) -> None:
        self._qualification = qualification or QualificationRepository()
        self._target_scores = target_scores or PositionTargetScoreOwner(
            qualification=self._qualification
        )
        self._ability = ability or AbilityEstimateService(
            qualification=self._qualification
        )

    def search(
        self,
        session: Session,
        *,
        user_id: str,
        exam_year: int | None,
        region: str | None,
        city: str | None,
        query: str | None,
        limit: int,
        cursor: str | None,
        now: datetime | None = None,
    ) -> PositionDiscoveryResponse:
        release = self._active_release(session, exam_year=exam_year)
        target_context = self._target_scores.context_for_release(
            release=release,
            now=now,
        )
        profile_version = self._qualification.get_profile_version(
            session,
            user_id=user_id,
        )
        return self._search_page(
            session,
            user_id=user_id,
            release=release,
            target_context=target_context,
            profile_version=profile_version,
            region=region,
            city=city,
            query=query,
            limit=limit,
            cursor=cursor,
            excluded_position_id=None,
        )

    def _search_page(
        self,
        session: Session,
        *,
        user_id: str,
        release: PositionRuleRelease,
        target_context: TargetScoreReleaseContext,
        profile_version: int,
        region: str | None,
        city: str | None,
        query: str | None,
        limit: int,
        cursor: str | None,
        excluded_position_id: str | None,
    ) -> PositionDiscoveryResponse:
        context = _context_fingerprint(
            user_id=user_id,
            profile_version=profile_version,
            release=release,
            region=region,
            city=city,
            query=query,
            excluded_position_id=excluded_position_id,
        )
        decoded = _decode_cursor(cursor, expected_context=context) if cursor else None
        rows = session.execute(
            text(_DISCOVERY_SQL),
            {
                "user_id": user_id,
                "profile_version": profile_version,
                "exam_year": release.exam_year,
                "rule_set_version": release.rule_set_version,
                "position_data_version": release.position_data_version,
                "region": region,
                "city": city,
                "query": query,
                "query_like": f"%{query}%" if query else None,
                "position_ids": None,
                "excluded_position_id": excluded_position_id,
                "cursor_rank": decoded.eligibility_rank if decoded else None,
                "cursor_headcount": decoded.headcount if decoded else None,
                "cursor_position_id": decoded.position_id if decoded else None,
                "limit_plus_one": limit + 1,
            },
        ).mappings().all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        target_scores = self._target_scores.ranges_for_positions(
            session,
            position_ids=[str(row["position_id"]) for row in page_rows],
            context=target_context,
        )
        items = [
            _item_from_row(
                row,
                target_score=target_scores[str(row["position_id"])],
            )
            for row in page_rows
        ]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(
                DiscoveryCursor(
                    context=context,
                    eligibility_rank=int(last["eligibility_rank"]),
                    headcount=int(last["headcount"]),
                    position_id=str(last["position_id"]),
                )
            )
        return PositionDiscoveryResponse(
            exam_year=release.exam_year,
            learner_profile_version=profile_version,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def workspace(
        self,
        session: Session,
        *,
        user_id: str,
        region: str | None,
        city: str | None,
        query: str | None,
        limit: int,
        cursor: str | None,
        now: datetime | None = None,
    ) -> PositionWorkspaceResponse:
        release = self._active_release(session, exam_year=None)
        target_context = self._target_scores.context_for_release(
            release=release,
            now=now,
        )
        profile_version = self._qualification.get_profile_version(
            session,
            user_id=user_id,
        )
        primary_position_id = self._confirmed_primary_goal_position_id(
            session,
            user_id=user_id,
        )
        discovery = self._search_page(
            session,
            user_id=user_id,
            release=release,
            target_context=target_context,
            profile_version=profile_version,
            region=region,
            city=city,
            query=query,
            limit=limit,
            cursor=cursor,
            excluded_position_id=primary_position_id,
        )
        ability = self._ability.get_current(
            session,
            user_id=user_id,
            exam_year=discovery.exam_year,
            now=now,
        )
        items = list(discovery.items)
        if cursor is None and primary_position_id is not None:
            primary_goal = self._load_primary_goal(
                session,
                user_id=user_id,
                position_id=primary_position_id,
                release=release,
                target_context=target_context,
                profile_version=profile_version,
            )
            items = [primary_goal, *items]
        return PositionWorkspaceResponse(
            exam_year=discovery.exam_year,
            learner_profile_version=discovery.learner_profile_version,
            rule_set_version=discovery.rule_set_version,
            position_data_version=discovery.position_data_version,
            ability_estimate=ability,
            items=[
                PositionWorkspaceItem(
                    **item.model_dump(),
                    fit=evaluate_position_fit(
                        position=item,
                        ability=ability,
                        exam_year=discovery.exam_year,
                    ),
                )
                for item in items
            ],
            next_cursor=discovery.next_cursor,
            has_more=discovery.has_more,
        )

    @staticmethod
    def _confirmed_primary_goal_position_id(
        session: Session,
        *,
        user_id: str,
    ) -> str | None:
        position_id = session.execute(
            text(
                """
                select position_id::text
                from learner.position_goal_versions
                where user_id = cast(:user_id as uuid)
                  and role = 'primary'
                  and status = 'confirmed'
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
        return str(position_id) if position_id is not None else None

    def _load_primary_goal(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        release: PositionRuleRelease,
        target_context: TargetScoreReleaseContext,
        profile_version: int,
    ) -> PositionDiscoveryItem:
        rows = (
            session.execute(
                text(_DISCOVERY_SQL),
                {
                    "user_id": user_id,
                    "profile_version": profile_version,
                    "exam_year": release.exam_year,
                    "rule_set_version": release.rule_set_version,
                    "position_data_version": release.position_data_version,
                    "region": None,
                    "city": None,
                    "query": None,
                    "query_like": None,
                    "position_ids": [str(position_id)],
                    "excluded_position_id": None,
                    "cursor_rank": None,
                    "cursor_headcount": None,
                    "cursor_position_id": None,
                    "limit_plus_one": 2,
                },
            )
            .mappings()
            .all()
        )
        if not rows:
            raise TargetScoreVersionConflictError(
                "The confirmed primary goal no longer belongs to the active position release"
            )

        row = rows[0]
        target_score = self._target_scores.ranges_for_positions(
            session,
            position_ids=[str(position_id)],
            context=target_context,
        )[str(position_id)]
        return _item_from_row(row, target_score=target_score)

    def compare(
        self,
        session: Session,
        *,
        user_id: str,
        position_ids: list[str],
        now: datetime | None = None,
    ) -> PositionComparisonResponse:
        if not 2 <= len(position_ids) <= 3 or len(position_ids) != len(set(position_ids)):
            raise PositionComparisonError("Compare two or three unique positions")
        release = self._active_release(session, exam_year=None)
        target_context = self._target_scores.context_for_release(
            release=release,
            now=now,
        )
        profile_version = self._qualification.get_profile_version(
            session,
            user_id=user_id,
        )
        rows = session.execute(
            text(_DISCOVERY_SQL),
            {
                "user_id": user_id,
                "profile_version": profile_version,
                "exam_year": release.exam_year,
                "rule_set_version": release.rule_set_version,
                "position_data_version": release.position_data_version,
                "region": None,
                "city": None,
                "query": None,
                "query_like": None,
                "position_ids": position_ids,
                "excluded_position_id": None,
                "cursor_rank": None,
                "cursor_headcount": None,
                "cursor_position_id": None,
                "limit_plus_one": 4,
            },
        ).mappings().all()
        rows_by_id = {str(row["position_id"]): row for row in rows}
        if set(rows_by_id) != set(position_ids):
            raise PositionComparisonError(
                "Every compared position must belong to the active guokao release"
            )
        target_scores = self._target_scores.ranges_for_positions(
            session,
            position_ids=position_ids,
            context=target_context,
        )
        items = [
            _comparison_item_from_row(
                rows_by_id[position_id],
                target_score=target_scores[position_id],
            )
            for position_id in position_ids
        ]
        largest_headcount = max(item.headcount for item in items)
        reliable_scores = [
            item.target_score.recommended
            for item in items
            if item.target_score is not None
        ]
        lowest_target = min(reliable_scores) if reliable_scores else None
        return PositionComparisonResponse(
            exam_year=release.exam_year,
            learner_profile_version=profile_version,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
            items=items,
            highlights=PositionComparisonHighlights(
                confirmed_eligible_position_ids=[
                    item.position_id
                    for item in items
                    if item.eligibility_status == "eligible"
                ],
                largest_headcount_position_ids=[
                    item.position_id
                    for item in items
                    if item.headcount == largest_headcount
                ],
                lowest_target_score_position_ids=[
                    item.position_id
                    for item in items
                    if lowest_target is not None
                    and item.target_score is not None
                    and item.target_score.recommended == lowest_target
                ],
                primary_goal_position_id=next(
                    (item.position_id for item in items if item.is_primary_goal),
                    None,
                ),
            ),
        )

    def detail(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        now: datetime | None = None,
    ) -> PositionDiscoveryDetail:
        target_context = self._target_scores.active_context_for_position(
            session,
            position_id=position_id,
            now=now,
        )
        release = target_context.release
        profile_version = self._qualification.get_profile_version(
            session,
            user_id=user_id,
        )
        rows = session.execute(
            text(_DISCOVERY_SQL),
            {
                "user_id": user_id,
                "profile_version": profile_version,
                "exam_year": release.exam_year,
                "rule_set_version": release.rule_set_version,
                "position_data_version": release.position_data_version,
                "region": None,
                "city": None,
                "query": None,
                "query_like": None,
                "position_ids": [position_id],
                "excluded_position_id": None,
                "cursor_rank": None,
                "cursor_headcount": None,
                "cursor_position_id": None,
                "limit_plus_one": 2,
            },
        ).mappings().all()
        if not rows:
            # The owner already established that the position exists. Not finding it
            # in this release therefore means its version membership changed.
            raise TargetScoreVersionConflictError(
                "The position no longer belongs to the active position release"
            )
        target_score = self._target_scores.range_for_position(
            session,
            position_id=position_id,
            context=target_context,
        )
        item = _comparison_item_from_row(rows[0], target_score=target_score)
        ability = self._ability.get_current(
            session,
            user_id=user_id,
            exam_year=release.exam_year,
            now=now,
        )
        return PositionDiscoveryDetail(
            **item.model_dump(),
            exam_year=release.exam_year,
            learner_profile_version=profile_version,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
            ability_estimate=ability,
            fit=evaluate_position_fit(
                position=item,
                ability=ability,
                exam_year=release.exam_year,
            ),
        )

    def fit(
        self,
        session: Session,
        *,
        user_id: str,
        position_id: str,
        now: datetime | None = None,
    ) -> PositionFitResponse:
        detail = self.detail(
            session,
            user_id=user_id,
            position_id=position_id,
            now=now,
        )
        return PositionFitResponse(
            exam_year=detail.exam_year,
            learner_profile_version=detail.learner_profile_version,
            rule_set_version=detail.rule_set_version,
            position_data_version=detail.position_data_version,
            position=PositionComparisonItem.model_validate(detail.model_dump()),
            ability_estimate=detail.ability_estimate,
            fit=detail.fit,
        )

    def _active_release(
        self,
        session: Session,
        *,
        exam_year: int | None,
    ) -> PositionRuleRelease:
        return self._qualification.active_release(
            session,
            exam_code="guokao",
            exam_year=exam_year,
        )


def _item_from_row(
    row: Any,
    *,
    target_score: TargetScoreRange | None,
) -> PositionDiscoveryItem:
    reason_codes: list[str] = []
    if not row["compilation_id"]:
        reason_codes.append("requirements_missing")
    if int(row["pending_count"] or 0) > 0:
        reason_codes.append("conditions_need_confirmation")
    if int(row["not_met_count"] or 0) > 0:
        reason_codes.append("hard_condition_not_met")
    if target_score is None:
        reason_codes.append("target_score_unavailable")
    return PositionDiscoveryItem(
        position_id=str(row["position_id"]),
        position_code=row["position_code"],
        name=row["name"],
        department_full=row["department_full"],
        system_code=row["system_code"],
        region=row["region"],
        city=row["city"],
        headcount=row["headcount"],
        has_extra_exam=bool(row["has_extra_exam"]),
        eligibility_status=row["eligibility_status"],
        met_count=int(row["met_count"] or 0),
        pending_count=int(row["pending_count"] or 0),
        not_met_count=int(row["not_met_count"] or 0),
        pending_requirement_ids=list(row["pending_requirement_ids"] or []),
        not_met_requirement_ids=list(row["not_met_requirement_ids"] or []),
        reason_codes=reason_codes,
        target_score=target_score,
        is_primary_goal=bool(row["is_primary_goal"]),
    )


def _comparison_item_from_row(
    row: Any,
    *,
    target_score: TargetScoreRange | None,
) -> PositionComparisonItem:
    item = _item_from_row(row, target_score=target_score)
    return PositionComparisonItem(
        **item.model_dump(),
        paper_type=row["paper_type"],
    )


def _context_fingerprint(
    *,
    user_id: str,
    profile_version: int,
    release: PositionRuleRelease,
    region: str | None,
    city: str | None,
    query: str | None,
    excluded_position_id: str | None,
) -> str:
    payload = {
        "userId": user_id,
        "profileVersion": profile_version,
        "ruleSetVersion": release.rule_set_version,
        "positionDataVersion": release.position_data_version,
        "region": region,
        "city": city,
        "query": query,
        "excludedPositionId": excluded_position_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _encode_cursor(cursor: DiscoveryCursor) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "c": cursor.context,
            "r": cursor.eligibility_rank,
            "h": cursor.headcount,
            "p": cursor.position_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, expected_context: str) -> DiscoveryCursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        position_id = str(UUID(payload["p"]))
        rank = int(payload["r"])
        headcount = int(payload["h"])
        if payload.get("v") != 1 or payload["c"] != expected_context:
            raise ValueError
        if rank not in (0, 1, 2) or headcount < 1:
            raise ValueError
        return DiscoveryCursor(
            context=payload["c"],
            eligibility_rank=rank,
            headcount=headcount,
            position_id=position_id,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PositionDiscoveryCursorError(
            "The position cursor is invalid or belongs to a different profile/filter version"
        ) from exc


_DISCOVERY_SQL = """
with latest_facts as (
  select distinct on (fact_key)
    fact_key, value, verification_status
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
candidate_positions as (
  select
    position.position_id,
    position.position_code,
    position.name,
    position.department_full,
    position.system_code,
    position.region,
    position.city,
    position.paper_type,
    position.headcount,
    position.has_extra_exam,
    compilation.compilation_id
  from product.positions position
  left join product.position_rule_compilations compilation
    on compilation.position_id = position.position_id
   and compilation.rule_set_version = :rule_set_version
   and compilation.position_data_version = :position_data_version
  where position.year = :exam_year
    and (
      cast(:position_ids as uuid[]) is null
      or position.position_id = any(cast(:position_ids as uuid[]))
    )
    and (
      cast(:excluded_position_id as uuid) is null
      or position.position_id <> cast(:excluded_position_id as uuid)
    )
    and (cast(:region as text) is null or position.region = cast(:region as text))
    and (cast(:city as text) is null or position.city = cast(:city as text))
    and (
      cast(:query as text) is null
      or position.name ilike :query_like
      or position.department_full ilike :query_like
      or position.position_code ilike :query_like
    )
),
requirement_checks as (
  select
    candidate.position_id,
    requirement.requirement_id,
    case
      when requirement.requirement_id is null then 'not_applicable'
      when requirement.operator = 'manual_review'
       and confirmation.response = 'satisfied' then 'met'
      when requirement.operator = 'manual_review'
       and confirmation.response = 'not_satisfied' then 'not_met'
      when requirement.operator = 'manual_review' then 'needs_confirmation'
      when fact.fact_key is null then 'needs_confirmation'
      when fact.verification_status = 'conflicting' then 'needs_confirmation'
      when requirement.operator = 'equals' and fact.value = requirement.expected then 'met'
      when requirement.operator = 'one_of'
       and jsonb_typeof(requirement.expected) = 'array'
       and requirement.expected @> jsonb_build_array(fact.value) then 'met'
      when requirement.operator = 'at_least'
       and jsonb_typeof(fact.value) = 'number'
       and jsonb_typeof(requirement.expected) = 'number'
       and (fact.value #>> '{}')::numeric >= (requirement.expected #>> '{}')::numeric then 'met'
      when requirement.operator = 'contains_any'
       and jsonb_typeof(requirement.expected) = 'array'
       and exists (
         select 1
         from jsonb_array_elements_text(requirement.expected) expected(value)
         where (
           jsonb_typeof(fact.value) = 'string'
           and lower(btrim(fact.value #>> '{}')) = lower(btrim(expected.value))
         ) or (
           jsonb_typeof(fact.value) = 'array'
           and exists (
             select 1
             from jsonb_array_elements_text(fact.value) actual(value)
             where lower(btrim(actual.value)) = lower(btrim(expected.value))
           )
         )
       ) then 'met'
      else 'not_met'
    end as check_status
  from candidate_positions candidate
  left join product.position_requirements requirement
    on requirement.position_id = candidate.position_id
   and requirement.rule_set_version = :rule_set_version
   and requirement.position_data_version = :position_data_version
   and requirement.hard_condition = true
  left join latest_facts fact on fact.fact_key = requirement.fact_key
  left join latest_confirmations confirmation
    on confirmation.requirement_id = requirement.requirement_id
),
eligibility as (
  select
    candidate.*,
    count(checks.requirement_id) filter (where checks.check_status = 'met') as met_count,
    count(checks.requirement_id) filter (where checks.check_status = 'needs_confirmation') as pending_count,
    count(checks.requirement_id) filter (where checks.check_status = 'not_met') as not_met_count,
    coalesce(
      array_agg(checks.requirement_id::text order by checks.requirement_id::text)
        filter (where checks.check_status = 'needs_confirmation'),
      array[]::text[]
    ) as pending_requirement_ids,
    coalesce(
      array_agg(checks.requirement_id::text order by checks.requirement_id::text)
        filter (where checks.check_status = 'not_met'),
      array[]::text[]
    ) as not_met_requirement_ids,
    case
      when candidate.compilation_id is null then 'needs_confirmation'
      when count(checks.requirement_id) filter (where checks.check_status = 'not_met') > 0 then 'ineligible'
      when count(checks.requirement_id) filter (where checks.check_status = 'needs_confirmation') > 0 then 'needs_confirmation'
      else 'eligible'
    end as eligibility_status
  from candidate_positions candidate
  left join requirement_checks checks on checks.position_id = candidate.position_id
  group by candidate.position_id, candidate.position_code, candidate.name,
           candidate.department_full, candidate.system_code, candidate.region,
           candidate.city, candidate.paper_type, candidate.headcount,
           candidate.has_extra_exam, candidate.compilation_id
),
annual_scores as (
  select link.position_id, evidence.year,
         avg(evidence.entry_score_min) as average_score,
         count(*) as sample_count
  from product.position_entry_score_links link
  join product.position_entry_score_evidence evidence
    on evidence.evidence_id = link.evidence_id
  join candidate_positions candidate on candidate.position_id = link.position_id
  where evidence.entry_score_min is not null
  group by link.position_id, evidence.year
),
latest_scores as (
  select distinct on (position_id)
    position_id, average_score as latest_average_score
  from annual_scores
  order by position_id, year desc
),
score_totals as (
  select position_id, sum(sample_count) as score_sample_count
  from annual_scores
  group by position_id
),
ranked as (
  select
    eligibility.*,
    case eligibility.eligibility_status
      when 'eligible' then 0
      when 'needs_confirmation' then 1
      else 2
    end as eligibility_rank,
    latest.latest_average_score,
    totals.score_sample_count,
    (goal.goal_version_id is not null) as is_primary_goal
  from eligibility
  left join latest_scores latest on latest.position_id = eligibility.position_id
  left join score_totals totals on totals.position_id = eligibility.position_id
  left join learner.position_goal_versions goal
    on goal.position_id = eligibility.position_id
   and goal.user_id = cast(:user_id as uuid)
   and goal.role = 'primary'
   and goal.status = 'confirmed'
)
select *
from ranked
where cast(:cursor_rank as integer) is null
   or eligibility_rank > cast(:cursor_rank as integer)
   or (eligibility_rank = cast(:cursor_rank as integer) and headcount < cast(:cursor_headcount as integer))
   or (
     eligibility_rank = cast(:cursor_rank as integer)
     and headcount = cast(:cursor_headcount as integer)
     and position_id::text > cast(:cursor_position_id as text)
   )
order by eligibility_rank, headcount desc, position_id::text
limit :limit_plus_one
"""
