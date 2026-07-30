#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any
from uuid import uuid4

import httpx

try:
    from tools.audit.load.target_safety import validate_http_load_target
except ModuleNotFoundError:  # Direct execution resolves modules from this directory.
    from target_safety import validate_http_load_target


def validate_target(
    base_url: str,
    *,
    allow_nonlocal: bool,
    target_environment_id: str,
    expected_target_host: str = "",
    attestation_path: Path | str | None = None,
    signature_path: Path | str | None = None,
    minimum_validity_seconds: float = 0,
) -> None:
    validate_http_load_target(
        base_url,
        allow_nonlocal=allow_nonlocal,
        target_environment_id=target_environment_id,
        expected_target_host=expected_target_host,
        attestation_path=attestation_path,
        signature_path=signature_path,
        minimum_validity_seconds=minimum_validity_seconds,
    )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--allow-nonlocal", action="store_true")
    parser.add_argument("--target-environment-id", default="")
    parser.add_argument("--expected-target-host", default="")
    parser.add_argument("--target-attestation", type=Path, required=True)
    parser.add_argument("--target-attestation-signature", type=Path, required=True)
    parser.add_argument("--users", type=Path, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--operation",
        choices=("today", "journey", "checkpoint", "submit"),
        default="today",
    )
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 2)


