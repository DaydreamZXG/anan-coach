#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import random
import statistics
import time
from typing import Any
from uuid import uuid4

import httpx


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(quantile * len(ordered)) - 1)], 2)


async def measured(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Authorization": f"Bearer {token}"}
    request_headers.update(headers or {})
    started = time.perf_counter()
    try:
        response = await client.request(method, path, headers=request_headers, json=body)
        elapsed = (time.perf_counter() - started) * 1000
        try:
            payload: Any = response.json()
        except Exception:
            payload = response.text[:500]
        return {
            "method": method,
            "path": path,
            "status": response.status_code,
            "elapsedMs": round(elapsed, 2),
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


async def virtual_user(
    client: httpx.AsyncClient,
    record: dict[str, str],
    *,
    deadline: float,
    ramp_seconds: float,
) -> list[dict[str, Any]]:
    rng = random.Random(record["userId"])
    await asyncio.sleep(rng.uniform(0, ramp_seconds))
    token = record["accessToken"]
    results: list[dict[str, Any]] = []

    today = await measured(client, "GET", "/api/v2/plans/today", token=token)
    results.append(today)
    if today["status"] != 200:
        return results
    task = next(
        (
            item
            for item in today["payload"].get("data", {}).get("tasks", [])
            if item.get("track") == "xingce"
        ),
        None,
    )
    if not task:
        today["assertionError"] = "today plan did not contain a xingce task"
        return results
    started = await measured(
        client,
        "POST",
        "/api/v2/xingce/task-sessions",
        token=token,
        body={"planTaskId": task["id"]},
        headers={"Idempotency-Key": f"sustained-start-{uuid4().hex}"},
    )
    results.append(started)
    if started["status"] != 200:
        return results
    projection = started["payload"]["data"]
    session_id = projection["sessionId"]
    lease = await measured(
        client,
        "POST",
        "/api/v2/task-editing/leases",
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
    lease_data = lease["payload"]["data"]
    lease_headers = {
        "X-Task-Edit-Lease": lease_data["leaseId"],
        "X-Task-Edit-Generation": str(lease_data["generation"]),
    }

    elapsed_seconds = int(projection.get("totalElapsedSeconds", 0))
    next_save = time.monotonic() + 15
    while time.monotonic() < deadline:
        await asyncio.sleep(rng.uniform(0.5, 1.5))
        if time.monotonic() < next_save:
            continue
        question = projection["bundle"]["questions"][0]
        elapsed_seconds += 15
        checkpoint = await measured(
            client,
            "PUT",
            f"/api/v2/xingce/task-sessions/{session_id}/checkpoint",
            token=token,
            body={
                "expectedRevision": projection["revision"],
                "currentQuestionId": question["questionId"],
                "answers": [
                    {
                        "questionId": question["questionId"],
                        "selectedOptionIds": [question["options"][0]["optionId"]],
                        "eliminatedOptionIds": [],
                        "marked": False,
                        "skipped": False,
                        "elapsedSeconds": elapsed_seconds,
                        "draftText": "",
                        "annotations": [],
                        "actionEvents": [],
                    }
                ],
                "totalElapsedSeconds": elapsed_seconds,
            },
            headers={
                **lease_headers,
                "Idempotency-Key": f"sustained-checkpoint-{uuid4().hex}",
            },
        )
        results.append(checkpoint)
        if checkpoint["status"] != 200:
            break
        projection = checkpoint["payload"]["data"]
        next_save += 15
    return results


async def main_async(args: argparse.Namespace) -> None:
    manifest = json.loads(args.users.read_text())
    selected = manifest["users"][: args.count]
    deadline = time.monotonic() + args.duration
    limits = httpx.Limits(
        max_connections=max(args.count, 20),
        max_keepalive_connections=max(args.count, 20),
    )
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(args.timeout),
        limits=limits,
    ) as client:
        started = time.perf_counter()
        nested = await asyncio.gather(
            *(
                virtual_user(
                    client,
                    record,
                    deadline=deadline,
                    ramp_seconds=args.ramp,
                )
                for record in selected
            )
        )
        wall_seconds = time.perf_counter() - started
    results = [item for group in nested for item in group]
    failures = [
        item
        for item in results
        if item["status"] < 200
        or item["status"] >= 300
        or item.get("assertionError")
    ]
    latencies = [float(item["elapsedMs"]) for item in results]
    summary = {
        "runId": manifest["runId"],
        "users": len(selected),
        "durationSeconds": args.duration,
        "rampSeconds": args.ramp,
        "wallSeconds": round(wall_seconds, 2),
        "requests": len(results),
        "requestsPerSecond": round(len(results) / wall_seconds, 2),
        "statusCounts": dict(Counter(str(item["status"]) for item in results)),
        "errors": len(failures),
        "errorRate": round(len(failures) / len(results), 4) if results else 0,
        "latencyMs": {
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2) if latencies else None,
        },
        "failures": failures[:20],
        "results": results,
    }
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key not in {"results", "failures"}}
        )
    )
    if failures:
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://kg.xingguoxian.cn")
    parser.add_argument("--users", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--ramp", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
