#!/usr/bin/env python3
"""导入 peipeishua sydw-public 前 100 题到 staging product.questions。

字段映射：
- sourceQuestionId -> question_id + representative_source_question_id
- section -> module_name, question_type
- isGraphicReasoning -> visual_required
- question.stem/stemHtml -> stem/stem_html
- question.options/optionHtmls -> question_options (label A-D)
- question.answer -> answer
"""
import json
from hashlib import sha256
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/pp-100.jsonl")
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 0  # 0 = 全量
SOURCE_DATASET_ID = "sydw-public-import-20260801"

LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"]


def _stable_identifier(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _existing_question_id(cur: psycopg.Cursor, source_question_id: str) -> str | None:
    row = cur.execute(
        """
        select question_id
        from product.questions
        where representative_source_question_id = %s
        union
        select question_id
        from product.question_occurrences
        where source_question_id = %s
        limit 1
        """,
        (source_question_id, source_question_id),
    ).fetchone()
    return str(row[0]) if row else None


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    inserted = 0
    skipped = 0
    t0 = time.time()
    with open(SRC) as f:
        for line in f:
            if LIMIT and inserted + skipped >= LIMIT:
                break
            d = json.loads(line)
            q = d.get("question") or {}
            if not q.get("stem") and not q.get("stemHtml"):
                skipped += 1
                continue
            source_question_id = str(d.get("sourceQuestionId") or "").strip()
            if not source_question_id:
                print("SKIP question without sourceQuestionId", file=sys.stderr)
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
                        module_name, question_type, visual_required,
                        stem, stem_html, answer
                    ) values (%s, 'objective', 'active', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        qid,
                        source_question_id,
                        d.get("section") or "单项选择题",
                        q.get("type") or d.get("section") or "单项选择题",
                        bool(d.get("isGraphicReasoning")),
                        q.get("stem"),
                        q.get("stemHtml"),
                        q.get("answer"),
                    ),
                )
                # 选项
                for i, opt in enumerate(q.get("options") or []):
                    cur.execute(
                        """
                        insert into product.question_options (
                            question_id, option_index, label, option_text, option_html
                        ) values (%s, %s, %s, %s, %s)
                        """,
                        (
                            qid,
                            i + 1,
                            LABELS[i] if i < len(LABELS) else str(i + 1),
                            opt,
                            (q.get("optionHtmls") or [])[i] if i < len(q.get("optionHtmls") or []) else opt,
                        ),
                    )
                # 解析（1 题 1 条）
                analysis = q.get("analysis") or q.get("analysisHtml") or ""
                if analysis:
                    cur.execute(
                        """
                        insert into product.question_explanations (
                            question_id, stored_analysis, original_analysis,
                            analysis_source, analysis_status,
                            prefer_ai_analysis, analysis_needs_review
                        ) values (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            qid,
                            analysis,
                            analysis,
                            "imported_peipeishua",
                            "imported",
                            False,
                            True,
                        ),
                    )
                # 知识点标签
                for kp in q.get("keypoints") or []:
                    tag_value = kp.get("name") if isinstance(kp, dict) else str(kp)
                    if tag_value:
                        cur.execute(
                            """
                            insert into product.question_tags (
                                question_tag_id, question_id, source_tag_id,
                                tag_type, tag_value
                            ) values (%s, %s, %s, %s, %s)
                            """,
                            (
                                f"pp_tag_{uuid4().hex[:20]}",
                                qid,
                                str(kp.get("id")) if isinstance(kp, dict) else None,
                                "knowledge_point",
                        tag_value,
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
                print(f"ERROR {source_question_id}: {exc}", file=sys.stderr)
                skipped += 1
                continue
            if inserted % 500 == 0:
                conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: inserted={inserted} skipped={skipped} elapsed={time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
