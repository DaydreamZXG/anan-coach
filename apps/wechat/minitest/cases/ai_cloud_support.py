"""微信 AI 自定义测试可通过 minium_func_call 调用的稳定起点与账号护栏。"""

import time


def open_page(mini, route):
    """把 AI 探索重置到已注册页面，并返回实际页面路径。"""

    page = mini.app.relaunch(route)
    mini.user_logger.info("AI journey start route: %s -> %s" % (route, page.path))
    return page.path


def open_today(mini):
    """回到今日 Tab，供连续 DDT 用例恢复稳定起点。"""

    page = mini.app.switch_tab("/pages/today/index", is_click=True)
    mini.user_logger.info("AI journey returned to today: %s" % page.path)
    return page.path


def require_disposable_account(mini):
    """阻止 AI 写入用例修改普通账号。"""

    page = mini.app.relaunch("/pages/me/index")
    deadline = time.time() + 30
    display_name = ""
    while time.time() < deadline:
        display_name = str(page.data.get("displayName") or "")
        if display_name:
            break
        time.sleep(0.2)
    normalized = display_name.lower()
    if not (
        "测试" in display_name
        or "test" in normalized
        or "e2e" in normalized
    ):
        raise AssertionError(
            "AI 写入计划拒绝修改普通账号；昵称必须包含测试、Test 或 E2E。"
        )
    mini.user_logger.info("AI disposable account verified: %s" % display_name)
    return display_name


def viewport_label(mini):
    """返回当前设备视口，方便大模型把视觉判断和设备形态对应起来。"""

    page = mini.app.get_current_page()
    size = page.inner_size
    label = "width=%s,height=%s" % (size["width"], size["height"])
    mini.user_logger.info("AI viewport: %s" % label)
    return label
