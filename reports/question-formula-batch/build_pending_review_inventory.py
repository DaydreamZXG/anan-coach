#!/usr/bin/env python3
"""Build a deterministic inventory for every formula still awaiting manual review."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> None:
    rows = []
    for path in sorted((ROOT / "results").glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("status") != "needs_manual_review":
            continue
        rows.append(
            {
                "asset_id": result["asset_id"],
                "qwen_confidence": result.get("confidence"),
                "expression": result["blocks"][0]["expression"],
                "ocr_text": "",
                "reason": "manual_formula_review",
            }
        )

    payload = {
        "schema_version": "formula-pending-review-inventory-v1",
        "accepted": rows,
        "count": len(rows),
    }
    output = ROOT / "pending-review-inventory.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "count": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
