from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.ability_estimation.schemas import ReportedScoreEvidenceRequest
from app.domains.ability_estimation.service import AbilityEstimateService
from app.domains.auth.repository import token_hash_for_tests
from app.domains.positions.discovery import (
    PositionComparisonError,
    PositionDiscoveryCursorError,
    PositionDiscoveryService,
)
from app.domains.qualification.gaps import QualificationGapService
from app.domains.qualification.repository import (
    FactEvidenceInput,
    FactMutation,
    QualificationRepository,
)
from app.domains.qualification.rule_releases import PositionRuleReleaseService
from app.main import create_app


NOW = datetime(2026, 7, 12, 18, tzinfo=UTC)
COMPARISON_TOKEN = "position-comparison-route-token"


def _seed_discovery_world(
    factory: sessionmaker[Session],
    *,
    extra_eligible_count: int = 0,
    primary_goal_key: str | None = None,
    source_complete: bool = True,
) -> tuple[str, dict[str, str]]:
    user_id = str(uuid4())
    specs = {
        "eligible": {
            "education": "本科及以上",
            "major": "法学",
            "remark": None,
            "service": 2,
            "headcount": 2,
            "region": "江西",
            "city": "赣州市",
        },
        "no_conditions": {
            "education": "不限",
            "major": "不限",
            "remark": "无特殊要求",
            "service": 0,
            "headcount": 1,
            "region": "江西",
            "city": "南昌市",
        },
        "pending": {
            "education": "本科",
            "major": "法学类",
            "remark": "须持有法律职业资格证书A证",
            "service": 0,
            "headcount": 5,
            "region": "江西",
            "city": "赣州市",
        },
        "ineligible": {
            "education": "硕士研究生",
            "major": "法学",
            "remark": None,
            "service": 0,
            "headcount": 8,
            "region": "北京",
            "city": "北京市",
        },
    }
    position_ids: dict[str, str] = {}
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
            {"id": user_id},
        )
        for index, (key, spec) in enumerate(specs.items()):
            position_id = str(uuid4())
            position_ids[key] = position_id
            session.execute(
                text(
                    """
                    insert into product.positions (
                      position_id, source_id, year, position_code, name,
                      department_full, system_code, region, city, paper_type,
                      headcount, education_min, major_required, political_req,
                      service_min_yrs, remark, raw_json
                    ) values (
                      :position_id, :source_id, 2026, :position_code, :name,
                      :department, 'tax', :region, :city, 'administrativeLaw',
                      :headcount, :education, :major, '不限', :service, :remark,
                      cast(:raw_json as jsonb)
                    )
                    """
                ),
                {
                    "position_id": position_id,
                    "source_id": f"discovery-{key}",
                    "position_code": f"30011000900{index}",
                    "name": f"{key}-一级行政执法员",
                    "department": f"国家税务总局{spec['city']}测试局",
                    "raw_json": "{}" if source_complete else None,
                    **spec,
                },
            )
        for index in range(extra_eligible_count):
            key = f"extra_eligible_{index}"
            position_id = str(uuid4())
            position_ids[key] = position_id
            session.execute(
                text(
                    """
                    insert into product.positions (
                      position_id, source_id, year, position_code, name,
                      department_full, system_code, region, city, paper_type,
                      headcount, education_min, major_required, political_req,
                      service_min_yrs, remark, raw_json
                    ) values (
                      :position_id, :source_id, 2026, :position_code, :name,
                      :department, 'tax', '江西', '南昌市', 'administrativeLaw',
                      :headcount, '不限', '不限', '不限', 0, '无特殊要求',
                      cast(:raw_json as jsonb)
                    )
                    """
                ),
                {
                    "position_id": position_id,
                    "source_id": f"discovery-{key}",
                    "position_code": f"300110019{index:03d}",
                    "name": f"extra-{index}-一级行政执法员",
                    "department": f"国家税务总局额外测试局{index}",
                    "headcount": 1000 - index,
                    "raw_json": "{}" if source_complete else None,
                },
            )
        repository = QualificationRepository()
        repository.replace_facts(
            session,
            user_id=user_id,
            command_id="discovery-profile-v1",
            mutations=[
                FactMutation(
                    key="education",
                    value="本科",
                    verification_status="reported",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                ),
                FactMutation(
                    key="grassroots_years",
                    value=2,
                    verification_status="reported",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                ),
                FactMutation(
                    key="major",
                    value="法学",
                    verification_status="reported",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                ),
            ],
            now=NOW,
        )
        release_service = PositionRuleReleaseService()
        release_id = release_service.ensure_draft(
            session,
            exam_code="guokao",
            exam_year=2026,
            rule_set_version="guokao-2026-rules-v1",
            position_data_version="guokao-2026-discovery-v1",
        )
        release_service.compile_draft(session, release_id=release_id, compiled_at=NOW)
        release_service.activate(session, release_id=release_id, activated_at=NOW)
        _insert_score(session, position_ids["eligible"], 2024, 121.5)
        _insert_score(session, position_ids["eligible"], 2025, 123.5)
        if primary_goal_key is not None:
            _insert_confirmed_primary_goal(
                session,
                user_id=user_id,
                position_id=position_ids[primary_goal_key],
            )
    return user_id, position_ids


