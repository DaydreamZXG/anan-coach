#!/usr/bin/env python3
"""重新发布题面与冻结快照不一致的题（生产数据修复）。

背景：
- 知识树 v4 发布校验要求每条绑定候选的 current_snapshot 与 frozen_snapshot
  逐字节一致（refresh_knowledge_map_nodes.sql 1667 行）。
- fenbi 标识清理清了 question_options/questions.stem_html，但历史 content head
  存的冻结快照还有旧域名，head 从未被重新发布，导致 frozen ≠ current，v4 发布
  回滚。

本脚本读取一个含不一致 question_id 的文件（每行一个 id），对每个 id 调用
生产正式入口 SQLQuestionContentCatalog.publish_current：从当前 questions/
options/assets 表读快照，算新的 immutable content_version_id，append 写入
question_content_versions 并原子推进 head。内容 hash 相同则 no-op。

用法（服务器上，用 db.env 的 DATABASE_URL）：
    cd /srv/gongkao-dazi/app/backend
    set -a; source /etc/gongkao-dazi/db.env; set +a
    .venv/bin/python scripts/republish_mismatched_content.py --ids-file /tmp/ids.txt

幂等：可重复执行；已对齐的题 publish_current 会 no-op。
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import sys

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.domains.external_ingestion.matching.catalog import (
    ContentPublicationError,
    ContentVersionIntegrityError,
    SQLQuestionContentCatalog,
)


def _clock() -> datetime:
    return datetime.now(UTC)


def load_ids(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ids-file",
        required=True,
        help="path to a file with one question_id per line to republish",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="questions to republish per commit (default 500)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only print the ids that would be republished",
    )
    args = parser.parse_args()

    question_ids = load_ids(args.ids_file)
    print(f"questions to republish: {len(question_ids)}")

    if args.dry_run:
        for question_id in question_ids:
            print(question_id)
        return 0

    session_factory = get_session_factory()
    session: Session = session_factory()
    republished = 0
    failed: list[tuple[str, str]] = []
    try:
        for index, question_id in enumerate(question_ids, start=1):
            try:
                SQLQuestionContentCatalog(
                    session, clock=_clock
                ).publish_current(question_id=question_id)
                republished += 1
            except (ContentPublicationError, ContentVersionIntegrityError) as exc:
                failed.append((question_id, str(exc)))
            except Exception as exc:  # noqa: BLE001 - 记录所有意外错误并继续
                failed.append((question_id, f"{type(exc).__name__}: {exc}"))
            if index % args.batch_size == 0:
                session.commit()
                print(
                    f"progress {index}/{len(question_ids)} "
                    f"(republished {republished}, failed {len(failed)})",
                    flush=True,
                )
        session.commit()
    finally:
        session.close()

    print(f"done: republished {republished}, failed {len(failed)}")
    for question_id, reason in failed[:20]:
        print(f"  FAIL {question_id}: {reason}")
    if len(failed) > 20:
        print(f"  ... and {len(failed) - 20} more failures")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
