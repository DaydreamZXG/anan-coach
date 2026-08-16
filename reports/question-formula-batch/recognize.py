#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from common import ROOT, latex_braces_balanced, read_jsonl, sha256_file, write_json


REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.domains.xfyun.config import credentials_from_environment  # noqa: E402
from app.domains.xfyun.formula import XfyunFormulaClient, XfyunFormulaError  # noqa: E402


RESULTS = ROOT / "results"
AUTO_CONFIDENCE = 0.95


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--priority", choices=("question_critical", "explanation"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rpm", type=float, default=50.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def task_result(task: dict[str, Any], client: XfyunFormulaClient) -> dict[str, Any]:
    source = Path(task["source_path"])
    digest = sha256_file(source)
    if digest != task["sha256"]:
        raise RuntimeError("source_hash_mismatch")
    recognition = client.recognize(source.read_bytes())
    formulas = [formula.strip() for line in recognition.lines for formula in line.formulas if formula.strip()]
    latex = " \\; ".join(formulas).strip()
    confidences = [line.confidence for line in recognition.lines if line.formulas]
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    balanced = latex_braces_balanced(latex)
    usable = bool(latex) and balanced
    status = "candidate_auto" if usable and confidence >= AUTO_CONFIDENCE else "needs_manual_review"
    result = {
        "schema_version": "formula-asset-v1",
        "asset_id": task["asset_id"],
        "sha256": task["sha256"],
        "source_path": task["source_path"],
        "priority": task["priority"],
        "status": status,
        "provider": "xfyun_formula",
        "provider_request_id": recognition.request_id,
        "blocks": [
            {"type": "formula", "format": "latex", "expression": latex or "[待复核]", "display": True}
        ],
        "confidence": round(confidence, 6),
        "verification": {
            "source_hash_matches": True,
            "latex_nonempty": bool(latex),
            "braces_balanced": balanced,
            "note": "讯飞候选；低置信或结构异常时保留原图并进入复核",
        },
    }
    return result


def failure_result(task: dict[str, Any], error: Exception) -> dict[str, Any]:
    note = "公式识别服务失败" if isinstance(error, XfyunFormulaError) else str(error)
    result = {
        "schema_version": "formula-asset-v1",
        "asset_id": task["asset_id"],
        "sha256": task["sha256"],
        "source_path": task["source_path"],
        "priority": task["priority"],
        "status": "failed",
        "provider": "xfyun_formula",
        "provider_request_id": None,
        "blocks": [{"type": "formula", "format": "latex", "expression": "[识别失败]", "display": True}],
        "confidence": None,
        "verification": {
            "source_hash_matches": note != "source_hash_mismatch",
            "latex_nonempty": False,
            "braces_balanced": False,
            "note": note[:240],
        },
    }
    return result


def main() -> None:
    args = arguments()
    if not 0 < args.rpm <= 50:
        raise SystemExit("--rpm 必须在 0 到 50 之间")
    tasks = [row for row in read_jsonl(ROOT / "manifest.jsonl") if not args.priority or row["priority"] == args.priority]
    pending = [row for row in tasks if not (RESULTS / f"{row['asset_id']}.json").is_file()]
    if args.limit > 0:
        pending = pending[: args.limit]
    print(json.dumps({"selected": len(tasks), "pending": len(pending), "dry_run": args.dry_run}, ensure_ascii=False))
    if args.dry_run or not pending:
        return
    client = XfyunFormulaClient(credentials_from_environment())
    interval = 60.0 / args.rpm
    RESULTS.mkdir(parents=True, exist_ok=True)
    previous_started = 0.0
    for index, task in enumerate(pending, 1):
        wait = interval - (time.monotonic() - previous_started)
        if wait > 0:
            time.sleep(wait)
        previous_started = time.monotonic()
        try:
            result = task_result(task, client)
        except Exception as error:
            result = failure_result(task, error)
        write_json(RESULTS / f"{task['asset_id']}.json", result)
        print(f"{index}/{len(pending)} {task['asset_id']} {result['status']}", flush=True)


if __name__ == "__main__":
    main()
