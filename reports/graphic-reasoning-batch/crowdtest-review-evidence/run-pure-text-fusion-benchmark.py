#!/usr/bin/env python3
"""Incrementally revalidate only pending fused semantic cases with a text model."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import getpass
import hashlib
import importlib.util
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
FUSION_ROOT = HERE / "pure-text-semantic-fusion-v1"
PRIVATE_ROOT = HERE.parent / "crowdtest-package/build/graphic-reasoning-semantic-private-evaluation-v1"
DEFAULT_OUTPUT = FUSION_ROOT / "benchmark-v1"
SYSTEM_PROMPT = """你是图形推理题求解器。输入是一道题的题干、选项和由四次独立视觉检查形成的规范化语义证据，你看不到原图，也不知道正确答案。
仅依据题干、选项、elements、relations、counts、topology、selectedObservations 和 reasoningCandidates 推理。
elements/relations 中 modelSupport>=2 的事实得到多次观察支持；baseSpecific* 只用于补足没有共识的细节，不得覆盖更高支持事实。若分框有冲突，优先题目资产角色、选项标签和支持数更高的版式。
不得调用网络、检索题库、猜测题目来源或利用常见答案分布。即使信息不足，也选择最可能的一个选项，同时把 informationSufficient 设为 false。
只输出一个 JSON 对象：{"questionId":"原值","answer":"A","informationSufficient":true,"reason":"不超过120字"}。answer 只能是 A-H，不得输出 Markdown。"""
PRINT_LOCK = threading.Lock()
STATUS_LOCK = threading.Lock()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def parse_content(content: str, expected_qid: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("questionId") != expected_qid:
        raise ValueError("response questionId mismatch")
    if value.get("answer") not in "ABCDEFGH":
        raise ValueError("answer must be A-H")
    if not isinstance(value.get("informationSufficient"), bool):
        raise ValueError("informationSufficient must be boolean")
    if not isinstance(value.get("reason"), str):
        raise ValueError("reason must be string")
    return {
        "questionId": expected_qid,
        "answer": value["answer"],
        "informationSufficient": value["informationSufficient"],
        "reason": value["reason"][:500],
    }


def call_api(*, key: str, api_base: str, api_model: str, case: dict[str, Any],
             timeout: int, max_tokens: int, thinking: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": api_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "请解答以下一道题。JSON 输入：\n" + stable_json(case)},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "stream": False,
    }
    if thinking:
        payload["thinking"] = {"type": thinking}
    request = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions",
        data=stable_json(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "graphic-reasoning-pure-text-fusion/1.0",
        },
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        result = json.loads(response.read().decode("utf-8"))
    elapsed = round(time.monotonic() - started, 3)
    choice = result["choices"][0]
    prediction = parse_content(choice["message"].get("content") or "", case["questionId"])
    return prediction, {
        "questionId": case["questionId"],
        "requestSha256": digest({
            "system": SYSTEM_PROMPT, "case": case, "model": api_model, "maxTokens": max_tokens,
        }),
        "responseId": result.get("id"),
        "finishReason": choice.get("finish_reason"),
        "elapsedSeconds": elapsed,
        "usage": result.get("usage") or {},
    }


def retryable(exc: Exception) -> bool:
    return not isinstance(exc, urllib.error.HTTPError) or exc.code in {408, 409, 429, 500, 502, 503, 504}


def stratified(rows: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit is None or limit >= len(rows):
        return rows
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row["visualGrammar"]].append(row)
    result = []
    grammars = sorted(buckets)
    cursor = 0
    while len(result) < limit and any(buckets.values()):
        grammar = grammars[cursor % len(grammars)]
        cursor += 1
        if buckets[grammar]:
            result.append(buckets[grammar].pop(0))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="https://opencode.ai/zen/go/v1")
    parser.add_argument("--api-model", default="deepseek-v4-flash")
    parser.add_argument("--provider-label", default="OpenCode Go")
    parser.add_argument("--fusion-root", type=Path, default=FUSION_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--concurrency", type=int, default=128)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default=None)
    parser.add_argument("--prompt-api-key", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--grammars", nargs="+", default=None)
    parser.add_argument("--min-prior-correct-support", type=int, default=0)
    parser.add_argument("--seed-from", action="append", type=Path, default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 512:
        parser.error("--concurrency must be 1..512")

    question_rows = {
        row["questionId"]: row for row in read_jsonl(args.fusion_root / "questions.jsonl")
    }
    unified_rows = {
        row["questionId"]: row
        for row in read_jsonl(HERE / "unified-semantic-assets-v1/questions.jsonl")
    }
    pending = read_jsonl(args.fusion_root / "pending-cases-answer-free.jsonl")
    if args.grammars:
        allowed_grammars = set(args.grammars)
        pending = [row for row in pending if row["visualGrammar"] in allowed_grammars]
    pending = [
        row for row in pending
        if int(unified_rows[row["questionId"]].get("correctSupport") or 0)
        >= args.min_prior_correct_support
    ]
    pending = stratified(pending, args.limit)
    cases = {row["questionId"]: row for row in pending}
    keys = {
        row["questionId"]: row["officialAnswer"]
        for row in read_jsonl(PRIVATE_ROOT / "question-key-private.jsonl")
        if row["questionId"] in cases
    }
    predictions_path = args.output / "predictions.jsonl"
    existing = {
        row["questionId"]: row for row in read_jsonl(predictions_path)
    } if predictions_path.exists() else {}
    seeded_from = Counter()
    for seed_dir in args.seed_from:
        seed_path = seed_dir / "predictions.jsonl"
        if not seed_path.exists():
            continue
        for row in read_jsonl(seed_path):
            qid = row.get("questionId")
            if qid in cases and qid not in existing:
                existing[qid] = row
                seeded_from[str(seed_dir)] += 1
    if existing:
        write_jsonl(predictions_path, [existing[key] for key in sorted(existing)])
    remaining = [case for qid, case in cases.items() if qid not in existing]
    protocol = {
        "schemaVersion": "graphic-reasoning-pure-text-fusion-benchmark-v1",
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "apiBase": args.api_base,
        "apiModel": args.api_model,
        "providerLabel": args.provider_label,
        "pendingQuestionCount": len(cases),
        "preservedPredictionCount": len(existing),
        "plannedNewRequestCount": len(remaining),
        "seededFrom": dict(sorted(seeded_from.items())),
        "concurrency": args.concurrency,
        "maxTokens": args.max_tokens,
        "answerIsolation": "official answers are used only after each API response for local scoring",
        "systemPromptSha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
    }
    write_json(args.output / "protocol.json", protocol)
    print(json.dumps(protocol, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key and args.prompt_api_key:
        key = getpass.getpass("API key（隐藏，不保存）: ").strip()
    if not key:
        raise SystemExit("API key missing; use --prompt-api-key or DEEPSEEK_API_KEY")

    lock = threading.Lock()
    state = {
        "state": "running", "expected": len(cases), "success": len(existing),
        "remaining": len(remaining), "errors": 0,
    }
    write_json(args.output / "status.json", state)

    def execute(case: dict[str, Any]) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        last_error = ""
        for attempt in range(1, args.retries + 1):
            try:
                prediction, audit = call_api(
                    key=key, api_base=args.api_base, api_model=args.api_model, case=case,
                    timeout=args.timeout, max_tokens=args.max_tokens, thinking=args.thinking,
                )
                official = keys[case["questionId"]]
                row = {
                    **prediction,
                    "correct": prediction["answer"] == official,
                    "provider": args.provider_label,
                    "elapsedSeconds": audit["elapsedSeconds"],
                    "usage": audit["usage"],
                    "requestSha256": audit["requestSha256"],
                }
                return case["questionId"], row, {**audit, "status": "success", "attempt": attempt}
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt == args.retries or not retryable(exc):
                    break
                time.sleep(min(8, 2 ** (attempt - 1)) + random.random())
        return case["questionId"], None, {
            "questionId": case["questionId"], "status": "error",
            "attempt": args.retries, "error": last_error,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(execute, case) for case in remaining]
        for future in concurrent.futures.as_completed(futures):
            try:
                qid, row, audit = future.result()
            except Exception as exc:
                audit = {
                    "questionId": None,
                    "status": "worker_crash",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                with lock:
                    append_jsonl(args.output / "audit.jsonl", audit)
                    state["errors"] += 1
                    state["updatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                    write_json(args.output / "status.json", state)
                continue
            with lock:
                append_jsonl(args.output / "audit.jsonl", audit)
                if row:
                    existing[qid] = row
                    write_jsonl(predictions_path, [existing[key] for key in sorted(existing)])
                    state["success"] = len(existing)
                else:
                    state["errors"] += 1
                state["remaining"] = len(cases) - len(existing)
                state["updatedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
                write_json(args.output / "status.json", state)
                if (state["success"] + state["errors"]) % 25 == 0:
                    with PRINT_LOCK:
                        print(json.dumps(state, ensure_ascii=False), flush=True)

    by_grammar: dict[str, Counter] = defaultdict(Counter)
    promoted = []
    unresolved = []
    for qid, case in cases.items():
        row = existing.get(qid)
        grammar = question_rows[qid]["visualGrammar"]
        by_grammar[grammar]["expected"] += 1
        if row:
            by_grammar[grammar]["answered"] += 1
            by_grammar[grammar]["correct"] += int(row["correct"])
            by_grammar[grammar]["sufficient"] += int(row["informationSufficient"])
            if row["correct"] and row["informationSufficient"]:
                promoted.append({
                    "questionId": qid, "visualGrammar": grammar,
                    "validation": "correct_and_information_sufficient",
                    "provider": row["provider"], "requestSha256": row["requestSha256"],
                })
                continue
        unresolved.append({
            "questionId": qid, "visualGrammar": grammar,
            "reason": (
                "no_prediction" if not row else
                "incorrect" if not row["correct"] else "information_insufficient"
            ),
        })
    final = {
        "schemaVersion": "graphic-reasoning-pure-text-fusion-score-v1",
        "expected": len(cases),
        "answered": len(existing),
        "correct": sum(bool(row["correct"]) for row in existing.values()),
        "correctAndInformationSufficient": len(promoted),
        "promotedQuestionCount": len(promoted),
        "unresolvedQuestionCount": len(unresolved),
        "byVisualGrammar": {key: dict(value) for key, value in sorted(by_grammar.items())},
        "promotionRule": "one fresh answer-isolated text-only run must be correct and informationSufficient=true",
    }
    state.update({"state": "complete", "remaining": len(cases) - len(existing)})
    write_json(args.output / "status.json", state)
    write_json(args.output / "score.json", final)
    write_jsonl(args.output / "promoted.jsonl", promoted)
    write_jsonl(args.output / "unresolved.jsonl", unresolved)
    print(json.dumps(final, ensure_ascii=False, indent=2), flush=True)
    return 0 if len(existing) == len(cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
