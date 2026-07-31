"""Adopt the major catalog tables into the migration chain.

product.major_categories / major_sub_categories / major_catalog were created
manually in production via backend/scripts/major_catalog_schema.sql and never
entered the alembic chain, so every database built from migrations (tests,
smoke, disaster recovery) lacked them and GET /api/v2/positions/majors failed
with UndefinedTable. This migration adopts the DDL idempotently and seeds the
authoritative catalog only when the tables are empty.

Revision ID: 20260731_89
Revises: 20260730_88
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op
from sqlalchemy import text


revision: str = "20260731_89"
down_revision: str | Sequence[str] | None = "20260730_88"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "major_catalog_seed.sql"

_DDL_STATEMENTS = (
    # 与 scripts/major_catalog_schema.sql 一致，但把 pg_trgm 扩展放到
    # trgm 索引之前，修正原脚本在全新数据库上的语句顺序问题。
    """
    create extension if not exists pg_trgm
    """,
    """
    create table if not exists product.major_categories (
        code        varchar(2) primary key,
        name        varchar(50) not null,
        display_order int not null default 0,
        created_at  timestamptz not null default now()
    )
    """,
    """
    create table if not exists product.major_sub_categories (
        code          varchar(4) primary key,
        category_code varchar(2) not null,
        name          varchar(50) not null,
        display_order int not null default 0,
        created_at    timestamptz not null default now(),
        constraint fk_sub_category_parent
            foreign key (category_code)
            references product.major_categories(code)
    )
    """,
    """
    create table if not exists product.major_catalog (
        code              varchar(8) primary key,
        sub_category_code varchar(4) not null,
        name              varchar(100) not null,
        display_order     int not null default 0,
        created_at        timestamptz not null default now(),
        constraint fk_major_sub_parent
            foreign key (sub_category_code)
            references product.major_sub_categories(code)
    )
    """,
    """
    create index if not exists idx_major_sub_category
        on product.major_catalog(sub_category_code)
    """,
    """
    create index if not exists idx_major_sub_parent
        on product.major_sub_categories(category_code)
    """,
    """
    create index if not exists idx_major_name
        on product.major_catalog(name)
    """,
    """
    create index if not exists idx_major_name_trgm
        on product.major_catalog using gin (name gin_trgm_ops)
    """,
)


def _seed_statements() -> list[str]:
    seed_sql = _SEED_PATH.read_text(encoding="utf-8")
    statements = []
    for raw in seed_sql.split(";"):
        lines = [
            line
            for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        statement = "\n".join(lines).strip()
        if statement.lower().startswith("insert into product.major"):
            statements.append(statement)
    if not statements:
        raise RuntimeError(f"no insert statements found in {_SEED_PATH}")
    return statements


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _DDL_STATEMENTS:
        bind.execute(text(statement))
    existing = bind.execute(
        text("select count(*) from product.major_categories")
    ).scalar_one()
    if existing == 0:
        for statement in _seed_statements():
            # 种子含中文文本，避免 text() 的 :param 解析，走驱动级执行。
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260731_89 is forward-only: the major catalog is an "
        "authoritative reference dataset required by positions/majors"
    )
