#!/usr/bin/env python3
"""Pack deduplicated formula images into a flat tar for an isolated GPU runner."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import tarfile

from common import ROOT, read_jsonl


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifest.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    rows = list(read_jsonl(args.manifest))
    if args.limit > 0:
        rows = rows[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w") as archive:
        manifest_data = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ).encode("utf-8")
        info = tarfile.TarInfo("manifest.jsonl")
        info.size = len(manifest_data)
        archive.addfile(info, BytesIO(manifest_data))
        for index, row in enumerate(rows, 1):
            source = Path(row["source_path"])
            suffix = source.suffix.lower() or ".png"
            archive.add(source, arcname=f"inputs/{row['asset_id']}{suffix}", recursive=False)
            if index % 5000 == 0:
                print(f"{index}/{len(rows)}", flush=True)
    print(json.dumps({"assets": len(rows), "output": str(args.output), "bytes": args.output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