async def request(
    client: httpx.AsyncClient,
    *,
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if extra_headers:
        headers.update(extra_headers)
    started = time.perf_counter()
    try:
        response = await client.request(method, path, headers=headers, json=body)
        elapsed_ms = (time.perf_counter() - started) * 1000
        payload: Any
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:500]
        return {
            "method": method,
            "path": path,
            "status": response.status_code,
            "elapsedMs": round(elapsed_ms, 2),
            "payload": payload,
        }
    except Exception as exc:
        return {
            "method": method,
            "path": path,
            "status": 0,
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def run_user(
    client: httpx.AsyncClient,
    record: dict[str, str],
    operation: str,
) -> list[dict[str, Any]]:
    token = record["accessToken"]
    today = await request(client, method="GET", path="/api/v2/plans/today", token=token)
    results = [today]
    if operation == "today" or today["status"] != 200:
        return results

    data = today.get("payload", {}).get("data", {})
    task = next(
        (item for item in data.get("tasks", []) if item.get("track") == "xingce"), None
    )
    if not task:
        today["assertionError"] = "today plan did not contain a xingce task"
        return results
    started = await request(
        client,
        method="POST",
        path="/api/v2/xingce/task-sessions",
        token=token,
        body={"planTaskId": task["id"]},
        idempotency_key=f"load-start-{uuid4().hex}",
    )
    results.append(started)
    if started["status"] != 200:
        return results

    projection = started.get("payload", {}).get("data", {})
    session_id = projection.get("sessionId")
    if operation in {"checkpoint", "submit"} and session_id:
        lease = await request(
            client,
            method="POST",
            path="/api/v2/task-editing/leases",
            token=token,
            body={
                "resourceType": "xingce_task_session",
                "resourceId": session_id,
                "deviceId": f"codex-load-device-{record['userId']}",
                "deviceLabel": "Codex 压测设备",
                "localBaseRevision": projection["revision"],
                "hasLocalChanges": False,
            },
        )
        results.append(lease)
        if lease["status"] != 200:
            return results
        lease_data = lease.get("payload", {}).get("data", {})
        lease_headers = {
            "X-Task-Edit-Lease": lease_data["leaseId"],
            "X-Task-Edit-Generation": str(lease_data["generation"]),
        }
        questions = projection.get("bundle", {}).get("questions", [])
        if not questions:
            started["assertionError"] = "started session did not contain questions"
            return results
        question = questions[0]
        elapsed_seconds = int(projection.get("totalElapsedSeconds", 0)) + 1
        checkpoint_answers = []
        for question_index, bundled_question in enumerate(questions):
            selected = (
                [bundled_question["options"][0]["optionId"]]
                if question_index == 0
                else []
            )
            checkpoint_answers.append(
                {
                    "questionId": bundled_question["questionId"],
                    "selectedOptionIds": selected,
                    "eliminatedOptionIds": [],
                    "marked": False,
                    "skipped": question_index != 0,
                    "elapsedSeconds": elapsed_seconds if question_index == 0 else 0,
                    "draftText": "",
                    "annotations": [],
                    "actionEvents": (
                        [
                            {
                                "atMs": elapsed_seconds * 1000,
                                "sessionAtMs": elapsed_seconds * 1000,
                                "type": "option_selected",
                                "optionId": selected[0],
                            }
                        ]
                        if selected
                        else []
                    ),
                }
            )
        checkpoint = await request(
            client,
            method="PUT",
            path=f"/api/v2/xingce/task-sessions/{session_id}/checkpoint",
            token=token,
            body={
                "expectedRevision": projection["revision"],
                "currentQuestionId": question["questionId"],
                "answers": checkpoint_answers,
                "totalElapsedSeconds": elapsed_seconds,
            },
            idempotency_key=f"load-checkpoint-{uuid4().hex}",
            extra_headers=lease_headers,
        )
        results.append(checkpoint)
        if checkpoint["status"] != 200:
            return results
        projection = checkpoint.get("payload", {}).get("data", {})
    if operation == "submit" and session_id:
        submitted = await request(
            client,
            method="POST",
            path=f"/api/v2/xingce/task-sessions/{session_id}/submit",
            token=token,
            body={
                "expectedRevision": projection["revision"],
                "totalElapsedSeconds": projection["totalElapsedSeconds"],
            },
            idempotency_key=f"load-submit-{uuid4().hex}",
            extra_headers=lease_headers,
        )
        results.append(submitted)
        if submitted["status"] != 200:
            return results
    if session_id:
        results.append(
            await request(
                client,
                method="GET",
                path=f"/api/v2/xingce/task-sessions/{session_id}",
                token=token,
            )
        )
    return results


async def main_async(args: argparse.Namespace) -> None:
    if args.offset < 0 or not 1 <= args.count <= 100:
        raise ValueError("load probe requires 1..100 users and a non-negative offset")
    if not 0 < args.timeout <= 60:
        raise ValueError("load probe timeout must be within (0, 60] seconds")
    validate_target(
        args.base_url,
        allow_nonlocal=args.allow_nonlocal,
        target_environment_id=args.target_environment_id,
        expected_target_host=args.expected_target_host,
        attestation_path=args.target_attestation,
        signature_path=args.target_attestation_signature,
        minimum_validity_seconds=(args.timeout * 8) + 5,
    )
    manifest = json.loads(args.users.read_text())
    selected = manifest["users"][args.offset : args.offset + args.count]
    if len(selected) != args.count:
        raise ValueError("requested user slice is outside the manifest")
    limits = httpx.Limits(
        max_connections=max(args.count, 10),
        max_keepalive_connections=max(args.count, 10),
    )
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=timeout,
        limits=limits,
        trust_env=False,
    ) as client:
        started_at = time.time()
        nested = await asyncio.gather(
            *(run_user(client, record, args.operation) for record in selected)
        )
        wall_ms = (time.time() - started_at) * 1000

    results = [item for group in nested for item in group]
    latencies = [float(item["elapsedMs"]) for item in results]
    status_counts = Counter(str(item["status"]) for item in results)
    failures = [
        item
        for item in results
        if item["status"] < 200 or item["status"] >= 300 or item.get("assertionError")
    ]
    summary = {
        "runId": manifest["runId"],
        "operation": args.operation,
        "users": len(selected),
        "requests": len(results),
        "wallMs": round(wall_ms, 2),
        "statusCounts": dict(status_counts),
        "errors": len(failures),
        "errorRate": round(len(failures) / len(results), 4) if results else 0,
        "latencyMs": {
            "min": round(min(latencies), 2) if latencies else None,
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2) if latencies else None,
        },
        "failures": failures[:20],
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
    args.output.write_bytes(encoded)
    args.output.chmod(0o600)
    args.output.with_name(f"{args.output.name}.sha256").write_text(
        f"{sha256(encoded).hexdigest()}  {args.output.name}\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in {"results", "failures"}
            }
        )
    )
    if failures:
        raise SystemExit(2)


def main() -> None:
    asyncio.run(main_async(arguments()))


if __name__ == "__main__":
    main()
