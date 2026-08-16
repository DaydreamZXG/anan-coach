#!/usr/bin/env python3
"""Read-only integrity tests for the generated crowdtest packages."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parent
PUBLIC_NAME = "graphic-reasoning-semantic-public-v1"
PRIVATE_NAME = "graphic-reasoning-semantic-private-evaluation-v1"
PUBLIC = ROOT / "build" / PUBLIC_NAME
PRIVATE = ROOT / "build" / PRIVATE_NAME
DIST = ROOT / "dist"


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(key)
            keys.extend(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(walk_keys(child))
    return keys


def main() -> int:
    inventory = jsonl(PUBLIC / "input" / "inventory-public.jsonl")
    assert len(inventory) == 3601
    assert len({row["assetId"] for row in inventory}) == 3601
    assert Counter(row["visualGrammarCandidate"] for row in inventory) == Counter({
        "regular_geometry": 2459,
        "folded_3d": 468,
        "rare_vector": 364,
        "glyph_life_object": 210,
        "textured_geometry": 92,
        "textured_pie": 5,
        "curve_circle_arc": 3,
    })
    forbidden = {"answerLabel", "officialAnswer", "correctAnswer", "solution", "analysis", "knowledgePoint", "questionId", "sourceJsonlLine", "sourcePaths", "checkpointKey"}
    for row in inventory:
        assert forbidden.isdisjoint(walk_keys(row)), row["assetId"]
        assert not any(isinstance(value, str) and value.startswith("/Users/") for value in row.values())
        image = PUBLIC / row["imagePath"]
        assert image.is_file()
        assert sha(image) == row["sourceSha256"]
        for ref in row["questionReferences"]:
            assert re.fullmatch(r"case_[0-9a-f]{20}", ref["caseId"])
    assert len(list((PUBLIC / "input" / "images").iterdir())) == 3601

    schema = json.loads((PUBLIC / "contract" / "semantic-scene-v2.schema.json").read_text(encoding="utf-8"))
    example = json.loads((PUBLIC / "examples" / "semantic-example.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(example)

    prompt = (ROOT / "submission-fields" / "first-prompt.md").read_text(encoding="utf-8")
    assert len(prompt.encode("utf-8")) < 30 * 1024
    assert "3,601" in prompt and "4,856" not in prompt
    assert "400+" not in prompt and "工具调用" not in prompt

    manual_rows = list(__import__("csv").DictReader((PRIVATE / "manual-review-sample-private.csv").open("r", encoding="utf-8-sig")))
    deepseek_rows = jsonl(PRIVATE / "deepseek-sample-private.jsonl")
    key = {row["questionId"]: row for row in jsonl(PRIVATE / "question-key-private.jsonl")}
    exclusions = jsonl(PRIVATE / "excluded-assets-private.jsonl")
    assert len(manual_rows) == 210
    assert len(deepseek_rows) == 400
    assert len(exclusions) == 1256
    assert all(key[row["questionId"]]["officialAnswer"] in {"A", "B", "C", "D"} for row in deepseek_rows)
    assert "gr_03633503ff1443587d48e1ee" not in {row["assetId"] for row in inventory}

    manifest = {row["file"]: row for row in jsonl(DIST / "package-manifest.jsonl")}
    for name in [f"{PUBLIC_NAME}.zip", f"{PRIVATE_NAME}.zip"]:
        archive_path = DIST / name
        assert archive_path.stat().st_size == manifest[name]["bytes"]
        assert sha(archive_path) == manifest[name]["sha256"]
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            members = archive.namelist()
            assert members and all(member.startswith(name.removesuffix(".zip") + "/") for member in members)
    with zipfile.ZipFile(DIST / f"{PUBLIC_NAME}.zip") as archive:
        names = archive.namelist()
        assert not any("private" in name.lower() or "answer-key" in name.lower() for name in names)
        assert sum(name.startswith(f"{PUBLIC_NAME}/input/images/") for name in names) == 3601

    print(json.dumps({
        "status": "passed",
        "publicAssets": len(inventory),
        "manualReviewAssets": len(manual_rows),
        "deepseekBlindQuestions": len(deepseek_rows),
        "archives": {name: {"bytes": row["bytes"], "sha256": row["sha256"]} for name, row in manifest.items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