def test_discovery_orders_by_eligibility_before_headcount_and_exposes_direct_score(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    with db_session_factory() as session:
        result = PositionDiscoveryService().search(
            session,
            user_id=user_id,
            exam_year=None,
            region=None,
            city=None,
            query=None,
            limit=10,
            cursor=None,
        )

    ordered_ids = [item.position_id for item in result.items]
    assert ordered_ids[0] == ids["no_conditions"]
    assert set(ordered_ids[1:3]) == {ids["eligible"], ids["pending"]}
    assert ordered_ids[3] == ids["ineligible"]
    assert [item.eligibility_status for item in result.items] == [
        "eligible",
        "needs_confirmation",
        "needs_confirmation",
        "ineligible",
    ]
    catalog_pending = next(item for item in result.items if item.position_id == ids["eligible"])
    assert catalog_pending.met_count == 2
    assert catalog_pending.pending_count == 1
    assert catalog_pending.target_score is not None
    assert catalog_pending.target_score.recommended == 124.48
    assert catalog_pending.target_score.evidence_level == "direct"
    assert "conditions_need_confirmation" in catalog_pending.reason_codes
    pending = next(item for item in result.items if item.position_id == ids["pending"])
    assert "conditions_need_confirmation" in pending.reason_codes
    assert "hard_condition_not_met" in result.items[3].reason_codes


def test_discovery_never_claims_eligible_when_release_source_is_incomplete(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(
        db_session_factory,
        source_complete=False,
    )
    with db_session_factory() as session:
        result = PositionDiscoveryService().search(
            session,
            user_id=user_id,
            exam_year=2026,
            region="江西",
            city="赣州市",
            query="eligible",
            limit=10,
            cursor=None,
        )

    assert [item.position_id for item in result.items] == [ids["eligible"]]
    item = result.items[0]
    assert item.eligibility_status == "needs_confirmation"
    assert item.pending_count == 2
    assert "source-conditions-incomplete" in item.pending_requirement_ids
    assert "source_conditions_incomplete" in item.reason_codes


def test_discovery_cursor_is_lossless_and_bound_to_profile_and_filters(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    service = PositionDiscoveryService()
    with db_session_factory() as session:
        first = service.search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=2,
            cursor=None,
        )
        second = service.search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=2,
            cursor=first.next_cursor,
        )
    assert first.has_more is True
    assert second.has_more is False
    assert {item.position_id for item in first.items + second.items} == set(ids.values())

    with db_session_factory.begin() as session:
        QualificationRepository().replace_facts(
            session,
            user_id=user_id,
            command_id="discovery-profile-v2",
            mutations=[
                FactMutation(
                    key="political_status",
                    value="群众",
                    verification_status="reported",
                    evidence=(FactEvidenceInput(source_type="learner_report"),),
                )
            ],
            now=NOW,
        )
    with pytest.raises(PositionDiscoveryCursorError):
        with db_session_factory() as session:
            service.search(
                session,
                user_id=user_id,
                exam_year=2026,
                region=None,
                city=None,
                query=None,
                limit=2,
                cursor=first.next_cursor,
            )


def test_discovery_applies_region_city_and_text_filters_before_evaluation(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    with db_session_factory() as session:
        result = PositionDiscoveryService().search(
            session,
            user_id=user_id,
            exam_year=2026,
            region="江西",
            city="赣州市",
            query="pending",
            limit=20,
            cursor=None,
        )
    assert [item.position_id for item in result.items] == [ids["pending"]]


def test_comparison_preserves_selection_order_and_returns_deterministic_highlights(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    selected = [ids["pending"], ids["eligible"], ids["ineligible"]]
    with db_session_factory() as session:
        result = PositionDiscoveryService().compare(
            session,
            user_id=user_id,
            position_ids=selected,
        )

    assert [item.position_id for item in result.items] == selected
    assert result.learner_profile_version == 2
    assert result.rule_set_version == "guokao-2026-rules-v1"
    assert result.position_data_version == "guokao-2026-discovery-v1"
    assert result.items[0].paper_type == "administrativeLaw"
    assert result.highlights.confirmed_eligible_position_ids == []
    assert result.highlights.largest_headcount_position_ids == [ids["ineligible"]]
    assert result.highlights.lowest_target_score_position_ids == [ids["eligible"]]


def test_comparison_rejects_positions_outside_the_active_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    with pytest.raises(PositionComparisonError, match="active guokao release"):
        with db_session_factory() as session:
            PositionDiscoveryService().compare(
                session,
                user_id=user_id,
                position_ids=[ids["eligible"], str(uuid4())],
            )


def test_comparison_binds_facts_to_the_reported_profile_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)

    class ConcurrentProfileMutation(QualificationRepository):
        def get_profile_version(self, session: Session, *, user_id: str) -> int:
            profile_version = super().get_profile_version(session, user_id=user_id)
            self.replace_facts(
                session,
                user_id=user_id,
                command_id="concurrent-profile-change",
                mutations=[
                    FactMutation(
                        key="education",
                        value="硕士研究生",
                        verification_status="reported",
                        evidence=(FactEvidenceInput(source_type="learner_report"),),
                    )
                ],
                now=NOW + timedelta(minutes=1),
            )
            return profile_version

    with db_session_factory() as session:
        result = PositionDiscoveryService(ConcurrentProfileMutation()).compare(
            session,
            user_id=user_id,
            position_ids=[ids["ineligible"], ids["eligible"]],
        )

    assert result.learner_profile_version == 2
    assert result.items[0].eligibility_status == "ineligible"
    assert result.items[1].eligibility_status == "needs_confirmation"


def test_qualification_gaps_only_reports_unresolved_inputs_that_affect_positions(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)

    with db_session_factory() as session:
        summary = QualificationGapService().summarize(session, user_id=user_id)

    assert summary.learner_profile_version == 2
    assert summary.rule_set_version == "guokao-2026-rules-v1"
    assert summary.position_data_version == "guokao-2026-discovery-v1"
    assert summary.total_affected_positions == 3
    assert summary.complete is True
    assert {gap.fact_key for gap in summary.gaps} == {"major", "other_conditions"}
    gaps = {gap.fact_key: gap for gap in summary.gaps}
    major_gap = gaps["major"]
    assert major_gap.gap_type == "position_confirmation"
    assert major_gap.action == "review_position_requirement"
    assert major_gap.affected_position_count == 3
    assert set(major_gap.sample_position_ids) == {
        ids["eligible"],
        ids["pending"],
        ids["ineligible"],
    }
    assert major_gap.sample_position_codes == [
        "300110009000",
        "300110009002",
        "300110009003",
    ]
    other_gap = gaps["other_conditions"]
    assert other_gap.gap_type == "position_confirmation"
    assert other_gap.action == "review_position_requirement"
    assert other_gap.affected_position_count == 1
    assert other_gap.sample_position_ids == [ids["pending"]]
    assert other_gap.sample_position_codes == ["300110009002"]


def test_qualification_gaps_are_user_isolated_and_report_missing_facts(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_discovery_world(db_session_factory)
    user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '新学习者')"),
            {"id": user_id},
        )
        session.execute(
            text("insert into learner.profiles (user_id, profile_version) values (:id, 1)"),
            {"id": user_id},
        )

    with db_session_factory() as session:
        summary = QualificationGapService().summarize(session, user_id=user_id)

    gaps = {(gap.fact_key, gap.gap_type): gap for gap in summary.gaps}
    assert gaps[("education", "missing_fact")].affected_position_count == 3
    assert gaps[("grassroots_years", "missing_fact")].affected_position_count == 1
    assert gaps[("major", "position_confirmation")].affected_position_count == 3
    assert gaps[("other_conditions", "position_confirmation")].affected_position_count == 1
    assert summary.total_affected_positions == 3
    assert summary.complete is False


def test_comparison_route_uses_authenticated_profile_and_returns_no_winner(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    _seed_session(db_session_factory, user_id=user_id, token=COMPARISON_TOKEN)
    selected = [ids["pending"], ids["eligible"]]

    response = _client(db_session_factory).post(
        "/api/v2/positions/compare",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
        json={"positionIds": selected},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requestId"]
    assert [item["positionId"] for item in body["data"]["items"]] == selected
    assert body["data"]["learnerProfileVersion"] == 2
    assert body["data"]["ruleSetVersion"] == "guokao-2026-rules-v1"
    assert "winnerPositionId" not in body["data"]
    assert "recommendedPositionId" not in body["data"]

    gaps = _client(db_session_factory).get(
        "/api/v2/qualification/gaps",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )
    assert gaps.status_code == 200
    gap_data = gaps.json()["data"]
    assert gap_data["learnerProfileVersion"] == 2
    assert gap_data["gaps"][0]["factKey"] == "major"
    assert set(gap_data["gaps"][0]["samplePositionIds"]) == {
        ids["eligible"],
        ids["pending"],
        ids["ineligible"],
    }


def test_workspace_and_fit_routes_bind_active_versions_ability_and_hard_requirements(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    _seed_session(db_session_factory, user_id=user_id, token=COMPARISON_TOKEN)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.positions set has_extra_exam = true "
                "where position_id = cast(:position_id as uuid)"
            ),
            {"position_id": ids["eligible"]},
        )

    client = _client(db_session_factory)
    unavailable = client.get(
        "/api/v2/positions/workspace",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )
    assert unavailable.status_code == 200
    unavailable_data = unavailable.json()["data"]
    assert unavailable_data["examYear"] == 2026
    assert unavailable_data["abilityEstimate"]["readiness"] == "unavailable"
    unavailable_eligible = next(
        item for item in unavailable_data["items"] if item["positionId"] == ids["eligible"]
    )
    assert unavailable_eligible["fit"]["status"] == "evidence_unavailable"
    assert unavailable_eligible["fit"]["evidenceStatus"] == "unavailable"
    assert unavailable_eligible["fit"]["band"] is None
    assert unavailable_eligible["fit"]["scoreGap"] is None
    assert unavailable_eligible["fit"]["evidenceReasonCodes"] == ["ability_estimate_unavailable"]

    pending = next(
        item for item in unavailable_data["items"] if item["positionId"] == ids["pending"]
    )
    pending_burden = pending["fit"]["requirementsBurden"]
    assert pending_burden["pendingRequirementCount"] > 0
    assert pending_burden["pendingRequirementCount"] == len(pending_burden["pendingRequirementIds"])
    assert pending_burden["hardConditionNotMetCount"] == len(
        pending_burden["hardConditionNotMetRequirementIds"]
    )
    assert "studyHours" not in pending_burden

    with db_session_factory.begin() as session:
        AbilityEstimateService().record_reported_score(
            session,
            user_id=user_id,
            command_id="position-workspace-complete-ability",
            report=ReportedScoreEvidenceRequest(
                reported_context="full_mock",
                xingce_score=65,
                shenlun_score=55,
                timed_status="timed",
                occurred_at=NOW + timedelta(hours=1),
            ),
            now=NOW + timedelta(hours=2),
        )

    ready = client.get(
        "/api/v2/positions/workspace",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )
    assert ready.status_code == 200
    ready_data = ready.json()["data"]
    assert ready_data["abilityEstimate"]["readiness"] == "complete"
    ready_eligible = next(
        item for item in ready_data["items"] if item["positionId"] == ids["eligible"]
    )
    fit = ready_eligible["fit"]
    assert fit["status"] == "overlap"
    assert fit["evidenceStatus"] == "ready"
    assert fit["band"] == "overlap"
    assert fit["evidenceReasonCodes"] == []
    assert fit["scoreGap"]["unit"] == "points"
    assert fit["scoreGap"]["direction"] == "positive_means_target_above_current"
    assert fit["scoreGap"]["lower"] == round(
        ready_eligible["targetScore"]["lowerBound"]
        - ready_data["abilityEstimate"]["estimate"]["total"]["upper"],
        2,
    )
    assert fit["scoreGap"]["recommended"] == round(
        ready_eligible["targetScore"]["recommended"]
        - ready_data["abilityEstimate"]["estimate"]["total"]["recommended"],
        2,
    )
    assert fit["scoreGap"]["upper"] == round(
        ready_eligible["targetScore"]["upperBound"]
        - ready_data["abilityEstimate"]["estimate"]["total"]["lower"],
        2,
    )
    assert fit["requirementsBurden"]["additionalExamRequired"] is True

    fit_response = client.get(
        f"/api/v2/positions/{ids['eligible']}/fit",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )
    assert fit_response.status_code == 200
    assert fit_response.json()["data"]["fit"] == fit
    assert fit_response.json()["data"]["position"]["positionId"] == ids["eligible"]

    detail = client.get(
        f"/api/v2/positions/{ids['eligible']}",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["fit"] == fit


def test_workspace_ranks_a_bounded_pool_by_qualification_fit_and_burden_before_headcount(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(
        db_session_factory,
        extra_eligible_count=9,
    )
    high_headcount_id = ids["extra_eligible_0"]
    better_fit_outside_old_page_id = ids["extra_eligible_8"]
    with db_session_factory.begin() as session:
        for year in (2024, 2025):
            _insert_score(
                session,
                high_headcount_id,
                year,
                145,
                evidence_position_code="300110009101",
            )
            _insert_score(
                session,
                better_fit_outside_old_page_id,
                year,
                112,
                evidence_position_code="300110009102",
            )
        AbilityEstimateService().record_reported_score(
            session,
            user_id=user_id,
            command_id="position-workspace-ranking-ability",
            report=ReportedScoreEvidenceRequest(
                reported_context="full_mock",
                xingce_score=65,
                shenlun_score=60,
                timed_status="timed",
                occurred_at=NOW + timedelta(hours=1),
            ),
            now=NOW + timedelta(hours=2),
        )

    service = PositionDiscoveryService()
    with db_session_factory() as session:
        legacy_page = service.search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=1,
            cursor=None,
            now=NOW + timedelta(hours=3),
        )
        workspace = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=None,
            now=NOW + timedelta(hours=3),
        )
        second = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=workspace.next_cursor,
            now=NOW + timedelta(hours=3),
        )
        transitioned = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=legacy_page.next_cursor,
            now=NOW + timedelta(hours=3),
        )
        transitioned_next = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=transitioned.next_cursor,
            now=NOW + timedelta(hours=3),
        )

    first_ids = [item.position_id for item in workspace.items]
    paged_ids = [item.position_id for item in [*workspace.items, *second.items]]
    assert better_fit_outside_old_page_id in first_ids
    assert paged_ids.index(better_fit_outside_old_page_id) < paged_ids.index(high_headcount_id)
    assert all(item.eligibility_status == "eligible" for item in workspace.items)
    assert len(set(paged_ids)) == len(paged_ids)
    assert workspace.next_cursor is not None
    assert [item.position_id for item in legacy_page.items] == [high_headcount_id]
    assert high_headcount_id not in {item.position_id for item in transitioned.items}
    assert better_fit_outside_old_page_id in {item.position_id for item in transitioned.items}
    transitioned_ids = [
        item.position_id for item in [*transitioned.items, *transitioned_next.items]
    ]
    assert len(set(transitioned_ids)) == len(transitioned_ids)
    assert high_headcount_id not in transitioned_ids

    with db_session_factory.begin() as session:
        AbilityEstimateService().record_reported_score(
            session,
            user_id=user_id,
            command_id="position-workspace-ranking-ability-v2",
            report=ReportedScoreEvidenceRequest(
                reported_context="full_mock",
                xingce_score=70,
                shenlun_score=65,
                timed_status="timed",
                occurred_at=NOW + timedelta(hours=4),
            ),
            now=NOW + timedelta(hours=5),
        )
    with pytest.raises(PositionDiscoveryCursorError):
        with db_session_factory() as session:
            service.workspace(
                session,
                user_id=user_id,
                region=None,
                city=None,
                query=None,
                limit=8,
                cursor=workspace.next_cursor,
                now=NOW + timedelta(hours=6),
            )


