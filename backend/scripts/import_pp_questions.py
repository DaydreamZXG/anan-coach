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
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"
SRC = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/pp-100.jsonl")
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 0  # 0 = 全量

LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"]


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
            qid = f"pp_{uuid4().hex[:24]}"
            try:
                cur.execute(
                    """
                    insert into product.questions (
                        question_id, representative_source_question_id,
                        module_name, question_type, visual_required,
                        stem, stem_html, answer
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        qid,
                        d.get("sourceQuestionId"),
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
                inserted += 1
            except psycopg.Error as exc:
                conn.rollback()
                print(f"ERROR {d.get('sourceQuestionId')}: {exc}", file=sys.stderr)
                skipped += 1
                continue

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: inserted={inserted} skipped={skipped} elapsed={time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

