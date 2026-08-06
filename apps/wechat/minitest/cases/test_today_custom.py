"""微信云测 Minium 自定义测试：今日页布局、任务队列和分包路由。"""

import minium

from minium_support import open_app_route


TODAY_PATH = "/pages/today/index"


class TodayLayoutCustomTest(minium.MiniTest):
    """覆盖本轮主包瘦身后最容易回归的今日页行为。"""

    def _open_ready_today(self):
        page = open_app_route(self.app, TODAY_PATH)
        self.assertEqual(
            page.path,
            TODAY_PATH,
            "今日页被重定向；请给云测计划配置已登录且已完成引导的测试账号。",
        )
        loaded = page.wait_for(
            lambda: page.data.get("status") in ("ready", "error"),
            max_timeout=30,
        )
        self.assertTrue(loaded, "今日计划 30 秒内没有完成加载。")
        data = page.data
        self.assertEqual(
            data.get("status"),
            "ready",
            "今日计划加载失败：%s" % (data.get("errorMessage") or "未知错误"),
        )
        self.assertIsNotNone(data.get("view"), "今日计划缺少 view 数据。")
        return page, data["view"]

    def test_today_task_queues_are_grouped_and_spaced(self):
        page, view = self._open_ready_today()
        active_tasks = view.get("activeTasks") or []
        queue_views = view.get("secondaryTasks") or []
        task_cards = page.get_elements(".compact-task")

        self.assertEqual(
            len(task_cards),
            len(queue_views),
            "次要任务卡片数量应与已分组的任务队列一致。",
        )
        self.assertLessEqual(
            len(task_cards),
            max(0, len(active_tasks) - 1),
            "次要任务没有按队列收拢，页面可能重新出现大量重复卡片。",
        )

        queue_titles = [str(item.get("title") or "") for item in queue_views]
        self.assertEqual(
            len(queue_titles),
            len(set(queue_titles)),
            "同名训练仍被渲染成多张卡片，应先合并为一个题组队列。",
        )

        if task_cards:
            primary = page.get_element(".primary-task", max_timeout=10)
            first_card = task_cards[0]
            primary_bottom = primary.rect["top"] + primary.rect["height"]
            self.assertGreaterEqual(
                first_card.rect["top"] - primary_bottom,
                8,
                "“开始第一项任务”和下一张任务卡之间缺少呼吸空间。",
            )

        for previous, current in zip(task_cards, task_cards[1:]):
            previous_bottom = previous.rect["top"] + previous.rect["height"]
            self.assertGreaterEqual(
                current.rect["top"] - previous_bottom,
                8,
                "相邻任务卡片之间缺少呼吸空间。",
            )

        action_buttons = page.get_elements(".compact-task__action")
        self.assertEqual(len(action_buttons), len(task_cards))
        for button in action_buttons:
            self.assertIn(button.inner_text, ("开始", "继续"))
            self.assertGreater(
                button.rect["width"],
                button.rect["height"] * 1.2,
                "任务卡“开始/继续”按钮被挤成竖排。",
            )

    def test_mock_and_settlement_have_breathing_room(self):
        page, _ = self._open_ready_today()
        mock_entry = page.get_element(".mock-entry", max_timeout=10)
        settlement = page.get_element(".settlement-button", max_timeout=10)
        mock_bottom = mock_entry.rect["top"] + mock_entry.rect["height"]

        self.assertGreaterEqual(
            settlement.rect["top"] - mock_bottom,
            8,
            "“完整模考”和“记录今天”之间缺少呼吸空间。",
        )

    def test_today_layout_matches_current_device_orientation(self):
        page, _ = self._open_ready_today()
        viewport = page.inner_size
        main = page.get_element(".today-col--main", max_timeout=10).rect
        side = page.get_element(".today-col--side-b", max_timeout=10).rect
        is_landscape_tablet = viewport["width"] >= 1024 and viewport["width"] > viewport["height"]

        if is_landscape_tablet:
            self.assertGreaterEqual(
                side["left"] - (main["left"] + main["width"]),
                16,
                "横屏平板的主任务区与右侧工具区间距不足。",
            )
            self.assertLessEqual(
                side["left"] + side["width"],
                viewport["width"] + 1,
                "横屏平板右侧工具区溢出屏幕。",
            )
        else:
            self.assertLessEqual(
                abs(main["left"] - side["left"]),
                4,
                "手机或竖屏平板不应被横屏双栏规则影响。",
            )
            self.assertLessEqual(
                abs(main["width"] - side["width"]),
                4,
                "手机或竖屏平板应保持同宽单列布局。",
            )


class SplitPackageRouteCustomTest(minium.MiniTest):
    """确认主包瘦身后关键分包仍可按真实路由打开。"""

    def _assert_route_opens(self, route):
        page = open_app_route(self.app, route)
        self.assertEqual(page.path, route, "分包页面未能打开：%s" % route)
        page.wait_for(2)
        current = self.app.get_current_page()
        self.assertEqual(
            current.path,
            route,
            "分包页面随后发生了异常重定向；请确认云测账号已登录并完成引导。",
        )

    def test_day_adjustment_route_opens(self):
        self._assert_route_opens("/plans/day-adjustment/index")

    def test_xingce_mastery_route_opens(self):
        self._assert_route_opens("/xingce/xingce-mastery/index")

    def test_notification_settings_route_opens(self):
        self._assert_route_opens("/account/notification-settings/index")
