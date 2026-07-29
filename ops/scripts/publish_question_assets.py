#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import mimetypes
import os
from pathlib import Path
import threading

import tos


APPROVED_PREFIX = (
    "data/question-bank-export/files/source-question-bank/"
    "fenbi-final-analysis-v1-20260613-assets/"
)
APPROVED_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png"})
CACHE_CONTROL = "public, max-age=31536000, immutable"
_thread_local = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish only manifest-approved question images to TOS."
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=16)
    return parser.parse_args()


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def client() -> tos.TosClientV2:
    current = getattr(_thread_local, "client", None)
    if current is None:
        current = tos.TosClientV2(
            required_env("TOS_ACCESS_KEY"),
            required_env("TOS_SECRET_KEY"),
            required_env("TOS_ENDPOINT"),
            required_env("TOS_REGION"),
        )
        _thread_local.client = current
    return current


def approved_key(value: str) -> str:
    candidate = value.strip()
    parts = candidate.split("/")
    if (
        not candidate.startswith(APPROVED_PREFIX)
        or "\\" in candidate
        or any(
            part in {"", ".", ".."} or any(ord(character) < 32 for character in part)
            for part in parts
        )
        or Path(candidate).suffix.casefold() not in APPROVED_SUFFIXES
    ):
        raise ValueError(f"object key is outside the approved question asset prefix: {value!r}")
    return candidate


def upload(root: Path, bucket: str, key: str) -> tuple[str, int]:
    source = root / key
    if not source.is_file():
        raise FileNotFoundError(source)
    expected_size = source.stat().st_size
    try:
        existing = client().head_object(bucket, key)
    except tos.exceptions.TosServerError as error:
        if error.status_code != 404:
            raise
    else:
        if existing.content_length == expected_size:
            return "skipped", expected_size
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    client().put_object_from_file(
        bucket,
        key,
        str(source),
        content_type=content_type,
        cache_control=CACHE_CONTROL,
        forbid_overwrite=False,
    )
    verified = client().head_object(bucket, key)
    if verified.content_length != expected_size:
        raise RuntimeError(f"uploaded size mismatch for {key}")
    return "uploaded", expected_size


def main() -> None:
    args = parse_args()
    if args.workers < 1 or args.workers > 64:
        raise ValueError("--workers must be between 1 and 64")
    root = args.root.resolve()
    manifest = args.manifest.resolve()
    keys = sorted({approved_key(line) for line in manifest.read_text().splitlines() if line})
    if not keys:
        raise ValueError("manifest is empty")
    bucket = required_env("TOS_BUCKET")
    counts = {"uploaded": 0, "skipped": 0}
    completed_bytes = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(upload, root, bucket, key): key for key in keys}
        for index, future in enumerate(as_completed(futures), start=1):
            status, byte_size = future.result()
            counts[status] += 1
            completed_bytes += byte_size
            if index == 1 or index % 500 == 0 or index == len(keys):
                print(
                    f"progress={index}/{len(keys)} uploaded={counts['uploaded']} "
                    f"skipped={counts['skipped']} bytes={completed_bytes}",
                    flush=True,
                )
    print(
        f"complete objects={len(keys)} uploaded={counts['uploaded']} "
        f"skipped={counts['skipped']} bytes={completed_bytes}",
        flush=True,
    )


if __name__ == "__main__":
    main()
