#!/usr/bin/env python3
"""Unified final gate. Missing or pending evidence can never become accepted."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_GATES = (
    "sourceHash",
    "panelCount",
    "optionCount",
    "connectedComponents",
    "endpointsJunctionsLoops",
    "fillState",
    "arrow",
    "text",
    "direction",
    "exactAngles",
    "pixelFit",
    "answerPreservation",
    "frontendContract",
)


def answer_preservation(source_answer: str | None, source_map: dict[str, str], rendered_map: dict[str, str]) -> dict[str, Any]:
    if not source_answer:
        return {"status": "fail", "reason": "source_answer_missing"}
    source_panel = source_map.get(source_answer)
    rendered_panel = rendered_map.get(source_answer)
    status = "pass" if source_panel and source_panel == rendered_panel else "fail"
    return {"status": status, "sourceAnswerLabel": source_answer, "sourcePanel": source_panel, "renderedPanel": rendered_panel}


def evaluate(gates: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, str] = {}
    failures: list[str] = []
    for name in REQUIRED_GATES:
        value = gates.get(name)
        status = value.get("status") if isinstance(value, dict) else value
        status = str(status or "missing")
        normalized[name] = status
        if status not in {"pass", "not_applicable"}:
            failures.append(name)
    return {
        "schemaVersion": "graphic-reasoning-final-gate-v1",
        "status": "accepted" if not failures else "repair_pending",
        "gates": normalized,
        "blockingGates": failures,
        "rule": "accepted requires every required gate to be pass or evidence-backed not_applicable; missing/pending/fail never closes",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    result = evaluate(evidence.get("gates") or evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
