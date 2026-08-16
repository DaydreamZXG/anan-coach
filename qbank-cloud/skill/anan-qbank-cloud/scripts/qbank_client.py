#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def call(method: str, path: str, body=None) -> dict:
    base = os.environ.get("QBANK_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("QBANK_API_KEY", "")
    if not base or not api_key:
        raise SystemExit("QBANK_BASE_URL and QBANK_API_KEY must be configured locally")
    data = json.dumps(body).encode() if body is not None else None
    request = Request(base + path, data=data, method=method, headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"
    })
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        print(error.read().decode(), file=sys.stderr)
        raise SystemExit(error.code) from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Call the bounded Anan QBank Cloud API")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--kind", choices=["practice", "mock"], required=True)
    create.add_argument("--exam", choices=["guokao", "shengkao", "sydw"], required=True)
    create.add_argument("--count", type=int)
    create.add_argument("--template-id")
    create.add_argument("--request-id", required=True)
    page = commands.add_parser("page")
    page.add_argument("--session-id", required=True)
    page.add_argument("--offset", type=int, default=0)
    page.add_argument("--limit", type=int, default=20)
    submit = commands.add_parser("submit")
    submit.add_argument("--session-id", required=True)
    submit.add_argument("--request-id", required=True)
    submit.add_argument("--answers-file", required=True)

    args = parser.parse_args()
    if args.command == "create":
        if args.kind == "practice" and (args.count is None or not 5 <= args.count <= 20):
            parser.error("practice requires --count from 5 through 20")
        if args.kind == "mock" and args.count is not None:
            parser.error("mock uses the complete template and does not accept --count")
        body = {"requestId": args.request_id, "kind": args.kind, "exam": args.exam}
        if args.count is not None:
            body["questionCount"] = args.count
        if args.template_id:
            body["templateId"] = args.template_id
        result = call("POST", "/v1/sessions", body)
    elif args.command == "page":
        if not 1 <= args.limit <= 20 or args.offset < 0:
            parser.error("--limit must be 1..20 and --offset must be nonnegative")
        result = call("GET", f"/v1/sessions/{args.session_id}/questions?{urlencode({'offset': args.offset, 'limit': args.limit})}")
    else:
        with open(args.answers_file, encoding="utf-8") as stream:
            answers = json.load(stream)
        result = call("POST", f"/v1/sessions/{args.session_id}/submit", {
            "requestId": args.request_id, "answers": answers
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