def test_workspace_pool_caps_at_200_and_rejects_a_legacy_cursor_at_candidate_201(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(
        db_session_factory,
        extra_eligible_count=201,
    )
    service = PositionDiscoveryService()

    with db_session_factory() as session:
        search_200 = service.search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=200,
            cursor=None,
            now=NOW + timedelta(hours=1),
        )
        search_201 = service.search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=201,
            cursor=None,
            now=NOW + timedelta(hours=1),
        )
        first_workspace_page = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=199,
            cursor=None,
            now=NOW + timedelta(hours=1),
        )
        final_workspace_page = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=199,
            cursor=first_workspace_page.next_cursor,
            now=NOW + timedelta(hours=1),
        )
        after_legacy_pool_boundary = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=search_200.next_cursor,
            now=NOW + timedelta(hours=1),
        )

    workspace_ids = [
        item.position_id
        for item in [
            *first_workspace_page.items,
            *final_workspace_page.items,
        ]
    ]
    assert search_200.items[-1].position_id == ids["extra_eligible_199"]
    assert search_201.items[-1].position_id == ids["extra_eligible_200"]
    assert len(workspace_ids) == len(set(workspace_ids)) == 200
    assert ids["extra_eligible_199"] in workspace_ids
    assert ids["extra_eligible_200"] not in workspace_ids
    assert first_workspace_page.has_more is True
    assert first_workspace_page.next_cursor is not None
    assert [item.position_id for item in final_workspace_page.items] == [ids["extra_eligible_199"]]
    assert final_workspace_page.has_more is False
    assert final_workspace_page.next_cursor is None
    assert after_legacy_pool_boundary.items == []
    assert after_legacy_pool_boundary.has_more is False

    with pytest.raises(
        PositionDiscoveryCursorError,
        match="outside the bounded candidate pool",
    ):
        with db_session_factory() as session:
            service.workspace(
                session,
                user_id=user_id,
                region=None,
                city=None,
                query=None,
                limit=8,
                cursor=search_201.next_cursor,
                now=NOW + timedelta(hours=1),
            )


