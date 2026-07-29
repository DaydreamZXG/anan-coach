#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.domains.planning.service import PlanningService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", required=True)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--explain", action="store_true")
    args = parser.parse_args()
    user_id = json.load(open(args.users))["users"][args.index]["userId"]
    engine = create_engine(os.environ["DATABASE_URL"])
    calls: list[dict[str, object]] = []
    raw_calls: list[tuple[float, str, object]] = []

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._audit_started = time.perf_counter()
        context._audit_parameters = parameters

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.split())
        elapsed_ms = (time.perf_counter() - context._audit_started) * 1000
        raw_calls.append((elapsed_ms, statement, context._audit_parameters))
        calls.append(
            {
                "elapsedMs": round(elapsed_ms, 3),
                "statement": normalized,
                "parameters": repr(context._audit_parameters)[:2000],
            }
        )

    started = time.perf_counter()
    with Session(engine) as session:
        PlanningService().get_or_create_today(session, user_id=user_id)
        session.rollback()
    wall_ms = (time.perf_counter() - started) * 1000
    grouped: dict[str, dict[str, float | int | str]] = defaultdict(
        lambda: {"count": 0, "totalMs": 0.0, "maxMs": 0.0, "statement": ""}
    )
    for call in calls:
        key = str(call["statement"])[:160]
        group = grouped[key]
        elapsed = float(call["elapsedMs"])
        group["count"] = int(group["count"]) + 1
        group["totalMs"] = round(float(group["totalMs"]) + elapsed, 3)
        group["maxMs"] = max(float(group["maxMs"]), elapsed)
        group["statement"] = str(call["statement"])
    explain = None
    if args.explain and raw_calls:
        _, statement, parameters = max(raw_calls, key=lambda item: item[0])
        with engine.raw_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
                    parameters,
                )
                explain = cursor.fetchone()[0]
            connection.rollback()
    summary = {
        "userId": user_id,
        "wallMs": round(wall_ms, 3),
        "sqlCalls": len(calls),
        "sqlTotalMs": round(sum(float(call["elapsedMs"]) for call in calls), 3),
        "slowest": sorted(calls, key=lambda item: float(item["elapsedMs"]), reverse=True)[:20],
        "groups": sorted(
            grouped.values(), key=lambda item: float(item["totalMs"]), reverse=True
        ),
        "explain": explain,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
