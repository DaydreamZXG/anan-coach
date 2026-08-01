#!/usr/bin/env python3
"""题库去重压实（细化标准）。

判重标准：题干(去空格后) ≥ 20 字 + 选项序列 + 答案 三者全同 = 真重复。
- 题干短(填空/特殊题型)不参与，避免误删
- 图推等相似题(题干同但图/选项不同)不合并
策略：每组建规范版（fenbi 优先），重复成员的出现记录进 question_occurrences，
子表(选项/解析/标签)迁移或删除，物理删除重复成员。
"""
import collections
import re
import sys
from uuid import uuid4

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"
MIN_STEM_LEN = 20


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 1. 取所有 pp_ 题
    cur.execute(
        """
        select question_id, stem, answer, representative_source_question_id
        from product.questions where question_id like 'pp_%'
        """
    )
    rows = cur.fetchall()
    # 选项序列
    cur.execute(
        """
        select question_id, string_agg(option_text, '|' order by option_index)
        from product.question_options where question_id like 'pp_%'
        group by question_id
        """
    )
    opt_seq = dict(cur.fetchall())

    # 2. 分组判重
    groups: dict[tuple, list[tuple]] = collections.defaultdict(list)
    for qid, stem, answer, src in rows:
        if len(_norm(stem)) < MIN_STEM_LEN:
            continue
        key = (_norm(stem), opt_seq.get(qid, ""), answer or "")
        groups[key].append((qid, src, stem, answer))

    removed = 0
    kept = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # 规范版：fenbi 优先
        members.sort(key=lambda m: (0 if (m[1] or "").startswith("fenbi:") else 1, m[0]))
        keep_id = members[0][0]
        for qid, src, _stem, _ans in members[1:]:
            # 选项迁移（规范版缺时）
            cur.execute(
                "select count(*) from product.question_options where question_id = %s",
                (keep_id,),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "update product.question_options set question_id = %s where question_id = %s",
                    (keep_id, qid),
                )
            # 解析迁移（规范版缺时）
            cur.execute(
                "select count(*) from product.question_explanations where question_id = %s",
                (keep_id,),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "update product.question_explanations set question_id = %s where question_id = %s",
                    (keep_id, qid),
                )
            # 记录出现来源
            if src:
                cur.execute(
                    """
                    insert into product.question_occurrences (
                        occurrence_id, question_id, source_question_id
                    ) values (%s, %s, %s)
                    """,
                    (f"occ_{uuid4().hex[:20]}", keep_id, src),
                )
            # 标签迁移
            cur.execute(
                "update product.question_tags set question_id = %s where question_id = %s",
                (keep_id, qid),
            )
            # 物理删除重复成员
            cur.execute("delete from product.question_options where question_id = %s", (qid,))
            cur.execute("delete from product.question_explanations where question_id = %s", (qid,))
            cur.execute("delete from product.question_tags where question_id = %s", (qid,))
            cur.execute("delete from product.questions where question_id = %s", (qid,))
            removed += 1
        kept += 1
        if kept % 500 == 0:
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: groups={kept} removed={removed}")


if __name__ == "__main__":
    main()
