#!/usr/bin/env python3
"""补导 fenbi 知识点(knowledge_points)到 question_tags。

fenbi 题通过 representative_source_question_id = 'fenbi:<key>' 关联已导入的 pp_ 题。
每题的 knowledge_points 写为 tag_type='knowledge_point' 的 question_tags。
"""
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/fenbi-json")


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 建立 fenbi key -> pp_ question_id 映射
    cur.execute(
        """
        select representative_source_question_id, question_id
        from product.questions
        where representative_source_question_id like 'fenbi:%'
        """
    )
    key_to_qid = {
        src.removeprefix("fenbi:"): qid for src, qid in cur.fetchall()
    }
    print(f"fenbi 题映射: {len(key_to_qid)}")

    # 遍历所有 json 补知识点
    tagged = 0
    skipped = 0
    for jf in sorted(SRC.rglob("*.json")):
        try:
            with open(jf, "rb") as raw:
                content = raw.read()
            paper = _load_json(content)
        except (OSError, ValueError, UnicodeDecodeError):
            skipped += 1
            continue
        for q in paper.get("questions", []):
            key = str(q.get("key") or q.get("id") or "")
            qid = key_to_qid.get(key)
            if not qid:
                continue
            for kp in q.get("knowledge_points") or []:
                name = kp.get("name") if isinstance(kp, dict) else str(kp)
                if not name:
                    continue
                cur.execute(
                    """
                    insert into product.question_tags (
                        question_tag_id, question_id, source_tag_id, tag_type, tag_value
                    ) values (%s, %s, %s, %s, %s)
                    """,
                    (
                        f"kptag_{uuid4().hex[:20]}",
                        qid,
                        str(kp.get("id")) if isinstance(kp, dict) else None,
                        "knowledge_point",
                        name,
                    ),
                )
                tagged += 1
        conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: tagged={tagged} skipped_papers={skipped}")


def _load_json(content: bytes) -> dict:
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return json.loads(content.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise UnicodeDecodeError("json", content[:50], 0, 1, "no compatible encoding")


if __name__ == "__main__":
    main()
