"""Minium 全量云测共用的路由、等待和账号状态工具。"""

from __future__ import annotations

import re
import time


REGISTERED_ROUTES = (
    "/pages/login/index",
    "/pages/today/index",
    "/pages/ability/index",
    "/pages/anan/index",
    "/pages/me/index",
    "/plans/daily-settlement/index",
    "/plans/retake/index",
    "/plans/plan-history/index",
    "/plans/study-preference/index",
    "/plans/plan-basis/index",
    "/plans/weekly-plan/index",
    "/plans/day-adjustment/index",
    "/account/membership/index",
    "/account/privacy/index",
    "/account/terms/index",
    "/account/notification-settings/index",
    "/account/support/index",
    "/xingce/xingce-task-intro/index",
    "/xingce/xingce-mastery/index",
    "/xingce/xingce-mastery/favorites/index",
    "/xingce/learning-packets/index",
    "/xingce/answer-ingestion/index",
    "/essay/essay-material/index",
    "/essay/essay-compose/index",
    "/essay/essay-review/index",
    "/essay/essay-rewrite/index",
    "/essay/essay-evidence/index",
    "/community/community-draft/index",
    "/community/community-structure/index",
    "/community/community-trial/index",
    "/community/community-contributions/index",
    "/reports/mock-report/index",
    "/reports/report-center/index",
    "/reports/external-report/index",
    "/reports/report-overview/index",
    "/reports/global-report/index",
    "/reports/ability-module/index",
    "/reports/error-pattern/index",
    "/pages/capture-import/index",
    "/pages/private-knowledge/index",
    "/pages/private-knowledge/detail/index",
    "/pages/onboarding/index",
    "/pages/onboarding/profile/index",
    "/pages/onboarding/entry/index",
    "/pages/onboarding/preparation-experience/index",
    "/pages/onboarding/position-preference/index",
    "/pages/onboarding/study-preference/index",
    "/pages/onboarding/ability-baseline/index",
    "/pages/onboarding/result/index",
    "/pages/positions/index",
    "/pages/positions/search/index",
    "/pages/positions/detail/index",
    "/pages/positions/compare/index",
    "/pages/positions/goal/index",
    "/pages/positions/briefing/index",
    "/pages/profile/archive/index",
    "/pages/practice/index",
    "/pages/mock-exam/index",
    "/pages/reflection/index",
    "/pages/essay-task/index",
)

REGISTERED_ROUTE_SET = frozenset(REGISTERED_ROUTES)
TAB_ROUTES = (
    "/pages/today/index",
    "/pages/ability/index",
    "/pages/anan/index",
    "/pages/me/index",
)
TASK_ROUTES = frozenset(
    {
        "/xingce/xingce-task-intro/index",
        "/pages/practice/index",
        "/pages/mock-exam/index",
        "/pages/essay-task/index",
        "/pages/reflection/index",
    }
)
LOADING_STATES = frozenset({"loading", "pending", "saving", "submitting", "processing"})
ERROR_STATES = frozenset({"error", "failed", "unauthenticated", "unavailable"})


def route_test_name(route: str) -> str:
    """生成云测报告中稳定、可读的测试方法名。"""

    slug = re.sub(r"[^a-z0-9]+", "_", route.lower()).strip("_")
    return "test_route_%s" % slug


def semantic_state(data):
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("status"), str):
        return data["status"]
    workspace = data.get("workspace")
    if isinstance(workspace, dict) and isinstance(workspace.get("status"), str):
        return workspace["status"]
    state = data.get("state")
    if isinstance(state, str):
        return state
    if isinstance(state, dict) and isinstance(state.get("status"), str):
        return state["status"]
    if isinstance(data.get("loading"), bool):
        return "loading" if data["loading"] else "ready"
    return None


def wait_until(predicate, timeout=20, interval=0.2):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as error:  # 页面切换期间旧 Page 对象可能短暂失效。
            last_error = error
        time.sleep(interval)
    if last_error:
        raise AssertionError("等待页面状态时最后一次异常：%s" % last_error)
    return None


def current_page(app):
    return app.get_current_page()


def wait_for_path(app, expected_paths, timeout=15):
    expected = set(expected_paths)
    return wait_until(
        lambda: (lambda page: page if page.path in expected else None)(current_page(app)),
        timeout=timeout,
    )


def wait_for_path_change(app, previous_path, timeout=15):
    return wait_until(
        lambda: (lambda page: page if page.path != previous_path else None)(current_page(app)),
        timeout=timeout,
    )


def wait_for_data(page, predicate, timeout=20):
    return wait_until(lambda: (lambda data: data if predicate(data) else None)(page.data), timeout)


