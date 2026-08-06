"""已完成新手引导账号的核心工作区真实数据就绪测试。"""

import minium

from minium_support import open_app_route, open_ready_page, wait_until


class AReadyAccountPreflightTest(minium.MiniTest):
    def test_ready_account_can_load_today_dashboard(self):
        _, data = open_ready_page(self, "/pages/today/index")
        self.assertIsInstance(data.get("view"), dict)
        self.assertTrue(data["view"].get("capacityVersionId"))


class CCoreWorkspaceReadinessTest(minium.MiniTest):
    def test_today_workspace_is_ready(self):
        page, data = open_ready_page(self, "/pages/today/index")
        self.assertTrue(data.get("view"))
        self.assertTrue(page.get_elements(".today-content"))

    def test_ability_workspace_is_ready(self):
        page, data = open_ready_page(self, "/pages/ability/index")
        self.assertTrue(data.get("profile"))
        self.assertTrue(page.get_elements(".ability-content"))

    def test_companion_workspace_has_context(self):
        page = open_app_route(self.app, "/pages/anan/index")
        self.assertEqual(page.path, "/pages/anan/index")
        data = wait_until(
            lambda: (
                lambda current: current
                if current.get("contextStatus") not in ("loading", None)
                else None
            )(page.data),
            timeout=30,
        )
        self.assertIsNotNone(data, "岸岸页没有完成上下文加载")
        self.assertNotEqual(data.get("contextStatus"), "error")
        self.assertTrue(page.get_elements(".coach-content"))

    def test_profile_workspace_is_ready(self):
        page, data = open_ready_page(self, "/pages/me/index")
        self.assertTrue(data.get("displayName"))
        self.assertTrue(page.get_elements(".profile-content"))

    def test_position_workspace_is_ready_or_empty(self):
        page, data = open_ready_page(
            self,
            "/pages/positions/index",
            allowed_states=("ready", "empty"),
        )
        workspace = data.get("workspace") or {}
        self.assertIn(workspace.get("status"), ("ready", "empty"))
        self.assertTrue(page.get_elements(".position-content"))

    def test_report_center_is_ready(self):
        page, data = open_ready_page(self, "/reports/report-center/index")
        self.assertTrue(data.get("center"))
        self.assertTrue(page.get_elements("view"))

    def test_notification_settings_are_ready(self):
        page, data = open_ready_page(self, "/account/notification-settings/index")
        self.assertTrue(data.get("draft"))
        self.assertTrue(page.get_elements("view"))

    def test_private_library_is_ready_or_empty(self):
        page, data = open_ready_page(
            self,
            "/pages/private-knowledge/index",
            allowed_states=("ready", "empty", "idle"),
        )
        self.assertTrue(page.get_elements("view"))
        self.assertNotEqual(data.get("status"), "error")
