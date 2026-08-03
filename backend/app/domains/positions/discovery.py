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

from app.domains.ability_estimation.schemas import AbilityEstimateCurrent
from app.domains.ability_estimation.service import AbilityEstimateService
from app.domains.onboarding.progress_repository import OnboardingProgressRepository
from app.domains.positions.fit import (
    evaluate_position_fit,
    position_recommendation_sort_key,
)
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


WORKSPACE_CANDIDATE_POOL_LIMIT = 200


@dataclass(frozen=True)
class DiscoveryCursor:
    context: str
    eligibility_rank: int
    headcount: int
    position_id: str


@dataclass(frozen=True)
class WorkspaceCursor:
    context: str
    ranking_fingerprint: str | None
    position_id: str
    legacy_eligibility_rank: int | None = None
    legacy_headcount: int | None = None
    legacy_position_id: str | None = None


@dataclass(frozen=True)
class WorkspacePage:
    items: tuple[PositionWorkspaceItem, ...]
    next_cursor: str | None
    has_more: bool


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
        self._ability = ability or AbilityEstimateService(qualification=self._qualification)
        self._preferences = OnboardingProgressRepository()

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
        release = self._active_release(session, exam_year=exam_year, now=now)
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
        rows = (
            session.execute(
                text(_DISCOVERY_SQL),
                {
                    "user_id": user_id,
                    "profile_version": profile_version,
                    "exam_year": release.exam_year,
                    "rule_set_version": release.rule_set_version,
                    "position_data_version": release.position_data_version,
                    "current_window_closes_at": release.current_window.closes_at,
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
            )
            .mappings()
            .all()
        )
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
        release = self._active_release(session, exam_year=None, now=now)
        target_context = self._target_scores.context_for_release(
            release=release,
            now=now,
        )
        profile_version = self._qualification.get_profile_version(
            session,
            user_id=user_id,
        )
        preference = (
            self._preferences.current_position_preference(session, user_id=user_id)
            if region is None
            else None
        )
        preferred_regions = (
            (region,)
            if region is not None
            else tuple(preference.preferred_regions if preference is not None else ())
        )
        accepts_relocation = preference.accepts_relocation if preference is not None else True
        preference_version = preference.preference_version if preference is not None else None
        primary_position_id = self._confirmed_primary_goal_position_id(
            session,
            user_id=user_id,
            release=release,
        )
        ability = self._ability.get_current(
            session,
            user_id=user_id,
            exam_year=release.exam_year,
            now=now,
        )
        page = self._workspace_page(
            session,
            user_id=user_id,
            release=release,
            target_context=target_context,
            profile_version=profile_version,
            ability=ability,
            region=region,
            preferred_regions=preferred_regions,
            accepts_relocation=accepts_relocation,
            preference_version=preference_version,
            city=city,
            query=query,
            limit=limit,
            cursor=cursor,
            excluded_position_id=primary_position_id,
        )
        items = list(page.items)
        if cursor is None and primary_position_id is not None:
            primary_goal = self._load_primary_goal(
                session,
                user_id=user_id,
                position_id=primary_position_id,
                release=release,
                target_context=target_context,
                profile_version=profile_version,
            )
            items = [
                PositionWorkspaceItem(
                    **primary_goal.model_dump(),
                    fit=evaluate_position_fit(
                        position=primary_goal,
                        ability=ability,
                        exam_year=release.exam_year,
                    ),
                ),
                *items,
            ]
        return PositionWorkspaceResponse(
            exam_year=release.exam_year,
            learner_profile_version=profile_version,
            rule_set_version=release.rule_set_version,
            position_data_version=release.position_data_version,
            ability_estimate=ability,
            items=items,
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def _workspace_page(
        self,
        session: Session,
        *,
        user_id: str,
        release: PositionRuleRelease,
        target_context: TargetScoreReleaseContext,
        profile_version: int,
        ability: AbilityEstimateCurrent,
        region: str | None,
        preferred_regions: tuple[str, ...],
        accepts_relocation: bool,
        preference_version: int | None,
        city: str | None,
        query: str | None,
        limit: int,
        cursor: str | None,
        excluded_position_id: str | None,
    ) -> WorkspacePage:
        base_context = _context_fingerprint(
            user_id=user_id,
            profile_version=profile_version,
            release=release,
            region=region,
            city=city,
            query=query,
            excluded_position_id=excluded_position_id,
        )
        context = _workspace_context_fingerprint(
            base_context=base_context,
            ability=ability,
            preference_version=preference_version,
            preferred_regions=preferred_regions,
            accepts_relocation=accepts_relocation,
        )

        # The workspace is a bounded recommendation surface. Preferred-region
        # rows receive a reserved candidate pool so a national headcount sort
        # cannot evict the user's local choices before fit ranking begins.
        def load_rows(*, candidate_region: str | None, candidate_limit: int) -> list[Any]:
            return list(
                session.execute(
                    text(_DISCOVERY_SQL),
                    {
                        "user_id": user_id,
                        "profile_version": profile_version,
                        "exam_year": release.exam_year,
                        "rule_set_version": release.rule_set_version,
                        "position_data_version": release.position_data_version,
                        "current_window_closes_at": release.current_window.closes_at,
                        "region": candidate_region,
                        "city": city,
                        "query": query,
                        "query_like": f"%{query}%" if query else None,
                        "position_ids": None,
                        "excluded_position_id": excluded_position_id,
                        "cursor_rank": None,
                        "cursor_headcount": None,
                        "cursor_position_id": None,
                        "limit_plus_one": candidate_limit,
                    },
                )
                .mappings()
                .all()
            )

        row_sets: list[list[Any]] = []
        if region is not None or not preferred_regions:
            row_sets.append(
                load_rows(
                    candidate_region=region,
                    candidate_limit=WORKSPACE_CANDIDATE_POOL_LIMIT,
                )
            )
        else:
            per_region_limit = max(
                1,
                WORKSPACE_CANDIDATE_POOL_LIMIT // len(preferred_regions),
            )
            row_sets.extend(
                load_rows(
                    candidate_region=preferred_region,
                    candidate_limit=per_region_limit,
                )
                for preferred_region in preferred_regions
            )
            if accepts_relocation:
                row_sets.append(
                    load_rows(
                        candidate_region=None,
                        candidate_limit=WORKSPACE_CANDIDATE_POOL_LIMIT,
                    )
                )
        rows_by_position: dict[str, Any] = {}
        for candidate_rows in row_sets:
            for candidate in candidate_rows:
                rows_by_position.setdefault(str(candidate["position_id"]), candidate)
        rows = list(rows_by_position.values())
        target_scores = self._target_scores.ranges_for_positions(
            session,
            position_ids=[str(row["position_id"]) for row in rows],
            context=target_context,
        )
        ranked: list[PositionWorkspaceItem] = []
        for row in rows:
            position_id = str(row["position_id"])
            position = _item_from_row(
                row,
                target_score=target_scores[position_id],
            )
            ranked.append(
                PositionWorkspaceItem(
                    **position.model_dump(),
                    fit=evaluate_position_fit(
                        position=position,
                        ability=ability,
                        exam_year=release.exam_year,
                    ),
                )
            )
        ranked.sort(
            key=lambda item: position_recommendation_sort_key(
                position=item,
                fit=item.fit,
                preferred_regions=preferred_regions,
            )
        )
        ranked = ranked[:WORKSPACE_CANDIDATE_POOL_LIMIT]
        decoded = (
            _decode_workspace_cursor(
                cursor,
                expected_context=context,
                expected_legacy_context=base_context,
            )
            if cursor
            else None
        )
        if (
            decoded is not None
            and decoded.legacy_eligibility_rank is not None
            and decoded.legacy_headcount is not None
            and decoded.legacy_position_id is not None
        ):
            if all(item.position_id != decoded.legacy_position_id for item in ranked):
                raise PositionDiscoveryCursorError(
                    "The legacy workspace cursor is outside the bounded candidate pool"
                )
            legacy_boundary = (
                decoded.legacy_eligibility_rank,
                -decoded.legacy_headcount,
                decoded.legacy_position_id,
            )
            ranked = [item for item in ranked if _legacy_discovery_sort_key(item) > legacy_boundary]
        ranking_fingerprint = _workspace_ranking_fingerprint(ranked)
        if (
            decoded is not None
            and decoded.ranking_fingerprint is not None
            and decoded.ranking_fingerprint != ranking_fingerprint
        ):
            raise PositionDiscoveryCursorError("The workspace cursor ranking evidence has changed")
        start = 0
        if decoded is not None and decoded.ranking_fingerprint is not None:
            start = next(
                (
                    index + 1
                    for index, item in enumerate(ranked)
                    if item.position_id == decoded.position_id
                ),
                -1,
            )
            if start < 0:
                raise PositionDiscoveryCursorError(
                    "The workspace cursor no longer belongs to the bounded candidate pool"
                )
        page_items = ranked[start : start + limit]
        has_more = start + len(page_items) < len(ranked)
        next_cursor = None
        if has_more and page_items:
            next_cursor = _encode_workspace_cursor(
                WorkspaceCursor(
                    context=context,
                    ranking_fingerprint=ranking_fingerprint,
                    position_id=page_items[-1].position_id,
                    legacy_eligibility_rank=(decoded.legacy_eligibility_rank if decoded else None),
                    legacy_headcount=decoded.legacy_headcount if decoded else None,
                    legacy_position_id=(decoded.legacy_position_id if decoded else None),
                )
            )
        return WorkspacePage(
            items=tuple(page_items),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def _confirmed_primary_goal_position_id(
        session: Session,
        *,
        user_id: str,
        release: PositionRuleRelease,
    ) -> str | None:
        position_id = session.execute(
            text(
                """
                select goal.position_id::text
                from learner.current_primary_position_goals goal
                left join product.position_import_batches import_batch
                  on import_batch.position_data_version = :position_data_version
                left join product.position_snapshots position_snapshot
                  on position_snapshot.position_id = goal.position_id
                 and position_snapshot.position_data_version
                       = :position_data_version
                left join product.positions legacy_position
                  on legacy_position.position_id = goal.position_id
                 and import_batch.position_data_version is null
                where goal.user_id = cast(:user_id as uuid)
                  and goal.position_data_version = :position_data_version
                  and goal.target_score_valid_until = :current_window_closes_at
                  and coalesce(position_snapshot.year, legacy_position.year)
                        = :exam_year
                """
            ),
            {
                "user_id": user_id,
                "position_data_version": release.position_data_version,
                "current_window_closes_at": release.current_window.closes_at,
                "exam_year": release.exam_year,
            },
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
                    "current_window_closes_at": release.current_window.closes_at,
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
        release = self._active_release(session, exam_year=None, now=now)
        target_context = self._target_scores.context_for_release(
            release=release,
            now=now,
        )
        profile_version = self._qualification.get_profile_version(
            session,
            user_id=user_id,
        )
        rows = (
            session.execute(
                text(_DISCOVERY_SQL),
                {
                    "user_id": user_id,
                    "profile_version": profile_version,
                    "exam_year": release.exam_year,
                    "rule_set_version": release.rule_set_version,
                    "position_data_version": release.position_data_version,
                    "current_window_closes_at": release.current_window.closes_at,
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
            )
            .mappings()
            .all()
        )
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
            item.target_score.recommended for item in items if item.target_score is not None
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
                    item.position_id for item in items if item.eligibility_status == "eligible"
                ],
                largest_headcount_position_ids=[
                    item.position_id for item in items if item.headcount == largest_headcount
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
        rows = (
            session.execute(
                text(_DISCOVERY_SQL),
                {
                    "user_id": user_id,
                    "profile_version": profile_version,
                    "exam_year": release.exam_year,
                    "rule_set_version": release.rule_set_version,
                    "position_data_version": release.position_data_version,
                    "current_window_closes_at": release.current_window.closes_at,
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
            )
            .mappings()
            .all()
        )
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
        now: datetime | None,
    ) -> PositionRuleRelease:
        return self._qualification.active_release(
            session,
            exam_code="guokao",
            exam_year=exam_year,
            now=now,
        )


def _item_from_row(
    row: Any,
    *,
    target_score: TargetScoreRange | None,
) -> PositionDiscoveryItem:
    reason_codes: list[str] = []
    if not row["compilation_id"]:
        reason_codes.append("requirements_missing")
    if bool(row["source_conditions_incomplete"]):
        reason_codes.append("source_conditions_incomplete")
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
        "releaseId": release.release_id,
        "ruleSetVersion": release.rule_set_version,
        "positionDataVersion": release.position_data_version,
        "businessWindowId": release.current_window.window_id,
        "region": region,
        "city": city,
        "query": query,
        "excludedPositionId": excluded_position_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _workspace_context_fingerprint(
    *,
    base_context: str,
    ability: AbilityEstimateCurrent,
    preference_version: int | None,
    preferred_regions: tuple[str, ...],
    accepts_relocation: bool,
) -> str:
    estimate = ability.estimate
    payload = {
        "baseContext": base_context,
        "abilityReadiness": ability.readiness,
        "abilityEstimateVersionId": (
            estimate.estimate_version_id if estimate is not None else None
        ),
        "abilityInputFingerprint": (estimate.input_fingerprint if estimate is not None else None),
        "positionPreferenceVersion": preference_version,
        "preferredRegions": preferred_regions,
        "acceptsRelocation": accepts_relocation,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _workspace_ranking_fingerprint(items: list[PositionWorkspaceItem]) -> str:
    raw = json.dumps(
        [item.position_id for item in items],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def _legacy_discovery_sort_key(
    item: PositionDiscoveryItem,
) -> tuple[int, int, str]:
    return (
        {
            "eligible": 0,
            "needs_confirmation": 1,
            "ineligible": 2,
        }[item.eligibility_status],
        -item.headcount,
        item.position_id,
    )


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


def _encode_workspace_cursor(cursor: WorkspaceCursor) -> str:
    if cursor.ranking_fingerprint is None:
        raise ValueError("A workspace cursor requires a ranking fingerprint")
    legacy = None
    if (
        cursor.legacy_eligibility_rank is not None
        and cursor.legacy_headcount is not None
        and cursor.legacy_position_id is not None
    ):
        legacy = {
            "r": cursor.legacy_eligibility_rank,
            "h": cursor.legacy_headcount,
            "p": cursor.legacy_position_id,
        }
    payload = json.dumps(
        {
            "v": 2,
            "c": cursor.context,
            "f": cursor.ranking_fingerprint,
            "p": cursor.position_id,
            "l": legacy,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_workspace_cursor(
    value: str,
    *,
    expected_context: str,
    expected_legacy_context: str,
) -> WorkspaceCursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        version = payload.get("v")
        if version == 1:
            legacy = _decode_cursor(
                value,
                expected_context=expected_legacy_context,
            )
            return WorkspaceCursor(
                context=expected_context,
                ranking_fingerprint=None,
                position_id=legacy.position_id,
                legacy_eligibility_rank=legacy.eligibility_rank,
                legacy_headcount=legacy.headcount,
                legacy_position_id=legacy.position_id,
            )
        position_id = str(UUID(payload["p"]))
        ranking_fingerprint = payload["f"]
        legacy_payload = payload.get("l")
        legacy_rank = None
        legacy_headcount = None
        legacy_position_id = None
        if legacy_payload is not None:
            legacy_rank = int(legacy_payload["r"])
            legacy_headcount = int(legacy_payload["h"])
            legacy_position_id = str(UUID(legacy_payload["p"]))
            if legacy_rank not in (0, 1, 2) or legacy_headcount < 1:
                raise ValueError
        if (
            version != 2
            or payload["c"] != expected_context
            or not isinstance(ranking_fingerprint, str)
            or len(ranking_fingerprint) != 24
        ):
            raise ValueError
        return WorkspaceCursor(
            context=payload["c"],
            ranking_fingerprint=ranking_fingerprint,
            position_id=position_id,
            legacy_eligibility_rank=legacy_rank,
            legacy_headcount=legacy_headcount,
            legacy_position_id=legacy_position_id,
        )
    except PositionDiscoveryCursorError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PositionDiscoveryCursorError(
            "The workspace cursor is invalid or its ranking evidence has changed"
        ) from exc


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
effective_positions as (
  select
    position.position_id,
    case when snapshot.snapshot_id is not null
      then snapshot.year else position.year end as year,
    case when snapshot.snapshot_id is not null
      then snapshot.position_code else position.position_code end as position_code,
    case when snapshot.snapshot_id is not null
      then snapshot.name else position.name end as name,
    case when snapshot.snapshot_id is not null
      then snapshot.department_full else position.department_full
    end as department_full,
    case when snapshot.snapshot_id is not null
      then snapshot.system_code else position.system_code end as system_code,
    case when snapshot.snapshot_id is not null
      then snapshot.region else position.region end as region,
    case when snapshot.snapshot_id is not null
      then snapshot.city else position.city end as city,
    case when snapshot.snapshot_id is not null
      then snapshot.paper_type else position.paper_type end as paper_type,
    case when snapshot.snapshot_id is not null
      then snapshot.headcount else position.headcount end as headcount,
    case when snapshot.snapshot_id is not null
      then snapshot.has_extra_exam else position.has_extra_exam
    end as has_extra_exam
  from product.positions position
  left join product.position_import_batches import_batch
    on import_batch.position_data_version = :position_data_version
  left join product.position_snapshots snapshot
    on snapshot.position_id = position.position_id
   and snapshot.position_data_version = :position_data_version
  where import_batch.position_data_version is null
     or snapshot.snapshot_id is not null
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
    compilation.compilation_id,
    coalesce(compilation.warnings, '[]'::jsonb)
      @> '["source_conditions_incomplete"]'::jsonb
      as source_conditions_incomplete
  from effective_positions position
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
    count(checks.requirement_id) filter (where checks.check_status = 'needs_confirmation')
      + case when candidate.source_conditions_incomplete then 1 else 0 end
      as pending_count,
    count(checks.requirement_id) filter (where checks.check_status = 'not_met') as not_met_count,
    coalesce(
      array_agg(checks.requirement_id::text order by checks.requirement_id::text)
        filter (where checks.check_status = 'needs_confirmation'),
      array[]::text[]
    ) || case
      when candidate.source_conditions_incomplete
      then array['source-conditions-incomplete']::text[]
      else array[]::text[]
    end as pending_requirement_ids,
    coalesce(
      array_agg(checks.requirement_id::text order by checks.requirement_id::text)
        filter (where checks.check_status = 'not_met'),
      array[]::text[]
    ) as not_met_requirement_ids,
    case
      when candidate.compilation_id is null then 'needs_confirmation'
      when count(checks.requirement_id) filter (where checks.check_status = 'not_met') > 0 then 'ineligible'
      when candidate.source_conditions_incomplete then 'needs_confirmation'
      when count(checks.requirement_id) filter (where checks.check_status = 'needs_confirmation') > 0 then 'needs_confirmation'
      else 'eligible'
    end as eligibility_status
  from candidate_positions candidate
  left join requirement_checks checks on checks.position_id = candidate.position_id
  group by candidate.position_id, candidate.position_code, candidate.name,
           candidate.department_full, candidate.system_code, candidate.region,
           candidate.city, candidate.paper_type, candidate.headcount,
           candidate.has_extra_exam, candidate.compilation_id,
           candidate.source_conditions_incomplete
),
annual_scores as (
  select score.position_id, score.score_year as year,
         avg(score.entry_score_min) as average_score,
         count(*) as sample_count
  from product.position_score_snapshots score
  join candidate_positions candidate
    on candidate.position_id = score.position_id
  where score.position_data_version = :position_data_version
  group by score.position_id, score.score_year
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
  left join learner.current_primary_position_goals goal
    on goal.position_id = eligibility.position_id
   and goal.user_id = cast(:user_id as uuid)
   and goal.position_data_version = cast(:position_data_version as text)
   and goal.target_score_valid_until = cast(:current_window_closes_at as timestamptz)
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