def open_app_route(app, url, timeout=15):
    """Avoid the Android cloud-device reLaunch/startup race when opening a route."""

    target = str(url).split("?", 1)[0]
    for _ in range(3):
        if target in TAB_ROUTES:
            page = app.switch_tab(target, is_click=False)
        else:
            root = app.switch_tab(TAB_ROUTES[0], is_click=False)
            if root.path != TAB_ROUTES[0]:
                root = app.relaunch(TAB_ROUTES[0])
            wait_until(lambda: current_page(app).path == TAB_ROUTES[0], timeout=5)
            page = app.navigate_to(url)
        if page.path == target:
            return page
        settled = wait_until(
            lambda: (
                lambda candidate: candidate if candidate.path == target else None
            )(current_page(app)),
            timeout=min(5, timeout),
        )
        if settled:
            return settled
        time.sleep(0.5)
    return current_page(app)


def wait_for_settled_page(app, requested_route, timeout=20):
    """等待目标页稳定、呈现 DOM，或安全跳转到另一条已注册路由。"""

    def inspect():
        page = current_page(app)
        if page.path != requested_route:
            return {"page": page, "outcome": "redirect", "state": None}
        data = page.data
        state = semantic_state(data)
        if state and state not in LOADING_STATES:
            return {"page": page, "outcome": "state", "state": state}
        if state is None and page.get_elements("view"):
            return {"page": page, "outcome": "rendered", "state": None}
        return None

    return wait_until(inspect, timeout=timeout)


def open_ready_page(test, route, allowed_states=("ready", "empty", "idle", "draft"), timeout=30):
    page = open_app_route(test.app, route)
    test.assertEqual(page.path, route, "没有打开目标页面：%s" % route)

    def inspect():
        current = current_page(test.app)
        if current.path != route:
            return {"kind": "redirect", "page": current, "data": None}
        data = current.data
        state = semantic_state(data)
        if state in allowed_states:
            return {"kind": "ready", "page": current, "data": data}
        if state in ERROR_STATES:
            return {"kind": "error", "page": current, "data": data}
        if state is None and current.get_elements("view"):
            return {"kind": "rendered", "page": current, "data": data}
        return None

    result = wait_until(inspect, timeout=timeout)
    test.assertIsNotNone(result, "%s 在 %s 秒内没有稳定" % (route, timeout))
    test.assertEqual(
        result["kind"],
        "ready",
        "%s 没有进入就绪态：%s" % (route, result),
    )
    return result["page"], result["data"]


def open_rendered_page(test, route, timeout=20):
    page = open_app_route(test.app, route)
    result = wait_for_settled_page(test.app, route, timeout=timeout)
    test.assertIsNotNone(result, "%s 一直加载或没有渲染 DOM" % route)
    final_page = result["page"]
    test.assertIn(final_page.path, REGISTERED_ROUTE_SET, "页面跳到了未注册路由")
    if route != "/pages/login/index":
        test.assertNotEqual(
            final_page.path,
            "/pages/login/index",
            "%s 被登录态拦截；当前计划没有使用已完成登录的账号。" % route,
        )
    return result


def tap_and_expect_route(test, page, selector, expected_paths, index=0, timeout=15):
    elements = page.get_elements(selector, max_timeout=10)
    test.assertGreater(len(elements), index, "没有找到交互控件：%s[%s]" % (selector, index))
    previous_path = page.path
    elements[index].tap()
    expected = set(expected_paths)
    next_page = wait_until(
        lambda: (
            lambda candidate: candidate
            if candidate.path != previous_path and candidate.path in expected
            else None
        )(current_page(test.app)),
        timeout=timeout,
    )
    test.assertIsNotNone(
        next_page,
        "点击 %s 后没有进入预期页面：%s" % (selector, sorted(expected)),
    )
    return next_page


def find_task_navigation(view, accepted_paths):
    accepted = set(accepted_paths)
    for task in view.get("activeTasks") or []:
        url = str(task.get("navigationUrl") or "")
        path = url.split("?", 1)[0]
        if task.get("canOpen") and path in accepted:
            return url, task
    return None, None


def assert_disposable_account(test):
    """写入型用例只允许在名字明确带“测试/Test/E2E”的账号上运行。"""

    _, data = open_ready_page(test, "/pages/me/index")
    display_name = str(data.get("displayName") or "")
    normalized = display_name.lower()
    test.assertTrue(
        "测试" in display_name or "test" in normalized or "e2e" in normalized,
        "写入型用例拒绝修改普通账号；请使用昵称含“测试”、Test 或 E2E 的专用账号。",
    )
    return display_name
