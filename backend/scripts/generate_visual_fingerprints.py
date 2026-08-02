#!/usr/bin/env python3
"""从 TOS 为当前题库内容版本回填视觉感知指纹。

默认 ``audit`` 只读取数据库和对象存储、输出统计，不写数据库。显式传入
``--mode apply`` 才会写入。脚本仅处理 ``question_content_heads`` 指向的当前版本，
并跳过已有完整四哈希的代表资产，因此可安全重复执行。
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BytesIO
import json
import os
from typing import Any, Literal
from uuid import uuid4

import psycopg
from PIL import Image, UnidentifiedImageError
import tos

from app.domains.external_ingestion.recognition.image_preprocess import prepare_for_ocr
from app.domains.external_ingestion.recognition.visual_hash import (
    feature_hashes,
    fingerprint_sha256,
)

POLICY_VERSION = "visual-hash-prepared-v2"
DEFAULT_MAXIMUM_OBJECT_BYTES = 25 * 1024 * 1024
STAT_KEYS = (
    "current_head_assets",
    "already_complete",
    "missing_object_key",
    "object_missing",
    "object_too_large",
    "object_read_failed",
    "decode_failed",
    "fingerprint_ready",
    "would_write",
    "written",
)


@dataclass(frozen=True)
class Candidate:
    question_id: str
    asset_id: str
    object_key: str | None
    content_version_id: str
    already_complete: bool


@dataclass(frozen=True)
class Fingerprint:
    sha256: str
    phash: str
    dhash: str
    ahash: str
    edge_dhash: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("audit", "apply"),
        default="audit",
        help="audit 只读（默认）；apply 才写入指纹",
    )
    parser.add_argument("--database-url", default=os.getenv("QBANK_IMPORT_DATABASE_URL"))
    parser.add_argument("--tos-bucket", default=os.getenv("TOS_BUCKET", "gongkao-dazi-prod"))
    parser.add_argument("--tos-region", default=os.getenv("TOS_REGION", "cn-guangzhou"))
    parser.add_argument("--tos-endpoint", default=os.getenv("TOS_ENDPOINT"))
    parser.add_argument("--tos-access-key", default=os.getenv("TOS_ACCESS_KEY"))
    parser.add_argument("--tos-secret-key", default=os.getenv("TOS_SECRET_KEY"))
    parser.add_argument(
        "--maximum-object-bytes",
        type=int,
        default=int(
            os.getenv("VISUAL_FINGERPRINT_MAXIMUM_OBJECT_BYTES", DEFAULT_MAXIMUM_OBJECT_BYTES)
        ),
    )
    parser.add_argument("--limit", type=int, help="仅处理前 N 条，便于分批验证")
    parser.add_argument("--shard-count", type=int, default=1, help="稳定题目分片总数")
    parser.add_argument("--shard-index", type=int, default=0, help="当前分片编号（从 0 开始）")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=int(os.getenv("VISUAL_FINGERPRINT_PROGRESS_EVERY", "250")),
        help="每处理 N 条输出一次进度（默认 250）",
    )
    return parser


def _require_configuration(args: argparse.Namespace) -> None:
    required = {
        "database URL": args.database_url,
        "TOS bucket": args.tos_bucket,
        "TOS region": args.tos_region,
        "TOS endpoint": args.tos_endpoint,
        "TOS access key": args.tos_access_key,
        "TOS secret key": args.tos_secret_key,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise SystemExit(f"missing required configuration: {', '.join(missing)}")
    if args.maximum_object_bytes <= 0:
        raise SystemExit("--maximum-object-bytes must be positive")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive")
    if args.shard_count <= 0:
        raise SystemExit("--shard-count must be positive")
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index must be within --shard-count")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive")


def _psycopg_database_url(database_url: str) -> str:
    """Accept the SQLAlchemy PostgreSQL URL used by the application env."""
    for driver in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if database_url.startswith(driver):
            return "postgresql://" + database_url[len(driver) :]
    return database_url


def fetch_candidates(
    conn: Any,
    *,
    limit: int | None = None,
    shard_count: int = 1,
    shard_index: int = 0,
) -> list[Candidate]:
    """取当前 content head 的每题第一张代表图，并标记是否已完整回填。"""
    sql = """
        select distinct on (asset.question_id)
               asset.question_id,
               asset.asset_id,
               nullif(btrim(asset.object_key), '') as object_key,
               head.content_version_id,
               exists (
                   select 1
                   from product.question_visual_fingerprints visual
                   where visual.content_version_id = head.content_version_id
                     and visual.source_asset_ref = asset.asset_id
                     and visual.fingerprint_policy_version = %s
                     and visual.phash is not null
                     and visual.dhash is not null
                     and visual.ahash is not null
                     and visual.edge_dhash is not null
               ) as already_complete
        from product.question_assets asset
        join product.question_content_heads head
          on head.question_id = asset.question_id
        where asset.is_representative_asset = true
          and mod(abs(hashtextextended(asset.question_id, 0)), %s) = %s
        order by asset.question_id, asset.part_kind, asset.part_index, asset.image_ordinal,
                 asset.asset_id
    """
    params: list[Any] = [POLICY_VERSION, shard_count, shard_index]
    if limit is not None:
        sql += " limit %s"
        params.append(limit)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [Candidate(*row) for row in cursor.fetchall()]


def read_object(client: Any, *, bucket: str, key: str, maximum_bytes: int) -> bytes:
    output = client.get_object(bucket, key)
    try:
        chunks: list[bytes] = []
        total = 0
        while chunk := output.read(1024 * 1024):
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("object_too_large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        close = getattr(output, "close", None)
        if callable(close):
            close()


def calculate_fingerprint(payload: bytes) -> Fingerprint:
    with Image.open(BytesIO(payload)) as image:
        prepared = prepare_for_ocr(image, enable_crop=True, max_long_edge=2200)
        hashes = feature_hashes(prepared.image)
    return Fingerprint(
        sha256=fingerprint_sha256(hashes),
        phash=hashes["phash"],
        dhash=hashes["dhash"],
        ahash=hashes["ahash"],
        edge_dhash=hashes["edge_dhash"],
    )


def write_fingerprint(conn: Any, candidate: Candidate, fingerprint: Fingerprint) -> None:
    """原位刷新同一资产的派生指纹，或插入新指纹。"""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            update product.question_visual_fingerprints
            set fingerprint_sha256 = %s,
                fingerprint_policy_version = %s,
                phash = %s,
                dhash = %s,
                ahash = %s,
                edge_dhash = %s
            where content_version_id = %s
              and source_asset_ref = %s
            """,
            (
                fingerprint.sha256,
                POLICY_VERSION,
                fingerprint.phash,
                fingerprint.dhash,
                fingerprint.ahash,
                fingerprint.edge_dhash,
                candidate.content_version_id,
                candidate.asset_id,
            ),
        )
        if cursor.rowcount:
            return
        cursor.execute(
            """
            insert into product.question_visual_fingerprints (
                visual_fingerprint_id, question_id, content_version_id,
                fingerprint_sha256, fingerprint_policy_version,
                source_asset_ref, created_at,
                phash, dhash, ahash, edge_dhash
            ) values (%s, %s, %s, %s, %s, %s, now(), %s, %s, %s, %s)
            on conflict (content_version_id, fingerprint_sha256) do update set
                fingerprint_policy_version = excluded.fingerprint_policy_version,
                source_asset_ref = excluded.source_asset_ref,
                phash = excluded.phash,
                dhash = excluded.dhash,
                ahash = excluded.ahash,
                edge_dhash = excluded.edge_dhash
            where product.question_visual_fingerprints.fingerprint_policy_version
                      is distinct from excluded.fingerprint_policy_version
               or product.question_visual_fingerprints.source_asset_ref
                      is distinct from excluded.source_asset_ref
               or product.question_visual_fingerprints.phash is distinct from excluded.phash
               or product.question_visual_fingerprints.dhash is distinct from excluded.dhash
               or product.question_visual_fingerprints.ahash is distinct from excluded.ahash
               or product.question_visual_fingerprints.edge_dhash
                      is distinct from excluded.edge_dhash
            """,
            (
                uuid4(),
                candidate.question_id,
                candidate.content_version_id,
                fingerprint.sha256,
                POLICY_VERSION,
                candidate.asset_id,
                fingerprint.phash,
                fingerprint.dhash,
                fingerprint.ahash,
                fingerprint.edge_dhash,
            ),
        )


