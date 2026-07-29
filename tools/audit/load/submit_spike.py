#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import time
from uuid import uuid4

import httpx


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(quantile * len(ordered)) - 1)], 2)


async def submit(
    client: httpx.AsyncClient,
    *,
    token: str,
    session: dict,
    lease: dict,
) -> dict:
    path = f"/api/v2/xingce/task-sessions/{session['sessionId']}/submit"
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": f"submit-spike-{uuid4().hex}",
        "X-Task-Edit-Lease": lease["leaseId"],
        "X-Task-Edit-Generation": str(lease["generation"]),
    }
    started = time.perf_counter()
    try:
        response = await client.post(
            path,
            headers=headers,
            json={
                "expectedRevision": session["revision"],
                "totalElapsedSeconds": session["totalElapsedSeconds"],
            },
        )
        elapsed = (time.perf_counter() - started) * 1000
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:500]
        return {
            "path": path,
            "status": response.status_code,
            "elapsedMs": round(elapsed, 2),
            "payload": payload,
        }
    except Exception as exc:
        return {
            "path": path,
            "status": 0,
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main_async(args: argparse.Namespace) -> None:
    users = json.loads(args.users.read_text())["users"]
    prepared: list[tuple[dict, dict]] = []
    for offset in range(0, len(users), 10):
        result_file = args.prep_directory / f"submit-prep-{offset}.json"
        results = json.loads(result_file.read_text())["results"]
        if len(results) != 50:
            raise ValueError(f"{result_file} did not contain 10 five-request preparations")
        for index in range(10):
            group = results[index * 5 : (index + 1) * 5]
            lease = group[2]["payload"]["data"]
            session = group[4]["payload"]["data"]
            prepared.append((session, lease))
    if len(prepared) != len(users):
        raise ValueError("prepared session count does not match user count")

    limits = httpx.Limits(max_connections=len(users), max_keepalive_connections=len(users))
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(args.timeout),
        limits=limits,
    ) as client:
        started = time.perf_counter()
        results = await asyncio.gather(
            *(
                submit(
                    client,
                    token=user["accessToken"],
                    session=session,
                    lease=lease,
                )
                for user, (session, lease) in zip(users, prepared)
            )
        )
        wall_ms = (time.perf_counter() - started) * 1000

    failures = [item for item in results if item["status"] < 200 or item["status"] >= 300]
    latencies = [float(item["elapsedMs"]) for item in results]
    summary = {
        "users": len(users),
        "requests": len(results),
        "wallMs": round(wall_ms, 2),
        "statusCounts": dict(Counter(str(item["status"]) for item in results)),
        "errors": len(failures),
        "errorRate": round(len(failures) / len(results), 4),
        "latencyMs": {
            "mean": round(statistics.fmean(latencies), 2),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2),
        },
        "failures": failures,
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
    parser.add_argument("--prep-directory", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
