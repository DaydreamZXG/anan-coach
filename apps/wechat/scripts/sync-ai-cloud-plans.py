#!/usr/bin/env python3
"""校验并通过微信云测官方 API 同步岸岸 AI 全量用例与测试计划。"""

from __future__ import annotations

import argparse
from collections import Counter
import getpass
import json
import os
from pathlib import Path
import sys
import warnings

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL.*")
import requests


WECHAT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = WECHAT_ROOT / "minitest" / "ai-full-suite.json"
APP_CONFIG = WECHAT_ROOT / "miniprogram" / "app.json"
API_ORIGIN = "https://minitest.weixin.qq.com"
AI_CASE_PATH = "/thirdapi/ai_case"
AI_PLAN_PATH = "/thirdapi/ai_case_plan"
PLAN_LIST_PATH = "/thirdapi/case_plan_list"
ALLOWED_PRIORITIES = {"P0", "P1", "P2", "P3", "P4", "P5", "Others"}
ACCOUNT_PREAMBLES = {
    "mature": "使用已登录、完成新手引导并具有真实历史学习数据的成熟测试账号。",
    "disposable": (
        '第一步先调用 minium_func_call("ai_cloud_support.require_disposable_account")；'
        "只有返回昵称后才能继续，普通账号必须立即失败。"
    ),
    "fresh": "使用从未登录、从未完成新手引导的全新微信虚拟账号。",
}


class CloudApiError(RuntimeError):
    pass


def app_routes() -> tuple[str, ...]:
    app = json.loads(APP_CONFIG.read_text(encoding="utf-8"))
    routes = ["/" + route for route in app.get("pages", [])]
    for package in app.get("subPackages", []):
        root = str(package["root"]).rstrip("/")
        routes.extend("/%s/%s" % (root, route) for route in package.get("pages", []))
    return tuple(routes)


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render_description(case: dict) -> str:
    account_profile = case["accountProfile"]
    lines = [
        "测试目标：%s" % case["objective"],
        "账号前置：%s" % ACCOUNT_PREAMBLES[account_profile],
        (
            '从 minium_func_call("ai_cloud_support.open_page", "%s") 打开本用例起点；'
            "如果返回的页面不是预期页面，直接判定失败。" % case["startRoute"]
        ),
    ]
    for index, step in enumerate(case["steps"], start=1):
        lines.append("%s. %s" % (index, step))
    lines.append("最后执行以下大模型断言；任意一条不成立都结束本用例并返回失败：")
    for assertion in case["assertions"]:
        lines.append('llm_assert("%s")' % assertion.replace('"', "'"))
    return "\n".join(lines)


def validate_manifest(manifest: dict) -> dict:
    cases = manifest.get("cases") or []
    plans = manifest.get("plans") or []
    if not cases or not plans:
        raise SystemExit("AI 全量清单必须同时包含 cases 和 plans。")

    case_ids = [case.get("id") for case in cases]
    case_names = [case.get("name") for case in cases]
    duplicate_ids = sorted(key for key, count in Counter(case_ids).items() if count > 1)
    duplicate_names = sorted(key for key, count in Counter(case_names).items() if count > 1)
    if duplicate_ids or duplicate_names:
        raise SystemExit("AI 用例存在重复：ids=%s names=%s" % (duplicate_ids, duplicate_names))

    case_by_id = {case["id"]: case for case in cases}
    for case in cases:
        required = {
            "id",
            "name",
            "priority",
            "accountProfile",
            "objective",
            "startRoute",
            "coverageRoutes",
            "steps",
            "assertions",
        }
        missing = sorted(required - set(case))
        if missing:
            raise SystemExit("AI 用例 %s 缺少字段：%s" % (case.get("id"), missing))
        if case["priority"] not in ALLOWED_PRIORITIES:
            raise SystemExit("AI 用例 %s 优先级非法" % case["id"])
        if case["accountProfile"] not in ACCOUNT_PREAMBLES:
            raise SystemExit("AI 用例 %s 账号类型非法" % case["id"])
        if not case["steps"] or not case["assertions"]:
            raise SystemExit("AI 用例 %s 没有步骤或断言" % case["id"])

    assigned_case_ids = []
    for plan in plans:
        if plan.get("priority") not in ALLOWED_PRIORITIES:
            raise SystemExit("AI 计划 %s 优先级非法" % plan.get("id"))
        references = plan.get("caseIds") or []
        unknown = sorted(set(references) - set(case_by_id))
        if unknown:
            raise SystemExit("AI 计划 %s 引用了未知用例：%s" % (plan.get("id"), unknown))
        wrong_profile = [
            case_id
            for case_id in references
            if case_by_id[case_id]["accountProfile"] != plan.get("accountProfile")
        ]
        if wrong_profile:
            raise SystemExit("AI 计划 %s 混入其他账号用例：%s" % (plan.get("id"), wrong_profile))
        assigned_case_ids.extend(references)

    assignment_counts = Counter(assigned_case_ids)
    missing_assignment = sorted(set(case_by_id) - set(assigned_case_ids))
    repeated_assignment = sorted(key for key, count in assignment_counts.items() if count > 1)
    if missing_assignment or repeated_assignment:
        raise SystemExit(
            "AI 用例计划归属不完整：missing=%s repeated=%s"
            % (missing_assignment, repeated_assignment)
        )

    registered = app_routes()
    declared = {
        route for case in cases for route in case.get("coverageRoutes", [])
    }
    missing_routes = sorted(set(registered) - declared)
    unknown_routes = sorted(declared - set(registered))
    if missing_routes or unknown_routes:
        raise SystemExit(
            "AI 用例路由覆盖与 app.json 不一致：missing=%s unknown=%s"
            % (missing_routes, unknown_routes)
        )

    return {
        "caseCount": len(cases),
        "planCount": len(plans),
        "routeCount": len(registered),
        "manualPlatformCount": len(manifest.get("manualPlatformCases") or []),
    }


