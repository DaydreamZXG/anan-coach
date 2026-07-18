#!/usr/bin/env python3
"""Probe an OpenAI-compatible provider without persisting its API credential."""

from __future__ import annotations

import argparse
from getpass import getpass
import json
import secrets
import sys
import time

import httpx


def _request(
    *, endpoint: str, api_key: str, model: str, key: str, mode: str
) -> dict[str, object]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return one valid JSON object and no Markdown.",
            },
            {
                "role": "user",
                "content": (
                    'Return exactly {"reply":"PONG"}'
                    if mode != "minimal"
                    else "Reply with exactly: PONG"
                ),
            },
        ],
        "temperature": 0,
    }
    if mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "provider_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"reply": {"type": "string", "const": "PONG"}},
                    "required": ["reply"],
                    "additionalProperties": False,
                },
            },
        }
    started = time.monotonic()
    response = httpx.post(
        endpoint.rstrip("/") + "/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        },
        content=json.dumps(payload, separators=(",", ":")),
        timeout=httpx.Timeout(60, connect=10),
    )
    response.raise_for_status()
    latency_ms = round((time.monotonic() - started) * 1000)
    headers = response.headers
    envelope = response.json()
    usage = envelope.get("usage") if isinstance(envelope, dict) else None
    choices = envelope.get("choices") if isinstance(envelope, dict) else None
    content = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
    parsed_content = (
        json.loads(content)
        if mode != "minimal" and isinstance(content, str)
        else content
    )
    return {
        "status": response.status_code,
        "latencyMs": latency_ms,
        "responseIdPresent": bool(envelope.get("id")),
        "responseModel": envelope.get("model"),
        "requestIdHeaders": sorted(
            name
            for name in headers
            if "request" in name or "trace" in name or name.endswith("-id")
        ),
        "usage": usage,
        "content": parsed_content,
        "responseId": envelope.get("id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    api_key = getpass("API key: ")
    stable_key = "provider-probe-" + secrets.token_hex(12)
    try:
        minimal = _request(
            endpoint=args.base_url,
            api_key=api_key,
            model=args.model,
            key=stable_key,
            mode="minimal",
        )
        first = _request(
            endpoint=args.base_url,
            api_key=api_key,
            model=args.model,
            key=stable_key,
            mode="json_object",
        )
        second = _request(
            endpoint=args.base_url,
            api_key=api_key,
            model=args.model,
            key=stable_key,
            mode="json_object",
        )
        schema = _request(
            endpoint=args.base_url,
            api_key=api_key,
            model=args.model,
            key="provider-probe-schema-" + secrets.token_hex(12),
            mode="json_schema",
        )
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as exc:
        print(f"probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    safe_minimal = dict(minimal)
    safe_first = dict(first)
    safe_second = dict(second)
    safe_schema = dict(schema)
    safe_minimal.pop("responseId", None)
    first_id = safe_first.pop("responseId", None)
    second_id = safe_second.pop("responseId", None)
    safe_schema.pop("responseId", None)
    print(
        json.dumps(
            {
                "first": safe_first,
                "minimal": safe_minimal,
                "jsonSchema": safe_schema,
                "second": safe_second,
                "sameIdempotencyKeyReplayed": bool(first_id) and first_id == second_id,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
