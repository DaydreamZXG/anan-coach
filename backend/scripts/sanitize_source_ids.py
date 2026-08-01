#!/usr/bin/env python3
"""把 fenbi 来源标识脱敏：`fenbi:<key>` -> `an:<key>`。

保留数字 key 作为主键/关联基础，去掉第三方品牌字样，避免误判来源。
同时清理 questions.representative_source_question_id 与
question_occurrences.source_question_id 两处。
"""
import sys

import psycopg

DB_URL = sys.argv[1] if len(sys.argv) > 1 else "postgresql:///gongkao_dazi_staging?host=/var/run/postgresql"


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 1. questions.representative_source_question_id
    cur.execute(
        "select count(*) from product.questions where representative_source_question_id like 'fenbi:%'"
    )
    q_count = cur.fetchone()[0]
    cur.execute(
        """
        update product.questions
        set representative_source_question_id = 'an:' || substring(representative_source_question_id from 7)
        where representative_source_question_id like 'fenbi:%'
        """
    )
    q_updated = cur.rowcount

    # 2. question_occurrences.source_question_id
    cur.execute(
        "select count(*) from product.question_occurrences where source_question_id like 'fenbi:%'"
    )
    o_count = cur.fetchone()[0]
    cur.execute(
        """
        update product.question_occurrences
        set source_question_id = 'an:' || substring(source_question_id from 7)
        where source_question_id like 'fenbi:%'
        """
    )
    o_updated = cur.rowcount

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: questions {q_updated}/{q_count}, occurrences {o_updated}/{o_count} -> an:前缀")


if __name__ == "__main__":
    main()
