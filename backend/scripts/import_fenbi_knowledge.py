#!/usr/bin/env python3
"""补导粉笔知识点(knowledge_points)到 question_tags。

题目来源既可能是历史 ``fenbi:<key>``，也可能是脱敏后的 ``an:<key>``；两者都
关联到同一份原始题目键。写入使用稳定 tag id，因此重复运行不会制造重复标签。
"""
import json
import sys
from hashlib import sha256
from pathlib import Path

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/fenbi-json")


def _stable_identifier(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 建立原始 key -> pp_ question_id 映射，兼容脱敏前后的来源前缀。
    cur.execute(
        """
        select representative_source_question_id, question_id
        from product.questions
        where representative_source_question_id like 'an:%'
           or representative_source_question_id like 'fenbi:%'
        """
    )
    key_to_qid = {
        src.split(":", 1)[1]: qid for src, qid in cur.fetchall()
    }
    print(f"粉笔题映射: {len(key_to_qid)}")

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
                        question_tag_id, question_id, tag_type, tag_value
                    ) values (%s, %s, %s, %s)
                    on conflict (question_tag_id) do nothing
                    """,
                    (
                        _stable_identifier("kptag", qid, name),
                        qid,
                        "knowledge_point",
                        name,
                    ),
                )
                tagged += cur.rowcount
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
