#!/usr/bin/env python3
"""通过微信云测官方 API 准备、提交并查询岸岸全量测试。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
from time import strftime
import warnings

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL.*")
import requests


WECHAT_ROOT = Path(__file__).resolve().parents[1]
CASE_ARCHIVE = WECHAT_ROOT / "minitest" / "dist" / "anan-minium-full-suite.zip"
RUN_STATE = WECHAT_ROOT / "minitest" / "dist" / "latest-cloud-run.json"
API_ORIGIN = "https://minitest.weixin.qq.com"
FULL_PLAN_NAME = "岸岸0.1.10-Minium全量-109条"
AI_PLAN_NAMES = {
    "mature": "岸岸0.1.10-AI全量-成熟账号",
    "write": "岸岸0.1.10-AI全量-写入账号",
    "fresh": "岸岸0.1.10-AI全量-全新账号",
}

# 顺序很重要：先把全新虚拟账号建档，再跑成熟账号的读与写闭环。
PACKAGE_ORDER = (
    "test_fresh_account_onboarding",
    "test_core_readiness",
    "test_readonly_journeys",
    "test_full_route_smoke",
    "test_responsive_layout",
    "test_today_custom",
    "test_stateful_journeys",
)
EXPECTED_CASES = 109
REGRESSION_BOOTSTRAP = {
    "test_fresh_account_onboarding": [
        "test_fresh_account_completes_login_and_onboarding",
    ],
    "test_core_readiness": [
        "test_ready_account_can_load_today_dashboard",
    ],
}

# 55 + 55 + 50 + 30 + 20 = 210 分钟。同一虚拟账号在云测中会自动排队。
RUN_MATRIX = (
    {
        "key": "minium-android",
        "plan": "full",
        "platform": "android",
        "account": "vaccount18",
        "seconds": 55 * 60,
    },
    {
        "key": "minium-ios",
        "plan": "full",
        "platform": "ios",
        "account": "vaccount19",
        "seconds": 55 * 60,
    },
    {
        "key": "ai-mature",
        "plan": "mature",
        "platform": "android",
        "account": "vaccount18",
        "seconds": 50 * 60,
    },
    {
        "key": "ai-write",
        "plan": "write",
        "platform": "android",
        "account": "vaccount18",
        "seconds": 30 * 60,
    },
    {
        "key": "ai-fresh",
        "plan": "fresh",
        "platform": "android",
        "account": "vaccount20",
        "seconds": 20 * 60,
    },
)
RETRY_ACCOUNTS = {
    "minium-android": "vaccount16",
    "minium-ios": "vaccount17",
    "ai-mature": "vaccount16",
    "ai-write": "vaccount16",
    "ai-fresh": "vaccount17",
    "regression-android": "vaccount18",
    "regression-ios": "vaccount19",
}
FRESH_CASE_RETRY_ACCOUNTS = (
    "vaccount15",
    "vaccount14",
    "vaccount13",
    "vaccount12",
)
MINIUM_CONFIG = {
    "assert_capture": True,
    "auto_relaunch": False,
    "auto_authorize": True,
    "audits": False,
    "teardown_snapshot": False,
    "enable_tap_capture": False,
}
STATUS_TEXT = {
    1: "排队中",
    2: "测试中",
    11: "未发现 Case",
    12: "测试结束",
    15: "任务超时",
    29: "创建失败",
}


class CloudApiError(RuntimeError):
    pass


class CloudClient:
    def __init__(self, token: str, group_id: str):
        self.token = token
        self.group_id = group_id

    def request(
        self,
        method: str,
        path: str,
        values: dict,
        *,
        json_body: bool = False,
        files: dict | None = None,
        timeout: int = 30,
    ) -> dict:
        payload = {"token": self.token, "group_en_id": self.group_id, **values}
        try:
            response = requests.request(
                method,
                API_ORIGIN + path,
                params=payload if method == "GET" else None,
                json=payload if method != "GET" and json_body else None,
                data=payload if method != "GET" and not json_body else None,
                files=files,
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except requests.HTTPError as error:
            detail = ""
            if error.response is not None:
                try:
                    body = error.response.json()
                    detail = str(body.get("msg") or body.get("message") or "")
                except requests.JSONDecodeError:
                    detail = error.response.text[:300]
            raise CloudApiError(
                "微信云测 API 请求失败：%s %s%s"
                % (method, path, "：%s" % detail if detail else "")
            ) from error
        except requests.RequestException as error:
            raise CloudApiError(
                "微信云测 API 连接失败：%s %s：%s"
                % (method, path, error.__class__.__name__)
            ) from error
        if result.get("rtn") != 0:
            raise CloudApiError(
                "微信云测 API 拒绝请求：%s %s：%s"
                % (method, path, result.get("msg") or result)
            )
        return result


def credentials() -> tuple[str, str]:
    token = os.environ.get("WECHAT_CLOUDTEST_TOKEN", "").strip()
    group_id = os.environ.get("WECHAT_CLOUDTEST_GROUP_ID", "").strip()
    if sys.stdin.isatty():
        if not token:
            token = getpass.getpass("微信云测 Token（输入不会显示）：").strip()
        if not group_id:
            group_id = input("微信云测项目英文 ID：").strip()
    if not token or not group_id:
        raise SystemExit("需要微信云测 Token 和项目英文 ID。")
    return token, group_id


def upload_cases(client: CloudClient) -> None:
    if not CASE_ARCHIVE.is_file():
        raise CloudApiError("找不到 Minium 用例包：%s" % CASE_ARCHIVE)
    with CASE_ARCHIVE.open("rb") as archive:
        client.request(
            "POST",
            "/thirdapi/case/upload",
            {"test_type": 2},
            files={"file": (CASE_ARCHIVE.name, archive, "application/zip")},
            timeout=60,
        )
    print("UPLOAD 109 条 Minium 用例包")


def minium_plan_config(client: CloudClient) -> dict:
    result = client.request("GET", "/thirdapi/minium_case_list", {})
    data = result.get("data") or {}
    packages = data.get("pkg_to_case_list") or {}
    missing = [name for name in PACKAGE_ORDER if name not in packages]
    total = sum(len(packages.get(name) or []) for name in PACKAGE_ORDER)
    if missing or total != EXPECTED_CASES:
        raise CloudApiError(
            "云端 Minium 用例不完整：missing=%s total=%s" % (missing, total)
        )
    return {
        "pkg_list": [
            {"pkg": name, "case_list": packages[name]} for name in PACKAGE_ORDER
        ],
        "exact_match": True,
    }


def sync_full_plan(client: CloudClient, config: dict) -> int:
    plans_result = client.request(
        "GET",
        "/thirdapi/case_plan_list",
        {"test_type": 2, "page_index": -1, "page_size": -1},
    )
    plans = plans_result.get("data") or []
    existing = next((item for item in plans if item.get("name") == FULL_PLAN_NAME), None)
    serialized = json.dumps(config, ensure_ascii=False)
    if existing:
        client.request(
            "PUT",
            "/thirdapi/case_plan",
            {
                "test_plan_id": int(existing["id"]),
                "test_plan_name": FULL_PLAN_NAME,
                "test_plan_config": serialized,
            },
            json_body=True,
        )
        plan_id = int(existing["id"])
        print("UPDATE Minium 全量计划 %s" % plan_id)
    else:
        created = client.request(
            "POST",
            "/thirdapi/case_plan",
            {"test_plan_name": FULL_PLAN_NAME, "test_plan_config": serialized},
            json_body=True,
        )
        plan_id = int(created["data"]["test_plan_id"])
        print("CREATE Minium 全量计划 %s" % plan_id)
    return plan_id


def all_plan_ids(client: CloudClient, full_plan_id: int) -> dict[str, int]:
    result = client.request(
        "GET",
        "/thirdapi/case_plan_list",
        {"test_type": 2, "page_index": -1, "page_size": -1},
    )
    by_name = {str(item.get("name")): int(item["id"]) for item in result.get("data") or []}
    missing = [name for name in AI_PLAN_NAMES.values() if name not in by_name]
    if missing:
        raise CloudApiError("缺少 AI 测试计划：%s" % missing)
    return {
        "full": full_plan_id,
        **{key: by_name[name] for key, name in AI_PLAN_NAMES.items()},
    }


def submit_runs(client: CloudClient, plan_ids: dict[str, int]) -> list[dict]:
    if RUN_STATE.is_file():
        state = json.loads(RUN_STATE.read_text(encoding="utf-8"))
        if state.get("submission_complete"):
            raise CloudApiError(
                "最近一轮全量任务已经全部提交；为避免重复扣时长，不会再次提交。"
            )
        run_tag = state["run_tag"]
        submitted = state.get("tasks") or []
    else:
        run_tag = "anan-full-%s" % strftime("%Y%m%d-%H%M%S")
        submitted = []
        state = {
            "run_tag": run_tag,
            "created_at": strftime("%Y-%m-%d %H:%M:%S"),
            "reserved_minutes": sum(item["seconds"] for item in RUN_MATRIX) // 60,
            "submission_complete": False,
            "tasks": submitted,
        }
        RUN_STATE.parent.mkdir(parents=True, exist_ok=True)
        RUN_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    submitted_keys = {item["key"] for item in submitted if item.get("task_id")}
    for item in RUN_MATRIX:
        if item["key"] in submitted_keys:
            print("KEEP   %-15s task=%s" % (item["key"], next(
                saved["task_id"] for saved in submitted if saved["key"] == item["key"]
            )))
            continue
        result = client.request(
            "POST",
            "/thirdapi/plan",
            {
                "test_type": 2,
                "wx_version": 2,
                "platforms": item["platform"],
                "test_plan_id": plan_ids[item["plan"]],
                "task_run_time": item["seconds"],
                "desc": "%s:%s" % (run_tag, item["key"]),
                "minium_config": MINIUM_CONFIG,
                "virtual_accounts": item["account"],
                "run_mode": 1,
            },
            json_body=True,
        )
        plan_id = int(result["data"]["plan_id"])
        record = {**item, "task_id": plan_id, "desc": "%s:%s" % (run_tag, item["key"])}
        submitted.append(record)
        state["tasks"] = submitted
        RUN_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "SUBMIT %-15s task=%s %s %s %s分钟"
            % (
                item["key"],
                plan_id,
                item["platform"],
                item["account"],
                item["seconds"] // 60,
            )
        )
    state["submission_complete"] = True
    state["tasks"] = submitted
    RUN_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return submitted


def show_status(client: CloudClient) -> None:
    if not RUN_STATE.is_file():
        raise CloudApiError("还没有本地云测运行记录。")
    state = json.loads(RUN_STATE.read_text(encoding="utf-8"))
    for task in state.get("tasks") or []:
        if not task.get("task_id"):
            print("STATUS %-15s 尚未提交" % task["key"])
            continue
        try:
            result = client.request(
                "GET",
                "/thirdapi/plan",
                {"plan_id": task["task_id"], "full_info": 1},
            )
        except CloudApiError as error:
            if "不存在或已删除" in str(error):
                print(
                    "STATUS %-18s task=%s 已被云端清理"
                    % (task["key"], task["task_id"])
                )
                continue
            raise
        data = result.get("data") or {}
        results = data.get("results") or []
        passed = sum(1 for item in results if item.get("is_passed") is True)
        failed = [item for item in results if item.get("is_passed") is False]
        print(
            "STATUS %-18s task=%s status=%s %s pass=%s fail=%s"
            % (
                task["key"],
                task["task_id"],
                data.get("status"),
                data.get("status_text")
                or STATUS_TEXT.get(data.get("status"), ""),
                passed,
                len(failed),
            )
        )
        for failure in failed[:5]:
            reason = ""
            for assertion in (failure.get("extra_info") or {}).get("assert_list") or []:
                if assertion.get("ret") is False:
                    reason = str(assertion.get("msg") or "")
                    break
            print(
                "  FAIL %s%s"
                % (
                    failure.get("case_name"),
                    " :: %s" % reason[:220].replace("\n", " ") if reason else "",
                )
            )


def retry_failed(client: CloudClient) -> None:
    if not RUN_STATE.is_file():
        raise CloudApiError("还没有本地云测运行记录。")
    state = json.loads(RUN_STATE.read_text(encoding="utf-8"))
    latest = {}
    for task in state.get("tasks") or []:
        latest[task.get("base_key") or task["key"]] = task

    failed = []
    for base_key, task in latest.items():
        result = client.request(
            "GET",
            "/thirdapi/plan",
            {"plan_id": task["task_id"], "full_info": 0},
        )
        status = int((result.get("data") or {}).get("status") or 0)
        if status > 10 and status != 12:
            failed.append((base_key, task, status))
    if not failed:
        print("没有需要重试的任务。")
        return

    plans_result = client.request(
        "GET",
        "/thirdapi/case_plan_list",
        {"test_type": 2, "page_index": -1, "page_size": -1},
    )
    by_name = {
        str(item.get("name")): int(item["id"])
        for item in plans_result.get("data") or []
    }
    plan_ids = {"full": by_name[FULL_PLAN_NAME]}
    plan_ids.update({key: by_name[name] for key, name in AI_PLAN_NAMES.items()})
    for platform in ("android", "ios"):
        key = "regression-%s" % platform
        name = "岸岸0.1.10-Minium失败回归-%s" % platform.upper()
        if name in by_name:
            plan_ids[key] = by_name[name]

    for base_key, previous, status in failed:
        attempt = int(previous.get("attempt") or 1) + 1
        account = RETRY_ACCOUNTS[base_key]
        desc = "%s:%s-retry%s" % (state["run_tag"], base_key, attempt - 1)
        result = client.request(
            "POST",
            "/thirdapi/plan",
            {
                "test_type": 2,
                "wx_version": 2,
                "platforms": previous["platform"],
                "test_plan_id": plan_ids[previous["plan"]],
                "task_run_time": previous["seconds"],
                "desc": desc,
                "minium_config": MINIUM_CONFIG,
                "virtual_accounts": account,
                "run_mode": 1,
            },
            json_body=True,
        )
        task_id = int(result["data"]["plan_id"])
        record = {
            **previous,
            "key": "%s-retry%s" % (base_key, attempt - 1),
            "base_key": base_key,
            "attempt": attempt,
            "account": account,
            "task_id": task_id,
            "retry_of": previous["task_id"],
            "desc": desc,
        }
        state["tasks"].append(record)
        RUN_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "RETRY  %-18s old=%s status=%s new=%s account=%s"
            % (base_key, previous["task_id"], status, task_id, account)
        )


def rerun_failed_fresh_ai(client: CloudClient) -> None:
    """换一个未用过的虚拟账号，只重跑已结束但用例失败的新手 AI 任务。"""

    if not RUN_STATE.is_file():
        raise CloudApiError("还没有本地云测运行记录。")
    state = json.loads(RUN_STATE.read_text(encoding="utf-8"))
    attempts = [
        task
        for task in state.get("tasks") or []
        if (task.get("base_key") or task.get("key")) == "ai-fresh"
    ]
    if not attempts:
        raise CloudApiError("没有找到全新账号 AI 任务。")
    previous = attempts[-1]
    report = client.request(
        "GET",
        "/thirdapi/plan",
        {"plan_id": previous["task_id"], "full_info": 1},
    )
    data = report.get("data") or {}
    status = int(data.get("status") or 0)
    failed = [
        item for item in data.get("results") or [] if item.get("is_passed") is False
    ]
    if status != 12 or not failed:
        print("全新账号 AI 任务尚未结束失败，不需要重跑。")
        return

    used_accounts = {str(task.get("account") or "") for task in state.get("tasks") or []}
    account = next(
        (item for item in FRESH_CASE_RETRY_ACCOUNTS if item not in used_accounts),
        None,
    )
    if not account:
        raise CloudApiError("没有剩余的未使用全新虚拟账号，停止自动重跑。")

    plans_result = client.request(
        "GET",
        "/thirdapi/case_plan_list",
        {"test_type": 2, "page_index": -1, "page_size": -1},
    )
    by_name = {
        str(item.get("name")): int(item["id"])
        for item in plans_result.get("data") or []
    }
    plan_id = by_name.get(AI_PLAN_NAMES["fresh"])
    if not plan_id:
        raise CloudApiError("缺少全新账号 AI 测试计划。")

    attempt = max(int(item.get("attempt") or 1) for item in attempts) + 1
    desc = "%s:ai-fresh-retry%s" % (state["run_tag"], attempt - 1)
    submitted = client.request(
        "POST",
        "/thirdapi/plan",
        {
            "test_type": 2,
            "wx_version": 2,
            "platforms": previous["platform"],
            "test_plan_id": plan_id,
            "task_run_time": previous["seconds"],
            "desc": desc,
            "minium_config": MINIUM_CONFIG,
            "virtual_accounts": account,
            "run_mode": 1,
        },
        json_body=True,
    )
    task_id = int(submitted["data"]["plan_id"])
    record = {
        **previous,
        "key": "ai-fresh-retry%s" % (attempt - 1),
        "base_key": "ai-fresh",
        "attempt": attempt,
        "account": account,
        "task_id": task_id,
        "retry_of": previous["task_id"],
        "desc": desc,
    }
    state["tasks"].append(record)
    RUN_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "RERUN  ai-fresh old=%s new=%s account=%s"
        % (previous["task_id"], task_id, account)
    )


def failed_case_config(client: CloudClient, task_id: int) -> tuple[dict, int]:
    result = client.request(
        "GET",
        "/thirdapi/plan",
        {"plan_id": task_id, "full_info": 1},
    )
    grouped = {}
    for item in (result.get("data") or {}).get("results") or []:
        if item.get("is_passed") is not False:
            continue
        match = re.match(r"^\(([^.]+)\.[^)]+\)(.+)$", str(item.get("case_name") or ""))
        if not match:
            raise CloudApiError("无法解析失败用例名：%s" % item.get("case_name"))
        grouped.setdefault(match.group(1), []).append(match.group(2))
    failed_count = sum(len(methods) for methods in grouped.values())
    if not failed_count:
        return {"pkg_list": [], "exact_match": True}, 0
    for package, methods in REGRESSION_BOOTSTRAP.items():
        selected = grouped.setdefault(package, [])
        grouped[package] = [method for method in methods if method not in selected] + selected
    count = sum(len(methods) for methods in grouped.values())
    package_names = [name for name in PACKAGE_ORDER if name in grouped]
    unknown = sorted(set(grouped) - set(package_names))
    if unknown:
        raise CloudApiError("失败用例来自未知包：%s" % unknown)
    return {
        "pkg_list": [
            {"pkg": name, "case_list": grouped[name]} for name in package_names
        ],
        "exact_match": True,
    }, count


def sync_named_minium_plan(client: CloudClient, name: str, config: dict) -> int:
    result = client.request(
        "GET",
        "/thirdapi/case_plan_list",
        {"test_type": 2, "page_index": -1, "page_size": -1},
    )
    existing = next(
        (item for item in result.get("data") or [] if item.get("name") == name),
        None,
    )
    serialized = json.dumps(config, ensure_ascii=False)
    if existing:
        client.request(
            "PUT",
            "/thirdapi/case_plan",
            {
                "test_plan_id": int(existing["id"]),
                "test_plan_name": name,
                "test_plan_config": serialized,
            },
            json_body=True,
        )
        return int(existing["id"])
    created = client.request(
        "POST",
        "/thirdapi/case_plan",
        {"test_plan_name": name, "test_plan_config": serialized},
        json_body=True,
    )
    return int(created["data"]["test_plan_id"])


def rerun_failed_cases(client: CloudClient) -> None:
    if not RUN_STATE.is_file():
        raise CloudApiError("还没有本地云测运行记录。")
    state = json.loads(RUN_STATE.read_text(encoding="utf-8"))
    by_key = {task["key"]: task for task in state.get("tasks") or []}
    upload_cases(client)
    reruns = (
        ("android", by_key["minium-android"], 30 * 60),
        ("ios", by_key["minium-ios"], 20 * 60),
    )
    for platform, original, seconds in reruns:
        base_key = "regression-%s" % platform
        config, count = failed_case_config(client, original["task_id"])
        if not count:
            print("SKIP   %s 没有失败用例" % base_key)
            continue
        plan_name = "岸岸0.1.10-Minium失败回归-%s" % platform.upper()
        plan_id = sync_named_minium_plan(client, plan_name, config)
        attempts = [
            task
            for task in state.get("tasks") or []
            if task.get("base_key") == base_key or task.get("key") == base_key
        ]
        previous = attempts[-1] if attempts else None
        if previous:
            report = client.request(
                "GET",
                "/thirdapi/plan",
                {"plan_id": previous["task_id"], "full_info": 1},
            )
            data = report.get("data") or {}
            status = int(data.get("status") or 0)
            failed = [
                item
                for item in data.get("results") or []
                if item.get("is_passed") is False
            ]
            if status in (1, 2):
                print(
                    "UPDATE %-18s 已补账号启动用例，task=%s %s"
                    % (base_key, previous["task_id"], STATUS_TEXT[status])
                )
                continue
            if status == 12 and not failed:
                print("SKIP   %s 已全部通过" % base_key)
                continue
            if previous.get("bootstrap_revision") == 1:
                print("KEEP   %s 带账号启动的回归已执行" % base_key)
                continue

        desc = "%s:%s" % (state["run_tag"], base_key)
        attempt = 1
        if previous:
            attempt = int(previous.get("attempt") or 1) + 1
            desc = "%s:%s-retry%s" % (state["run_tag"], base_key, attempt - 1)
        account = RETRY_ACCOUNTS[base_key]
        submitted = client.request(
            "POST",
            "/thirdapi/plan",
            {
                "test_type": 2,
                "wx_version": 2,
                "platforms": platform,
                "test_plan_id": plan_id,
                "task_run_time": seconds,
                "desc": desc,
                "minium_config": MINIUM_CONFIG,
                "virtual_accounts": account,
                "run_mode": 1,
            },
            json_body=True,
        )
        task_id = int(submitted["data"]["plan_id"])
        record = {
            "key": base_key if not previous else "%s-retry%s" % (base_key, attempt - 1),
            "base_key": base_key,
            "plan": base_key,
            "platform": platform,
            "account": account,
            "seconds": seconds,
            "task_id": task_id,
            "source_task": original["task_id"],
            "case_count": count,
            "attempt": attempt,
            "bootstrap_revision": 1,
            "retry_of": previous["task_id"] if previous else None,
            "desc": desc,
        }
        state["tasks"].append(record)
        RUN_STATE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "REGRESS %-18s task=%s cases=%s account=%s %s分钟"
            % (base_key, task_id, count, account, seconds // 60)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--start", action="store_true", help="上传用例、同步计划并提交 210 分钟全量任务")
    action.add_argument("--status", action="store_true", help="查询最近一轮全量任务状态")
    action.add_argument("--retry-failed", action="store_true", help="只重试已经失败的任务")
    action.add_argument(
        "--rerun-failed-ai-fresh",
        action="store_true",
        help="换全新虚拟账号，重跑已结束但用例失败的新手 AI 任务",
    )
    action.add_argument(
        "--rerun-failed-cases",
        action="store_true",
        help="上传修正后的用例包，只回归 Android/iOS 失败用例",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token, group_id = credentials()
    client = CloudClient(token, group_id)
    if args.status:
        show_status(client)
        return
    if args.retry_failed:
        retry_failed(client)
        return
    if args.rerun_failed_ai_fresh:
        rerun_failed_fresh_ai(client)
        return
    if args.rerun_failed_cases:
        rerun_failed_cases(client)
        return
    upload_cases(client)
    config = minium_plan_config(client)
    full_plan_id = sync_full_plan(client, config)
    plan_ids = all_plan_ids(client, full_plan_id)
    submit_runs(client, plan_ids)
    print("全量云测已提交：5 个任务，最长预留 210 分钟。")


if __name__ == "__main__":
    try:
        main()
    except CloudApiError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