def test_workspace_pins_confirmed_primary_goal_outside_the_first_candidate_page(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(
        db_session_factory,
        extra_eligible_count=9,
        primary_goal_key="eligible",
    )
    service = PositionDiscoveryService()

    with db_session_factory() as session:
        ordinary_page = service.search(
            session,
            user_id=user_id,
            exam_year=2026,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=None,
        )
        workspace = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=None,
        )
        next_workspace_page = service.workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=workspace.next_cursor,
        )
        filtered_workspace = service.workspace(
            session,
            user_id=user_id,
            region="北京",
            city=None,
            query=None,
            limit=8,
            cursor=None,
        )

    assert ids["eligible"] not in {item.position_id for item in ordinary_page.items}
    assert workspace.items[0].position_id == ids["eligible"]
    assert workspace.items[0].is_primary_goal is True
    assert len(workspace.items) == 9
    assert len({item.position_id for item in workspace.items}) == 9
    assert workspace.has_more is ordinary_page.has_more
    candidate_ids = [
        item.position_id
        for item in [*workspace.items, *next_workspace_page.items]
        if not item.is_primary_goal
    ]
    assert ids["eligible"] not in candidate_ids
    assert len(candidate_ids) == len(set(candidate_ids)) == len(ids) - 1
    assert next_workspace_page.has_more is False
    assert filtered_workspace.items[0].position_id == ids["eligible"]
    assert [item.position_id for item in filtered_workspace.items if not item.is_primary_goal] == [
        ids["ineligible"]
    ]


