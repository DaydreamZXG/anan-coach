#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
import json

import jsonschema

from common import ROOT, read_jsonl, write_json


def main() -> None:
    schema = json.loads((ROOT / "formula-result.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    manifest = list(read_jsonl(ROOT / "manifest.jsonl"))
    status = Counter()
    invalid: list[dict[str, str]] = []
    completed = set()
    for path in sorted((ROOT / "results").glob("*.json")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            validator.validate(result)
            completed.add(result["asset_id"])
            status[result["status"]] += 1
        except Exception as error:
            invalid.append({"path": str(path), "error": str(error)[:240]})
    summary = {
        "schema_version": "formula-verification-summary-v1",
        "asset_count": len(manifest),
        "processed_count": len(completed),
        "pending_count": len(manifest) - len(completed),
        "status_counts": dict(status),
        "schema_invalid_count": len(invalid),
        "schema_invalid": invalid,
    }
    write_json(ROOT / "verification-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
