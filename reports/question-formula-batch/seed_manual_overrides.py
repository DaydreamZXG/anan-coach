#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from common import ROOT, latex_braces_balanced, read_jsonl, sha256_file, write_json


SCHEMA = json.loads((ROOT / "formula-result.schema.json").read_text(encoding="utf-8"))


def main() -> None:
    overrides = json.loads((ROOT / "manual-overrides.json").read_text(encoding="utf-8"))["assets"]
    manifest = {row["sha256"]: row for row in read_jsonl(ROOT / "manifest.jsonl")}
    manifest_by_id = {row["asset_id"]: row for row in manifest.values()}
    id_overrides = []
    for filename, note in (
        ("manual-critical-overrides.json", "公式原图与原题上下文人工复核（2026-08-11）"),
        ("manual-pending-overrides.json", "216 张待复核公式逐图人工定稿（2026-08-11）"),
    ):
        path = ROOT / filename
        if path.exists():
            id_overrides.append(
                (json.loads(path.read_text(encoding="utf-8"))["assets"], note)
            )
    count = 0
    for digest, override in overrides.items():
        task = manifest[digest]
        actual = sha256_file(Path(task["source_path"]))
        latex = override["latex"].strip()
        result = {
            "schema_version": "formula-asset-v1",
            "asset_id": task["asset_id"],
            "sha256": digest,
            "source_path": task["source_path"],
            "priority": task["priority"],
            "status": "verified_override",
            "provider": "manual_verified",
            "provider_request_id": None,
            "blocks": [
                {
                    "type": "formula",
                    "format": "latex",
                    "expression": latex,
                    "display": bool(override["display"]),
                }
            ],
            "confidence": 1.0,
            "verification": {
                "source_hash_matches": actual == digest,
                "latex_nonempty": bool(latex),
                "braces_balanced": latex_braces_balanced(latex),
                "note": override["note"],
            },
        }
        jsonschema.validate(result, SCHEMA)
        write_json(ROOT / "results" / f"{task['asset_id']}.json", result)
        count += 1
    for overrides_by_id, note in id_overrides:
        for asset_id, latex_value in overrides_by_id.items():
            task = manifest_by_id[asset_id]
            actual = sha256_file(Path(task["source_path"]))
            latex = latex_value.strip()
            result = {
                "schema_version": "formula-asset-v1",
                "asset_id": task["asset_id"],
                "sha256": task["sha256"],
                "source_path": task["source_path"],
                "priority": task["priority"],
                "status": "verified_override",
                "provider": "manual_verified",
                "provider_request_id": None,
                "blocks": [
                    {"type": "formula", "format": "latex", "expression": latex, "display": True}
                ],
                "confidence": 1.0,
                "verification": {
                    "source_hash_matches": actual == task["sha256"],
                    "latex_nonempty": bool(latex),
                    "braces_balanced": latex_braces_balanced(latex),
                    "note": note,
                },
            }
            jsonschema.validate(result, SCHEMA)
            write_json(ROOT / "results" / f"{task['asset_id']}.json", result)
            count += 1
    print(f"seeded={count}")


if __name__ == "__main__":
    main()
