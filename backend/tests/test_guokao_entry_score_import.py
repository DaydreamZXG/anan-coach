from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.importers.guokao_entry_scores import (
    GuokaoEntryScoreImportError,
    import_guokao_entry_scores,
    verify_guokao_entry_score_import,
)


def test_import_guokao_entry_scores_upserts_links_and_omits_candidate_pii(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = tmp_path / "interview.xlsx"
    _create_interview_workbook(
        source,
        rows=[
            (
                "002111021301304",
                "张三",
                "中央办公厅",
                "002000",
                "警卫局",
                "档案管理岗位一级主任科员及以下",
                "100110001001",
                110.1,
            ),
            (
                "002111071000324",
                "李四",
                "中央办公厅",
                "002000",
                "警卫局",
                "档案管理岗位一级主任科员及以下",
                "100110001001",
                110.1,
            ),
            (
                "002111071401708",
                "王五",
                "中央办公厅",
                "002000",
                "警卫局",
                "文物管理岗位一级主任科员及以下",
                "100110001002",
                116.6,
            ),
            (
                "999111071401708",
                "赵六",
                "中央办公厅",
                "002000",
                "警卫局",
                "未导入岗位",
                "100110001003",
                118.2,
            ),
        ],
    )
    _insert_position(
        db_session,
        source_id="gk-2024-exact",
        position_code="100110001001",
        name="档案管理岗位一级主任科员及以下",
        department_full="中央办公厅警卫局",
    )
    _insert_position(
        db_session,
        source_id="gk-2024-contained",
        position_code="100110001002",
        name="文物管理岗位一级主任科员及以下",
        department_full="中央办公厅警卫局机关",
    )
    db_session.commit()

    first = import_guokao_entry_scores(session=db_session, source_path=source, batch_size=2)
    second = import_guokao_entry_scores(session=db_session, source_path=source, batch_size=2)
    verification = verify_guokao_entry_score_import(
        db_session,
        source_file_sha256=first.source_file_sha256,
    )

    assert first.evidence_rows == 3
    assert second.evidence_rows == 3
    assert verification["ok"] is True
    assert verification["linked_positions"] == 2
    assert verification["link_counts_by_method"] == {
        "exact_department_title_code": 1,
        "unique_department_containment_title_code": 1,
    }
    assert verification["unlinked_evidence"] == 1
    assert verification["coverage_by_year"] == [
        {"year": 2024, "positions": 2, "positions_with_entry_score": 2, "pct": 100.0}
    ]

    evidence = db_session.execute(
        text(
            """
            select candidate_count, entry_score_min, raw_json::text as raw_json
            from product.position_entry_score_evidence
            where position_code = '100110001001'
            """
        )
    ).mappings().one()
    assert int(evidence["candidate_count"]) == 2
    assert float(evidence["entry_score_min"]) == 110.1
    assert "张三" not in evidence["raw_json"]
    assert "002111021301304" not in evidence["raw_json"]


def test_import_guokao_entry_scores_rejects_conflicting_department_position_pair(
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = tmp_path / "conflicting.xlsx"
    _create_interview_workbook(
        source,
        rows=[
            (
                "002111021301304",
                "张三",
                "中央办公厅",
                "002000",
                "警卫局",
                "档案管理岗位一级主任科员及以下",
                "100110001001",
                110.1,
            ),
            (
                "002111071000324",
                "李四",
                "中央办公厅",
                "002000",
                "警卫局",
                "档案管理岗位一级主任科员及以下",
                "100110001001",
                111.2,
            ),
        ],
    )

    with pytest.raises(GuokaoEntryScoreImportError, match="conflicting entry score evidence"):
        import_guokao_entry_scores(session=db_session, source_path=source)


def _create_interview_workbook(path: Path, *, rows: list[tuple[object, ...]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(("准考证号", "姓名", "招录机关", "部门代码", "用人司局", "招考职位", "职位代码", "最低面试分数"))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def _insert_position(
    session: Session,
    *,
    source_id: str,
    position_code: str,
    name: str,
    department_full: str,
) -> None:
    session.execute(
        text(
            """
            insert into product.positions (
              source_id,
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
              major_required
            ) values (
              :source_id,
              2024,
              :position_code,
              :name,
              :department_full,
              'central',
              '北京',
              '北京市',
              'central',
              1,
              '本科',
              '不限'
            )
            """
        ),
        {
            "source_id": source_id,
            "position_code": position_code,
            "name": name,
            "department_full": department_full,
        },
    )
