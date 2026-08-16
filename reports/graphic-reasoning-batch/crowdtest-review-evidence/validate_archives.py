#!/usr/bin/env python3
"""Independently validate downloaded ZIPs with the public validator."""

from __future__ import annotations

import json
import subprocess
import tempfile
import zipfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[3]
PUBLIC = PROJECT / "reports/graphic-reasoning-batch/crowdtest-package/build/graphic-reasoning-semantic-public-v1"
VALIDATOR = PUBLIC / "tools/validate_submission.py"
DESKTOP = Path("/Users/zxg/Desktop/未命名文件夹")
ARCHIVES = {
    "summit": DESKTOP / "summit/3640-10359-summit.zip",
    "rudder": DESKTOP / "rudder/3640-10360-rudder.zip",
    "pelican": DESKTOP / "pelican/3640-10362-pelican.zip",
    "quarry": DESKTOP / "quarry/3640-10361-quarry.zip",
}


def locate(root: Path) -> Path:
    hits = list(root.rglob("graphic-reasoning-semantic-public-v1/output/manifest.jsonl"))
    if len(hits) != 1:
        raise RuntimeError(f"expected one submission root, found {len(hits)}")
    return hits[0].parents[1]


def main() -> int:
    print("图推众测产物：独立契约验证")
    print("验证器：", VALIDATOR)
    print("说明：检查覆盖、JSON 结构、哈希引用和必需文件；不替代人工语义检查。\n")
    for model, archive_path in ARCHIVES.items():
        with tempfile.TemporaryDirectory(prefix=f"gr-{model}-") as temporary:
            temporary_path = Path(temporary)
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(temporary_path)
            submission = locate(temporary_path)
            report = temporary_path / "independent-validation.json"
            command = [
                "python3", str(VALIDATOR), "--root", str(submission),
                "--output", "output", "--report", str(report),
            ]
            completed = subprocess.run(command, text=True, capture_output=True)
            result = json.loads(report.read_text(encoding="utf-8"))
            state = json.loads((submission / "output/run-state.json").read_text(encoding="utf-8"))
            print(f"[{model.upper()}]")
            print(f"archive: {archive_path}")
            print(f"valid assets: {result['validAssetCount']}/{result['expectedAssetCount']}")
            print(f"structured_candidate: {state['structuredCandidateCount']}")
            print(f"repair_pending: {state['repairPendingCount']}")
            print(f"global errors: {result['globalErrors'] or 'none'}")
            print(f"validator exit: {completed.returncode}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