def test_workspace_preserves_mixed_eligibility_facts_for_truthful_client_counts(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, _ = _seed_discovery_world(db_session_factory)

    with db_session_factory() as session:
        workspace = PositionDiscoveryService().workspace(
            session,
            user_id=user_id,
            region=None,
            city=None,
            query=None,
            limit=8,
            cursor=None,
        )

    statuses = [item.eligibility_status for item in workspace.items]
    assert statuses == [
        "eligible",
        "needs_confirmation",
        "needs_confirmation",
        "ineligible",
    ]
    assert sum(status == "eligible" for status in statuses) == 1
    assert sum(status == "needs_confirmation" for status in statuses) == 2


def test_workspace_and_fit_reject_expired_target_basis_with_stable_error(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id, ids = _seed_discovery_world(db_session_factory)
    _seed_session(db_session_factory, user_id=user_id, token=COMPARISON_TOKEN)
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.position_rule_releases "
                "set activated_at = :expired_at where status = 'active'"
            ),
            {"expired_at": datetime.now(UTC) - timedelta(days=366)},
        )

    client = _client(db_session_factory)
    workspace = client.get(
        "/api/v2/positions/workspace",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )
    fit = client.get(
        f"/api/v2/positions/{ids['eligible']}/fit",
        headers={"Authorization": f"Bearer {COMPARISON_TOKEN}"},
    )

    assert workspace.status_code == 409
    assert workspace.json()["code"] == "target_score_basis_expired"
    assert fit.status_code == 409
    assert fit.json()["code"] == "target_score_basis_expired"


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)


