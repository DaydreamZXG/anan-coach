#!/usr/bin/env python3
"""把 fenbi 题的图(title_images/选项 images)关联到 product.question_assets。

- 通过 `an:<key>` 找到已导入的 question_id
- 每题每个图写一条 asset(part_kind=stem_html 或 option_html)
- image_src = 相对路径(试卷_images/xxx)，object_key = data/question-bank-export/files/<相对路径>
"""
import json
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

    # an:key -> question_id 映射
    cur.execute(
        "select representative_source_question_id, question_id from product.questions where representative_source_question_id like 'an:%'"
    )
    key_to_qid = {src.removeprefix("an:"): qid for src, qid in cur.fetchall()}
    print(f"an:key 映射: {len(key_to_qid)}")

    assets = 0
    q_with_assets = 0
    for jf in sorted(SRC.rglob("*.json")):
        try:
            with open(jf, "rb") as raw:
                content = raw.read()
            paper = _load_json(content)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        for q in paper.get("questions", []):
            key = str(q.get("key") or q.get("id") or "")
            qid = key_to_qid.get(key)
            if not qid:
                continue
            wrote = 0
            # 题干图
            for i, img in enumerate(q.get("title_images") or []):
                cur.execute(
                    _INSERT_SQL,
                    (f"asset_{uuid4().hex[:20]}", qid, key, True, "stem_html", i,
                     img.get("bytes", 0), img.get("local", ""), _object_key(img.get("local", "")), img.get("original", "")),
                )
                assets += 1
                wrote += 1
            # 选项图
            for oi, opt in enumerate(q.get("options") or []):
                for ii, img in enumerate(opt.get("images") or []):
                    cur.execute(
                        _INSERT_SQL,
                        (f"asset_{uuid4().hex[:20]}", qid, key, False, "option_html",
                         oi * 10 + ii, img.get("bytes", 0), img.get("local", ""), _object_key(img.get("local", "")), img.get("original", "")),
                    )
                    assets += 1
                    wrote += 1
            if wrote:
                q_with_assets += 1
        conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: assets={assets}, questions_with_assets={q_with_assets}")


_INSERT_SQL = """
insert into product.question_assets (
    asset_id, question_id, source_question_id, is_representative_asset,
    part_kind, part_index, image_ordinal, image_src, object_key, source_html
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _object_key(local: str) -> str:
    return f"data/question-bank-export/files/{local}" if local else ""


def _load_json(content: bytes) -> dict:
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return json.loads(content.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise UnicodeDecodeError("json", content[:50], 0, 1, "no compatible encoding")


if __name__ == "__main__":
    main()