def _tos_status(exc: Exception) -> int | None:
    for name in ("status_code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


def process_candidates(
    candidates: Iterable[Candidate],
    *,
    mode: Literal["audit", "apply"],
    read: Callable[[str], bytes],
    write: Callable[[Candidate, Fingerprint], None],
    commit: Callable[[], None] | None = None,
    batch_size: int = 500,
    progress_every: int = 250,
    progress: Callable[[Counter[str]], None] | None = None,
) -> Counter[str]:
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")
    stats: Counter[str] = Counter({key: 0 for key in STAT_KEYS})
    pending = 0
    for candidate in candidates:
        stats["current_head_assets"] += 1
        if candidate.already_complete:
            stats["already_complete"] += 1
        elif not candidate.object_key:
            stats["missing_object_key"] += 1
        else:
            try:
                payload = read(candidate.object_key)
            except Exception as exc:
                if _tos_status(exc) == 404:
                    stats["object_missing"] += 1
                elif isinstance(exc, ValueError) and str(exc) == "object_too_large":
                    stats["object_too_large"] += 1
                else:
                    stats["object_read_failed"] += 1
            else:
                try:
                    fingerprint = calculate_fingerprint(payload)
                except (UnidentifiedImageError, OSError, ValueError):
                    stats["decode_failed"] += 1
                else:
                    stats["fingerprint_ready"] += 1
                    if mode == "audit":
                        stats["would_write"] += 1
                    else:
                        write(candidate, fingerprint)
                        stats["written"] += 1
                        pending += 1
                        if commit is not None and pending >= batch_size:
                            commit()
                            pending = 0
        if progress is not None and stats["current_head_assets"] % progress_every == 0:
            progress(stats.copy())
    if mode == "apply" and commit is not None and pending:
        commit()
    return stats


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    _require_configuration(args)
    conn = psycopg.connect(_psycopg_database_url(args.database_url))
    conn.autocommit = False
    client = tos.TosClientV2(
        args.tos_access_key,
        args.tos_secret_key,
        args.tos_endpoint,
        args.tos_region,
    )
    try:
        if args.mode == "audit":
            conn.execute("set transaction read only")
        candidates = fetch_candidates(
            conn,
            limit=args.limit,
            shard_count=args.shard_count,
            shard_index=args.shard_index,
        )
        stats = process_candidates(
            candidates,
            mode=args.mode,
            read=lambda key: read_object(
                client,
                bucket=args.tos_bucket,
                key=key,
                maximum_bytes=args.maximum_object_bytes,
            ),
            write=lambda candidate, fingerprint: write_fingerprint(
                conn, candidate, fingerprint
            ),
            commit=conn.commit,
            progress_every=args.progress_every,
            progress=lambda current: print(
                json.dumps(
                    {"event": "progress", "mode": args.mode, **current},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            ),
        )
        if args.mode == "audit":
            conn.rollback()
        print(json.dumps({"mode": args.mode, **stats}, ensure_ascii=False, sort_keys=True))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
