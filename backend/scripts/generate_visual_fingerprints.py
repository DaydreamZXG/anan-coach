#!/usr/bin/env python3
"""给已挂图的题生成视觉感知指纹，写入 product.question_visual_fingerprints。

- 对每题的第一张代表图算 4 种哈希 → 合成 fingerprint_sha256
- content_version_id 取题的内容版本（若无则用 question_id 派生）
- source_asset_ref 指回该题的资产 id
"""
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
from PIL import Image

# 引入视觉哈希（项目内模块；staging 运行时可能从 /tmp import，模块尚未部署进后端）
try:
    from app.domains.external_ingestion.recognition.visual_hash import (
        feature_hashes,
        fingerprint_sha256,
    )
except ImportError:
    sys.path.insert(0, "/tmp")
    from visual_hash import feature_hashes, fingerprint_sha256  # type: ignore

DB_URL = (
    os.environ.get("QBANK_IMPORT_DATABASE_URL")
    or (sys.argv[1] if len(sys.argv) > 1 else None)
    or "postgresql:///gongkao_dazi?host=/var/run/postgresql"
)
ASSET_ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/srv/gongkao-dazi/question-bank-export/files")
POLICY_VERSION = "visual-hash-v1"


def main() -> None:
    conn = psycopg.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    # 取带 assets + 有 content_version 的题（确保外键满足），每题只取一张代表资产
    cur.execute(
        """
        select distinct on (a.question_id)
               a.question_id, a.asset_id, a.image_src, c.content_version_id
        from product.question_assets a
        join product.question_content_versions c on c.question_id = a.question_id
        where a.is_representative_asset = true
          and a.image_src is not null and a.image_src != ''
        order by a.question_id, a.part_kind, a.part_index, a.image_ordinal
        """
    )
    rows = cur.fetchall()
    print(f"带资产且有内容版本的题: {len(rows)}")

    written = 0
    skipped = 0
    for qid, asset_id, image_src, cv_id in rows:
        # 组装图路径：asset root + image_src（去掉省份前缀，staging 文件无省份层）
        # image_src 形如「江苏/2019年..._images/img_xxx.png」，实际文件在 files/<试卷名>_images/
        rel = image_src.split("/", 1)[1] if "/" in image_src else image_src
        img_path = ASSET_ROOT / rel
        if not img_path.exists():
            skipped += 1
            continue
        try:
            with Image.open(img_path) as img:
                hashes = feature_hashes(img)
        except Exception:
            skipped += 1
            continue
        fp = fingerprint_sha256(hashes)
        cur.execute(
            """
            insert into product.question_visual_fingerprints (
                visual_fingerprint_id, question_id, content_version_id,
                fingerprint_sha256, fingerprint_policy_version,
                source_asset_ref, created_at,
                phash, dhash, ahash, edge_dhash
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (content_version_id, fingerprint_sha256) do nothing
            """,
            (
                uuid4(),
                qid,
                cv_id,
                fp,
                POLICY_VERSION,
                asset_id,
                datetime.now(UTC),
                hashes["phash"],
                hashes["dhash"],
                hashes["ahash"],
                hashes["edge_dhash"],
            ),
        )
        written += cur.rowcount
        if written % 500 == 0:
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"done: written={written} skipped={skipped}")


if __name__ == "__main__":
    main()