def _seed_session(factory: sessionmaker[Session], *, user_id: str, token: str) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  :session_id, :user_id, :token_hash, 'wechat-comparison-test', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )


def _insert_score(
    session: Session,
    position_id: str,
    year: int,
    score: float,
    *,
    evidence_position_code: str = "300110009000",
) -> None:
    evidence_id = session.execute(
        text(
            """
            insert into product.position_entry_score_evidence (
              year, department_code, position_code, agency, bureau,
              position_title, entry_score_min, source_name,
              source_file_sha256, candidate_count
            ) values (
              :year, '360000', :evidence_position_code, '国家税务总局测试局',
              '机关处室', '一级行政执法员', :score, 'fixture', :sha, 5
            ) returning evidence_id
            """
        ),
        {
            "year": year,
            "score": score,
            "evidence_position_code": evidence_position_code,
            "sha": f"{year}{evidence_position_code}".zfill(64)[-64:],
        },
    ).scalar_one()
    session.execute(
        text(
            """
            insert into product.position_entry_score_links (
              position_id, evidence_id, match_method, confidence
            ) values (:position_id, :evidence_id, 'fixture', 'high')
            """
        ),
        {"position_id": position_id, "evidence_id": evidence_id},
    )


def _insert_confirmed_primary_goal(
    session: Session,
    *,
    user_id: str,
    position_id: str,
) -> None:
    snapshot_id = str(uuid4())
    session.execute(
        text(
            """
            insert into learner.eligibility_snapshots (
              snapshot_id, user_id, position_id, learner_profile_version,
              rule_set_version, position_data_version, status, checks,
              missing_fact_keys, evaluated_at
            ) values (
              cast(:snapshot_id as uuid), cast(:user_id as uuid),
              cast(:position_id as uuid), 2, 'guokao-2026-rules-v1',
              'guokao-2026-discovery-v1', 'eligible', '[]'::jsonb,
              '[]'::jsonb, :evaluated_at
            )
            """
        ),
        {
            "snapshot_id": snapshot_id,
            "user_id": user_id,
            "position_id": position_id,
            "evaluated_at": NOW,
        },
    )
    session.execute(
        text(
            """
            insert into learner.position_goal_versions (
              goal_version_id, user_id, position_id, role, status,
              target_score_lower, target_score_recommended, target_score_upper,
              target_score_evidence_level, target_score_sample_count,
              target_score_valid_until, learner_profile_version,
              eligibility_snapshot_id, position_data_version, created_at
            ) values (
              cast(:goal_version_id as uuid), cast(:user_id as uuid),
              cast(:position_id as uuid), 'primary', 'confirmed',
              121, 125, 129, 'direct', 2, :valid_until, 2,
              cast(:snapshot_id as uuid), 'guokao-2026-discovery-v1', :created_at
            )
            """
        ),
        {
            "goal_version_id": str(uuid4()),
            "user_id": user_id,
            "position_id": position_id,
            "valid_until": NOW + timedelta(days=180),
            "snapshot_id": snapshot_id,
            "created_at": NOW,
        },
    )
