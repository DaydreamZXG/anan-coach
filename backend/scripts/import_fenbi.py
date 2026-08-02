#!/usr/bin/env python3
"""导入 peipeishua fenbi 试卷题库（国考/省考）到 staging product.questions。

每份试卷是一个 json：{ paper, questions: [...], images: [...] }。
每题字段：title/title_html/options/answer/difficulty/correct_ratio 等。
图（images.local 指向本地图片文件）在后续步骤单独迁移到 question_assets。
"""
import json
from hashlib import sha256
import sys
import time
from pathlib import Path

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/fenbi-dir")
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 0  # 0 = 全量
SOURCE_DATASET_ID = "fenbi-import-20260801"


def _stable_identifier(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _existing_question_id(cur: psycopg.Cursor, source_question_id: str) -> str | None:
    legacy_source_id = source_question_id.replace("an:", "fenbi:", 1)
    row = cur.execute(
        """
        select question_id
        from product.questions
        where representative_source_question_id in (%s, %s)
        union
        select question_id
        from product.question_occurrences
        where source_question_id in (%s, %s)
        limit 1
        """,
        (source_question_id, legacy_source_id, source_question_id, legacy_source_id),
    ).fetchone()
    return str(row[0]) if row else None


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    t0 = time.time()
    # 遍历目录下所有试卷 json
    json_files = sorted(SRC.rglob("*.json"))
    for jf in json_files:
        if LIMIT and inserted + skipped >= LIMIT:
            break
        try:
            with open(jf, "rb") as raw:
                content = raw.read()
            paper = _load_json_bytes(content)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            print(f"SKIP {jf.name}: {exc}", file=sys.stderr)
            skipped += 1
            continue
        paper_name = paper.get("paper", {}).get("name") or paper.get("name") or jf.stem
        for q in paper.get("questions", []):
            title = q.get("title") or q.get("title_html") or ""
            if not title:
                skipped += 1
                continue
            source_question_id = f"an:{q.get('key') or q.get('id') or ''}"
            if source_question_id == "an:":
                skipped += 1
                continue
            if _existing_question_id(cur, source_question_id) is not None:
                skipped += 1
                continue
            qid = _stable_identifier("pp", SOURCE_DATASET_ID, source_question_id)
            cur.execute("savepoint import_question")
            try:
                cur.execute(
                    """
                    insert into product.questions (
                        question_id, domain, status, representative_source_question_id,
                        module_name, question_type, difficulty,
                        stem, stem_html, answer, rate
                    ) values (%s, 'objective', 'active', %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        qid,
                        source_question_id,
                        paper_name,
                        _type_name(q.get("type")),
                        _difficulty(q.get("difficulty")),
                        title,
                        q.get("title_html") or title,
                        q.get("answer") or "",
                        _rate(q),
                    ),
                )
                # 选项
                for opt in q.get("options") or []:
                    cur.execute(
                        """
                        insert into product.question_options (
                            question_id, option_index, label, option_text, option_html
                        ) values (%s, %s, %s, %s, %s)
                        """,
                        (
                            qid,
                            int(opt.get("index", 0)) + 1,
                            opt.get("label") or "ABCDEFGH"[int(opt.get("index", 0))],
                            opt.get("text") or "",
                            opt.get("html") or opt.get("text") or "",
                        ),
                    )
                cur.execute(
                    """
                    insert into product.question_occurrences (
                        occurrence_id, question_id, source_question_id, source_dataset_id
                    ) values (%s, %s, %s, %s)
                    on conflict (occurrence_id) do nothing
                    """,
                    (
                        _stable_identifier("occ", qid, source_question_id),
                        qid,
                        source_question_id,
                        SOURCE_DATASET_ID,
                    ),
                )
                cur.execute("release savepoint import_question")
                inserted += 1
            except psycopg.Error as exc:
                cur.execute("rollback to savepoint import_question")
                cur.execute("release savepoint import_question")
                print(f"ERROR {jf.name} q{title[:20]}: {exc}", file=sys.stderr)
                skipped += 1
                continue
        # 每份试卷提交一次，避免事务过大
        conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: inserted={inserted} skipped={skipped} elapsed={time.time() - t0:.1f}s")


def _load_json_bytes(content: bytes) -> dict:
    """按 utf-8 → gbk → latin-1 顺序尝试解码 JSON，兼容老试卷文件编码。"""
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return json.loads(content.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise UnicodeDecodeError("json", content[:50], 0, 1, "no compatible encoding")


def _type_name(t: object) -> str:
    mapping = {1: "单选题", 2: "多选题", 3: "判断题", 4: "资料分析", 5: "图形推理"}
    return mapping.get(t) if isinstance(t, int) else str(t or "")


def _difficulty(d: object) -> str:
    if not isinstance(d, int):
        return str(d or "")
    # 粉笔难度 1-5，转文本
    return {1: "易", 2: "较易", 3: "中", 4: "较难", 5: "难"}.get(d, str(d))


def _rate(q: dict) -> str:
    return str(q.get("rate") or q.get("correct_ratio") or "")


if __name__ == "__main__":
    main()
