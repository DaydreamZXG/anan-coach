"""核心工作区在当前云测设备上的横向溢出与横竖屏布局检查。"""

import minium

from minium_support import open_app_route, open_ready_page, wait_until


class IResponsiveWorkspaceTest(minium.MiniTest):
    def _assert_no_horizontal_overflow(self, route, allowed_states=("ready",)):
        page, _ = open_ready_page(self, route, allowed_states=allowed_states)
        viewport = page.inner_size
        self.assertLessEqual(
            page.scroll_width,
            viewport["width"] + 1,
            "%s 在 %sx%s 视口出现横向溢出"
            % (route, viewport["width"], viewport["height"]),
        )
        return page

    def test_today_has_no_horizontal_overflow(self):
        self._assert_no_horizontal_overflow("/pages/today/index")

    def test_ability_has_no_horizontal_overflow(self):
        self._assert_no_horizontal_overflow("/pages/ability/index")

    def test_companion_has_no_horizontal_overflow(self):
        page = open_app_route(self.app, "/pages/anan/index")
        self.assertEqual(page.path, "/pages/anan/index")
        self.assertTrue(
            wait_until(
                lambda: page.data.get("contextStatus") not in (None, "loading"),
                timeout=30,
            ),
            "岸岸页上下文没有完成加载",
        )
        viewport = page.inner_size
        self.assertLessEqual(page.scroll_width, viewport["width"] + 1)

    def test_profile_has_no_horizontal_overflow(self):
        self._assert_no_horizontal_overflow("/pages/me/index")

    def test_position_search_has_no_horizontal_overflow(self):
        self._assert_no_horizontal_overflow(
            "/pages/positions/search/index",
            allowed_states=("ready", "empty"),
        )

    def test_report_center_has_no_horizontal_overflow(self):
        self._assert_no_horizontal_overflow("/reports/report-center/index")
