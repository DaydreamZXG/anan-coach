from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from uuid import uuid4

from openpyxl import Workbook
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.positions.discovery import PositionDiscoveryService
from app.domains.positions.target_scores import PositionTargetScoreOwner
from app.domains.qualification.application import QualificationApplicationService
from app.domains.qualification.repository import (
    FactEvidenceInput,
    FactMutation,
    QualificationRepository,
)
from app.domains.qualification.rule_releases import (
    PositionReleaseWindowInput,
    PositionRuleReleaseService,
)
from app.importers.gwy_positions import (
    import_gwy_positions,
    verify_gwy_position_import,
)
from app.importers.guokao_official_positions import OFFICIAL_HEADERS


def test_import_gwy_positions_upserts_and_links_scores(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gwy.db"
    _create_source_db(source)

    first = import_gwy_positions(
        session=db_session,
        source_path=source,
        position_data_version="gwy-fixture-v1",
        batch_size=2,
    )
    second = import_gwy_positions(
        session=db_session,
        source_path=source,
        position_data_version="gwy-fixture-v1",
        batch_size=2,
    )
    verification = verify_gwy_position_import(
        db_session,
        source_path=source,
        position_data_version="gwy-fixture-v1",
    )

    assert first.positions == 3
    assert first.position_score_histories == 1
    assert first.position_score_links == 2
    assert second.positions == 3
    assert verification["ok"] is True
    assert verification["target_counts"]["positions"] == 3
    assert verification["target_counts"]["position_score_links"] == 2
    assert verification["coverage_by_year"] == [
        {"year": 2024, "positions": 1, "positions_with_score": 0, "pct": 0.0},
        {"year": 2026, "positions": 2, "positions_with_score": 2, "pct": 100.0},
    ]

    row = (
        db_session.execute(
            text(
                """
            select p.major_required, h.entry_score_min, h.signup_count, l.confidence
            from product.positions p
            join product.position_score_links l on l.position_id = p.position_id
            join product.position_score_histories h on h.score_history_id = l.score_history_id
            where p.source_id = 'gk-2026-00001'
            """
            )
        )
        .mappings()
        .one()
    )
    assert row["major_required"] == "法学类"
    assert float(row["entry_score_min"]) == 121.5
    assert int(row["signup_count"]) == 42
    assert row["confidence"] == "shared_code_year"


def test_import_gwy_positions_enriches_every_official_row_and_keeps_source_proof(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gwy.db"
    official = tmp_path / "guokao-2026-official.xlsx"
    _create_source_db(source)
    _create_official_workbook(official)

    result = import_gwy_positions(
        session=db_session,
        source_path=source,
        official_source_path=official,
        official_exam_year=2026,
        position_data_version="guokao-2026-official-v1",
        batch_size=2,
    )
    verification = verify_gwy_position_import(
        db_session,
        source_path=source,
        official_source_path=official,
        official_exam_year=2026,
        position_data_version="guokao-2026-official-v1",
    )

    assert result.official_positions_enriched == 2
    assert verification["ok"] is True
    assert verification["official_catalog"]["source_positions"] == 2
    assert verification["official_catalog"]["verified_positions"] == 2

    row = (
        db_session.execute(
            text(
                """
            select education_min, remark,
                   raw_json ->> 'contractVersion' as contract_version,
                   raw_json ->> 'positionDataVersion' as position_data_version,
                   raw_json #>> '{officialFields,degree}' as degree,
                   raw_json #>> '{officialFields,serviceProject}' as service_project
            from product.positions
            where source_id = 'gk-2026-00001'
            """
            )
        )
        .mappings()
        .one()
    )
    assert row["education_min"] == "本科及以上"
    assert row["remark"] == "限应届高校毕业生；大学英语四级425分及以上。"
    assert row["contract_version"] == "guokao-official-position@v1"
    assert row["position_data_version"] == "guokao-2026-official-v1"
    assert row["degree"] == "与最高学历相对应的学位"
    assert row["service_project"] == "无限制"


def test_position_import_snapshots_switch_atomically_with_the_active_release(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gwy-snapshot.db"
    _create_source_db(source)
    with sqlite3.connect(source) as connection:
        # A position data version is bound to one exam year by the release
        # authority; this fixture exercises the 2026 release only.
        connection.execute("delete from positions where year <> 2026")
        connection.execute(
            """
            update positions
            set major_required = '不限',
                political_req = '不限',
                hukou_req = '不限',
                service_min_yrs = 0,
                remark = null,
                raw_json = '{}'
            where year = 2026
            """
        )
    import_gwy_positions(
        session=db_session,
        source_path=source,
        position_data_version="snapshot-fixture-v1",
    )

    target_position_id = str(
        db_session.execute(
            text(
                """
                select position_id
                from product.positions
                where source_id = 'gk-2026-00001'
                """
            )
        ).scalar_one()
    )
    peer_position_id = str(
        db_session.execute(
            text(
                """
                select position_id
                from product.positions
                where source_id = 'gk-2026-00002'
                """
            )
        ).scalar_one()
    )

    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            update positions
            set name = '海关科技管理岗位',
                system_code = 'customs',
                headcount = 9,
                education_min = '博士研究生',
                major_required = '不限',
                political_req = '不限',
                hukou_req = '不限',
                service_min_yrs = 0,
                remark = null,
                raw_json = '{}'
            where id = 'gk-2026-00001'
            """
        )
    import_gwy_positions(
        session=db_session,
        source_path=source,
        position_data_version="snapshot-fixture-v2",
    )

    stored_rows = (
        db_session.execute(
            text(
                """
            select
              position.name as legacy_name,
              v1.name as v1_name,
              v1.education_min as v1_education,
              v2.name as v2_name,
              v2.education_min as v2_education
            from product.positions position
            join product.position_snapshots v1
              on v1.position_id = position.position_id
             and v1.position_data_version = 'snapshot-fixture-v1'
            join product.position_snapshots v2
              on v2.position_id = position.position_id
             and v2.position_data_version = 'snapshot-fixture-v2'
            where position.position_id = cast(:position_id as uuid)
            """
            ),
            {"position_id": target_position_id},
        )
        .mappings()
        .one()
    )
    assert stored_rows == {
        "legacy_name": "一级行政执法员",
        "v1_name": "一级行政执法员",
        "v1_education": "本科",
        "v2_name": "海关科技管理岗位",
        "v2_education": "博士研究生",
    }

    db_session.execute(
        text(
            """
            delete from product.position_score_links
            where position_id = cast(:position_id as uuid)
            """
        ),
        {"position_id": target_position_id},
    )
    db_session.execute(
        text(
            """
            insert into product.position_entry_score_evidence (
              year, department_code, position_code, agency, bureau,
              position_title, entry_score_min, source_name,
              source_file_sha256, candidate_count
            ) values
              (
                2023, 'snapshot-peer-2023', '300110001001',
                '国家税务总局测试局B', '测试司局', '一级行政执法员',
                124, 'snapshot-fixture', :source_sha256, 3
              ),
              (
                2024, 'snapshot-peer-2024', '300110001001',
                '国家税务总局测试局B', '测试司局', '一级行政执法员',
                125, 'snapshot-fixture', :source_sha256, 3
              ),
              (
                2025, 'snapshot-peer-2025', '300110001001',
                '国家税务总局测试局B', '测试司局', '一级行政执法员',
                126, 'snapshot-fixture', :source_sha256, 3
              ),
              (
                2023, 'snapshot-target-2023', '300110001001',
                '国家税务总局测试局A', '测试司局', '一级行政执法员',
                120, 'snapshot-target-fixture', :source_sha256, 3
              ),
              (
                2024, 'snapshot-target-2024', '300110001001',
                '国家税务总局测试局A', '测试司局', '一级行政执法员',
                121, 'snapshot-target-fixture', :source_sha256, 3
              ),
              (
                2025, 'snapshot-target-2025', '300110001001',
                '国家税务总局测试局A', '测试司局', '一级行政执法员',
                122, 'snapshot-target-fixture', :source_sha256, 3
              )
            """
        ),
        {"source_sha256": "b" * 64},
    )
    db_session.execute(
        text(
            """
            insert into product.position_entry_score_links (
              position_id, evidence_id, match_method, confidence
            )
            select
              cast(:position_id as uuid), evidence_id, 'fixture', 'high'
            from product.position_entry_score_evidence
            where source_name = 'snapshot-fixture'
            """
        ),
        {"position_id": peer_position_id},
    )
    db_session.execute(
        text(
            """
            insert into product.position_entry_score_links (
              position_id, evidence_id, match_method, confidence
            )
            select
              cast(:position_id as uuid), evidence_id, 'fixture', 'high'
            from product.position_entry_score_evidence
            where source_name = 'snapshot-target-fixture'
            """
        ),
        {"position_id": target_position_id},
    )

    user_id = str(uuid4())
    checked_at = datetime(2026, 10, 20, 12, tzinfo=UTC)
    db_session.execute(
        text(
            """
            insert into learner.users (user_id, display_name)
            values (cast(:user_id as uuid), '快照回归学习者')
            """
        ),
        {"user_id": user_id},
    )
    db_session.execute(
        text(
            """
            insert into learner.profiles (
              user_id, profile_version, created_at, updated_at
            ) values (
              cast(:user_id as uuid), 1, :now, :now
            )
            """
        ),
        {"user_id": user_id, "now": checked_at},
    )
    QualificationRepository().replace_facts(
        db_session,
        user_id=user_id,
        command_id="snapshot-fixture-profile",
        mutations=[
            FactMutation(
                key="education",
                value="本科",
                verification_status="confirmed",
                evidence=(
                    FactEvidenceInput(
                        source_type="learner_report",
                        source_reference="snapshot-fixture",
                        recorded_at=checked_at,
                    ),
                ),
                effective_at=checked_at,
            )
        ],
        now=checked_at,
    )

    release_service = PositionRuleReleaseService()
    windows = [
        PositionReleaseWindowInput(
            kind="primary",
            sequence_no=1,
            opens_at=datetime(2026, 10, 15, tzinfo=UTC),
            closes_at=datetime(2026, 10, 31, tzinfo=UTC),
            official_source_url="https://example.gov.cn/snapshot-fixture",
            official_source_title="快照版本回归公告",
            official_source_published_at=datetime(2026, 9, 28, tzinfo=UTC),
            official_source_received_at=datetime(2026, 9, 29, tzinfo=UTC),
            official_source_sha256="c" * 64,
            official_source_receipt="snapshot-fixture-receipt",
        )
    ]
    release_ids: dict[str, str] = {}
    v1_release_id = release_service.ensure_draft(
        db_session,
        exam_code="guokao",
        exam_year=2026,
        rule_set_version="snapshot-rules-v1",
        position_data_version="snapshot-fixture-v1",
    )
    release_service.compile_draft(
        db_session,
        release_id=v1_release_id,
        compiled_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    release_service.configure_windows(
        db_session,
        release_id=v1_release_id,
        windows=windows,
    )
    release_ids["v1"] = v1_release_id
    release_service.activate(
        db_session,
        release_id=v1_release_id,
        activated_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    db_session.commit()

    db_session.execute(
        text(
            """
            update product.position_entry_score_evidence
            set entry_score_min = entry_score_min + 100
            where source_name = 'snapshot-target-fixture'
            """
        )
    )
    v2_release_id = release_service.ensure_draft(
        db_session,
        exam_code="guokao",
        exam_year=2026,
        rule_set_version="snapshot-rules-v2",
        position_data_version="snapshot-fixture-v2",
    )
    release_service.compile_draft(
        db_session,
        release_id=v2_release_id,
        compiled_at=datetime(2026, 10, 2, 1, tzinfo=UTC),
    )
    release_service.configure_windows(
        db_session,
        release_id=v2_release_id,
        windows=windows,
    )
    release_ids["v2"] = v2_release_id

    discovery = PositionDiscoveryService()
    qualification = QualificationApplicationService()
    target_scores = PositionTargetScoreOwner()

    v1_search = discovery.search(
        db_session,
        user_id=user_id,
        exam_year=2026,
        region=None,
        city=None,
        query=None,
        limit=20,
        cursor=None,
        now=checked_at,
    )
    v1_item = next(item for item in v1_search.items if item.position_id == target_position_id)
    assert v1_item.name == "一级行政执法员"
    assert v1_item.headcount == 1
    assert (
        qualification.evaluate_position(
            db_session,
            user_id=user_id,
            position_id=target_position_id,
            now=checked_at,
        ).evaluation.status
        == "eligible"
    )
    v1_context = target_scores.active_context_for_position(
        db_session,
        position_id=target_position_id,
        now=checked_at,
    )
    v1_target_score = target_scores.range_for_position(
        db_session,
        position_id=target_position_id,
        context=v1_context,
    )
    assert v1_target_score is not None
    assert v1_target_score.recommended < 150

    release_service.activate(
        db_session,
        release_id=release_ids["v2"],
        activated_at=datetime(2026, 10, 3, tzinfo=UTC),
    )
    db_session.commit()

    v2_search = discovery.search(
        db_session,
        user_id=user_id,
        exam_year=2026,
        region=None,
        city=None,
        query=None,
        limit=20,
        cursor=None,
        now=checked_at,
    )
    v2_item = next(item for item in v2_search.items if item.position_id == target_position_id)
    assert v2_item.name == "海关科技管理岗位"
    assert v2_item.headcount == 9
    assert (
        qualification.evaluate_position(
            db_session,
            user_id=user_id,
            position_id=target_position_id,
            now=checked_at,
        ).evaluation.status
        == "ineligible"
    )
    v2_context = target_scores.active_context_for_position(
        db_session,
        position_id=target_position_id,
        now=checked_at,
    )
    v2_target_score = target_scores.range_for_position(
        db_session,
        position_id=target_position_id,
        context=v2_context,
    )
    assert v2_target_score is not None
    assert v2_target_score.recommended > 200

    score_snapshots = (
        db_session.execute(
            text(
                """
            select
              position_data_version,
              min(entry_score_min) as minimum_score,
              max(entry_score_min) as maximum_score
            from product.position_score_snapshots
            where position_id = cast(:position_id as uuid)
              and evidence_kind = 'official_entry'
            group by position_data_version
            order by position_data_version
            """
            ),
            {"position_id": target_position_id},
        )
        .mappings()
        .all()
    )
    assert [
        (
            row["position_data_version"],
            float(row["minimum_score"]),
            float(row["maximum_score"]),
        )
        for row in score_snapshots
    ] == [
        ("snapshot-fixture-v1", 120.0, 122.0),
        ("snapshot-fixture-v2", 220.0, 222.0),
    ]

    digests = (
        db_session.execute(
            text(
                """
            select
              status,
              content_digest,
              product.compute_position_release_content_digest(release_id)
                as recomputed_digest
            from product.position_rule_releases
            where release_id in (
              cast(:v1_release_id as uuid),
              cast(:v2_release_id as uuid)
            )
            order by status
            """
            ),
            {
                "v1_release_id": release_ids["v1"],
                "v2_release_id": release_ids["v2"],
            },
        )
        .mappings()
        .all()
    )
    assert {row["status"] for row in digests} == {"active", "retired"}
    assert all(len(str(row["content_digest"])) == 64 for row in digests)
    assert all(row["content_digest"] == row["recomputed_digest"] for row in digests)
    db_session.execute(text("set local time zone 'UTC'"))
    utc_digest = db_session.execute(
        text(
            """
            select product.compute_position_release_content_digest(
              cast(:release_id as uuid)
            )
            """
        ),
        {"release_id": release_ids["v2"]},
    ).scalar_one()
    db_session.execute(text("set local time zone 'Asia/Shanghai'"))
    shanghai_digest = db_session.execute(
        text(
            """
            select product.compute_position_release_content_digest(
              cast(:release_id as uuid)
            )
            """
        ),
        {"release_id": release_ids["v2"]},
    ).scalar_one()
    assert utc_digest == shanghai_digest

    with pytest.raises(IntegrityError, match="immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.position_snapshots
                    set name = '不允许覆盖'
                    where position_id = cast(:position_id as uuid)
                      and position_data_version = 'snapshot-fixture-v1'
                    """
                ),
                {"position_id": target_position_id},
            )
    with pytest.raises(IntegrityError, match="immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    """
                    update product.position_score_snapshots
                    set entry_score_min = 299
                    where position_id = cast(:position_id as uuid)
                      and position_data_version = 'snapshot-fixture-v1'
                    """
                ),
                {"position_id": target_position_id},
            )


def _create_source_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table systems (
              code text primary key,
              name_zh text not null,
              paper_type text not null,
              is_big_six integer not null default 1
            );
            create table positions (
              id text primary key,
              year integer not null,
              position_code text not null,
              name text not null,
              department_full text not null,
              system_code text not null,
              region text not null,
              city text,
              paper_type text not null,
              headcount integer not null default 1,
              education_min text,
              major_required text,
              political_req text,
              hukou_req text,
              service_min_yrs integer,
              remark text,
              raw_json text,
              created_at integer
            );
            create table position_score_history (
              id text primary key,
              position_code text not null,
              year integer not null,
              entry_score_min real,
              entry_score_max real,
              admission_min real,
              admission_max real,
              signup_count integer,
              ratio real,
              data_source text,
              unique (position_code, year)
            );
            create table system_score_history (
              id text primary key,
              system_code text not null,
              region text not null,
              year integer not null,
              entry_score_min real,
              entry_score_max real,
              admission_min real,
              admission_max real,
              confidence text not null default 'aggregated',
              data_source text,
              created_at integer,
              unique (system_code, region, year)
            );
            create table position_tags (
              position_id text not null,
              tag text not null,
              primary key (position_id, tag)
            );
            insert into systems (code, name_zh, paper_type, is_big_six)
            values ('tax', '国家税务总局', 'administrativeLaw', 1);
            insert into positions (
              id,
              year,
              position_code,
              name,
              department_full,
              system_code,
              region,
              city,
              paper_type,
              headcount,
              education_min,
              major_required,
              political_req,
              hukou_req,
              service_min_yrs,
              remark,
              raw_json
            ) values
              ('gk-2026-00001', 2026, '300110001001', '一级行政执法员', '国家税务总局测试局A', 'tax', '北京', '北京市', 'administrativeLaw', 1, '本科', '法学类', null, null, null, '备注A', '{"source":"fixture"}'),
              ('gk-2026-00002', 2026, '300110001001', '一级行政执法员', '国家税务总局测试局B', 'tax', '北京', '北京市', 'administrativeLaw', 2, '本科', '经济学类', null, null, null, '备注B', null),
              ('gk-2024-001', 2024, '300110009999', '一级行政执法员', '国家税务总局测试局C', 'tax', '天津', '天津市', 'administrativeLaw', 1, '本科', '不限', null, null, null, null, null);
            insert into position_score_history (
              id,
              position_code,
              year,
              entry_score_min,
              entry_score_max,
              signup_count,
              ratio,
              data_source
            ) values (
              'score-2026-001',
              '300110001001',
              2026,
              121.5,
              133.2,
              42,
              14.0,
              'fixture'
            );
            insert into system_score_history (
              id,
              system_code,
              region,
              year,
              entry_score_min,
              entry_score_max,
              confidence,
              data_source
            ) values (
              'tax-全国-2019',
              'tax',
              '全国',
              2019,
              110.0,
              140.0,
              'aggregated',
              'fixture'
            );
            insert into position_tags (position_id, tag)
            values ('gk-2026-00001', '历史分偏高');
            """
        )


def _create_official_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试直属机构"
    sheet.append(["招考职位由招录机关编报"] + [None] * (len(OFFICIAL_HEADERS) - 1))
    sheet.append(list(OFFICIAL_HEADERS))
    sheet.append(
        [
            "130000",
            "国家税务总局",
            "测试局A",
            "中央国家行政机关省级以下直属机构",
            "一级行政执法员",
            "普通职位",
            "其他职位",
            "从事基层税收工作",
            "300110001001",
            "县（区）级及以下",
            "行政执法类",
            "1",
            "法学类",
            "本科及以上",
            "与最高学历相对应的学位",
            "不限",
            "无限制",
            "无限制",
            "否",
            "3:1",
            "北京市",
            "北京市",
            "限应届高校毕业生；大学英语四级425分及以上。",
            "https://example.test/a",
            "010-00000001",
            "",
            "",
        ]
    )
    sheet.append(
        [
            "130000",
            "国家税务总局",
            "测试局B",
            "中央国家行政机关省级以下直属机构",
            "一级行政执法员",
            "普通职位",
            "其他职位",
            "从事基层税收工作",
            "300110001001",
            "县（区）级及以下",
            "行政执法类",
            "2",
            "经济学类",
            "本科及以上",
            "与最高学历相对应的学位",
            "不限",
            "二年",
            "三支一扶",
            "否",
            "3:1",
            "北京市",
            "北京市",
            "在本单位最低服务年限5年。",
            "https://example.test/b",
            "010-00000002",
            "",
            "",
        ]
    )
    workbook.save(path)
