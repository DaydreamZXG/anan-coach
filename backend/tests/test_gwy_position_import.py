from __future__ import annotations

from pathlib import Path
import sqlite3

from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.orm import Session

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

    first = import_gwy_positions(session=db_session, source_path=source, batch_size=2)
    second = import_gwy_positions(session=db_session, source_path=source, batch_size=2)
    verification = verify_gwy_position_import(db_session, source_path=source)

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

    row = db_session.execute(
        text(
            """
            select p.major_required, h.entry_score_min, h.signup_count, l.confidence
            from product.positions p
            join product.position_score_links l on l.position_id = p.position_id
            join product.position_score_histories h on h.score_history_id = l.score_history_id
            where p.source_id = 'gk-2026-00001'
            """
        )
    ).mappings().one()
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

    row = db_session.execute(
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
    ).mappings().one()
    assert row["education_min"] == "本科及以上"
    assert row["remark"] == "限应届高校毕业生；大学英语四级425分及以上。"
    assert row["contract_version"] == "guokao-official-position@v1"
    assert row["position_data_version"] == "guokao-2026-official-v1"
    assert row["degree"] == "与最高学历相对应的学位"
    assert row["service_project"] == "无限制"


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
