#!/usr/bin/env python3
"""把 sydw(事业编/公基)题从'常识判断'模块剥离，归到独立 'gongji'(公共基础知识)模块。

行测常识判断 = fenbi(国考/省考)来源，保持干净。
sydw 来源的常识题实为公基题，单独成块保留（不删，作为事业编题库潜力）。
"""
import sys

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 1. sydw 来源 + 有 general_knowledge 模块标签的题
    cur.execute(
        """
        select distinct t.question_id
        from product.question_tags t
        join product.questions q on q.question_id = t.question_id
        where t.tag_type = 'module' and t.tag_value = 'general_knowledge'
          and q.question_id like 'pp_%'
          and q.representative_source_question_id not like 'fenbi:%'
        """
    )
    qids = [row[0] for row in cur.fetchall()]
    print(f"sydw 公基题(将从常识剥离): {len(qids)}")

    # 2. 这些题的 general_knowledge 标签改成 gongji
    changed = 0
    for qid in qids:
        cur.execute(
            """
            update product.question_tags set tag_value = 'gongji'
            where question_id = %s and tag_type = 'module' and tag_value = 'general_knowledge'
            """,
            (qid,),
        )
        changed += cur.rowcount
        # 若已有 gongji 标签则删掉重复
        cur.execute(
            """
            delete from product.question_tags t
            where question_id = %s and tag_type = 'module' and tag_value = 'gongji'
              and t.question_tag_id not in (
                select min(question_tag_id) from product.question_tags
                where question_id = %s and tag_type = 'module' and tag_value = 'gongji'
              )
            """,
            (qid, qid),
        )
        if changed % 1000 == 0:
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: 剥离 {changed} 条标签，sydw 公基独立成块")


if __name__ == "__main__":
    main()
