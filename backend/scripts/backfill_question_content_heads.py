#!/usr/bin/env python3
"""批量补建缺失的题面 content version + head（生产数据修复）。

背景：
- product.question_content_versions / question_content_heads 是题面冻结基线。
- 迁移 20260715_21 只在首次创建时发布既有题面；之后导入的新题若没走
  SQLQuestionContentCatalog.publish_current，就没有 content head。
- 知识树 v4 发布（refresh_knowledge_map_nodes.sql）要求每条绑定候选的题
  都有 content_version_id + 冻结快照，缺 head 的题会触发
  `knowledge-map question content head is missing or stale` 回滚。

本脚本用生产正式入口 SQLQuestionContentCatalog.publish_current 为所有
active/canonical 客观题补建 content_version + head。publish_current 内部：
- 持 question-content-publish:{question_id} advisory lock（安全并发）；
- 从 questions 表快照当前内容，算 immutable content_version_id；
- 写 append-only question_content_versions，原子推进 question_content_heads；
- 内容 hash 碰撞但内容不同时抛 ContentVersionIntegrityError 拒绝。

用法（服务器上，用 db.env 的 DATABASE_URL）：
    cd /srv/gongkao-dazi/app/backend
    set -a; source /etc/gongkao-dazi/db.env; set +a
    .venv/bin/python scripts/backfill_question_content_heads.py

幂等：可重复执行；已补建的题 publish_current 会 no-op 推进到相同版本。
"""

from __future__ import annotations

import argparse
from datetime import datetime, UTC
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_engine, get_session_factory
from app.domains.external_ingestion.matching.catalog import (
    ContentPublicationError,
    ContentVersionIntegrityError,
    SQLQuestionContentCatalog,
)


def _clock() -> datetime:
    return datetime.now(UTC)


def find_missing_heads(session: Session) -> list[str]:
    """Return active/canonical objective question ids lacking a content head."""
    rows = session.execute(
        text(
            """
            select question.question_id
            from product.questions question
            where question.domain = 'objective'
              and question.status in ('active', 'canonical')
              and not exists (
                select 1
                from product.question_content_heads head
                where head.question_id = question.question_id
              )
            order by question.question_id
            """
        )
    ).scalars()
    return [str(question_id) for question_id in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="questions to publish per commit (default 500)",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=0,
        help="publish at most this many questions (default all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only report the questions that would be backfilled",
    )
    args = parser.parse_args()

    engine = get_engine()
    session_factory = get_session_factory()

    with session_factory() as session:
        question_ids = find_missing_heads(session)
    total = len(question_ids)
    if args.max_questions > 0:
        question_ids = question_ids[: args.max_questions]
    print(f"missing content heads: {total}")

    if args.dry_run:
        for question_id in question_ids:
            print(question_id)
        return 0

    catalog = SQLQuestionContentCatalog  # type: ignore[assignment]
    session = session_factory()
    published = 0
    failed: list[tuple[str, str]] = []
    try:
        # publish_current 持单题 advisory lock 并原子推进 head；分批 commit
        # 让长事务不积压锁/临时文件。失败跳过并记录，不中断整批。
        for index, question_id in enumerate(question_ids, start=1):
            try:
                SQLQuestionContentCatalog(
                    session,
                    clock=_clock,
                ).publish_current(question_id=question_id)
                published += 1
            except (ContentPublicationError, ContentVersionIntegrityError) as exc:
                failed.append((question_id, str(exc)))
            except Exception as exc:  # noqa: BLE001 - 记录所有意外错误并继续
                failed.append((question_id, f"{type(exc).__name__}: {exc}"))
            if index % args.batch_size == 0:
                session.commit()
                print(
                    f"progress {index}/{len(question_ids)} "
                    f"(published {published}, failed {len(failed)})",
                    flush=True,
                )
        session.commit()
    finally:
        session.close()

    print(f"done: published {published}, failed {len(failed)}")
    for question_id, reason in failed[:20]:
        print(f"  FAIL {question_id}: {reason}")
    if len(failed) > 20:
        print(f"  ... and {len(failed) - 20} more failures")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