class CloudClient:
    def __init__(self, token: str, group_id: str):
        self.token = token
        self.group_id = group_id

    def request(self, method: str, path: str, values: dict) -> dict:
        payload = {"token": self.token, "group_en_id": self.group_id, **values}
        url = API_ORIGIN + path
        try:
            response = requests.request(
                method,
                url,
                params=payload if method == "GET" else None,
                data=payload if method != "GET" else None,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
        except requests.HTTPError as error:
            response_text = error.response.text.strip() if error.response is not None else ""
            try:
                response_body = json.loads(response_text)
                detail = str(response_body.get("msg") or response_body.get("message") or "")
            except json.JSONDecodeError:
                detail = response_text[:300]
            raise CloudApiError(
                "微信云测 API 请求失败：%s %s，HTTP %s%s"
                % (
                    method,
                    path,
                    error.response.status_code if error.response is not None else "unknown",
                    "：%s" % detail if detail else "",
                )
            ) from error
        except requests.RequestException as error:
            raise CloudApiError(
                "微信云测 API 连接失败：%s %s：%s" % (method, path, error.__class__.__name__)
            ) from error
        if result.get("rtn") != 0:
            raise CloudApiError(
                "微信云测 API 拒绝请求：%s %s：%s"
                % (method, path, result.get("msg") or result)
            )
        return result


def sync_cases(client: CloudClient, manifest: dict) -> dict[str, int]:
    existing_result = client.request("GET", AI_CASE_PATH, {})
    existing_by_name = {}
    for item in existing_result.get("data") or []:
        name = str(item.get("name") or "")
        if name in existing_by_name:
            raise CloudApiError("云端存在重名 AI 用例：%s" % name)
        existing_by_name[name] = item

    ids = {}
    for case in manifest["cases"]:
        description = render_description(case)
        existing = existing_by_name.get(case["name"])
        values = {
            "name": case["name"],
            "description": description,
            "priority": case["priority"],
        }
        if existing:
            case_id = int(existing["id"])
            changed = (
                str(existing.get("case_desc") or "") != description
                or str(existing.get("case_priority") or "") != case["priority"]
            )
            if changed:
                client.request("PUT", AI_CASE_PATH, {"case_id": case_id, **values})
                print("UPDATE case %s" % case["id"])
            else:
                print("KEEP   case %s" % case["id"])
        else:
            created = client.request("POST", AI_CASE_PATH, values)
            case_id = int(created["data"]["id"])
            print("CREATE case %s" % case["id"])
        ids[case["id"]] = case_id
    return ids


def sync_plans(client: CloudClient, manifest: dict, case_ids: dict[str, int]) -> None:
    existing_result = client.request(
        "GET",
        PLAN_LIST_PATH,
        {"test_type": 2, "page_index": -1, "page_size": -1},
    )
    existing_by_name = {
        str(item.get("name") or ""): item for item in existing_result.get("data") or []
    }
    for plan in manifest["plans"]:
        ordered_ids = [case_ids[case_id] for case_id in plan["caseIds"]]
        values = {
            "test_plan_name": plan["name"],
            "case_ids": json.dumps(ordered_ids, ensure_ascii=False),
            "priority": plan["priority"],
        }
        existing = existing_by_name.get(plan["name"])
        if existing:
            client.request(
                "PUT",
                AI_PLAN_PATH,
                {"test_plan_id": int(existing["id"]), **values},
            )
            print("UPDATE plan %s (%s cases)" % (plan["id"], len(ordered_ids)))
        else:
            client.request("POST", AI_PLAN_PATH, values)
            print("CREATE plan %s (%s cases)" % (plan["id"], len(ordered_ids)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="只校验本地清单（默认）")
    action.add_argument("--apply", action="store_true", help="创建或更新云端 AI 用例与计划")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = load_manifest(args.manifest.resolve())
    summary = validate_manifest(manifest)
    print(
        "AI full suite: {caseCount} cases / {planCount} plans / "
        "{routeCount} registered routes / {manualPlatformCount} manual platform cases".format(
            **summary
        )
    )
    if not args.apply:
        return

    token = os.environ.get("WECHAT_CLOUDTEST_TOKEN", "").strip()
    group_id = os.environ.get("WECHAT_CLOUDTEST_GROUP_ID", "").strip()
    if sys.stdin.isatty():
        if not token:
            token = getpass.getpass("微信云测 Token（输入不会显示）：").strip()
        if not group_id:
            group_id = input("微信云测项目英文 ID：").strip()
    if not token or not group_id:
        raise SystemExit(
            "同步云端需要微信云测 Token 和项目英文 ID；可在交互终端输入，"
            "也可设置 WECHAT_CLOUDTEST_TOKEN 与 WECHAT_CLOUDTEST_GROUP_ID。"
        )
    client = CloudClient(token, group_id)
    case_ids = sync_cases(client, manifest)
    sync_plans(client, manifest, case_ids)
    print("微信云测 AI 全量用例与三组计划已同步。")


if __name__ == "__main__":
    try:
        main()
    except CloudApiError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
