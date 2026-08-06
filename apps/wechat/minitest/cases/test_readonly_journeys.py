"""成熟测试账号的主要只读用户旅程。"""

import minium

from minium_support import (
    TAB_ROUTES,
    open_ready_page,
    tap_and_expect_route,
    wait_for_path,
)


class DMainTabJourneyTest(minium.MiniTest):
    def _assert_tab(self, route, selector):
        page = self.app.switch_tab(route, is_click=True)
        self.assertEqual(page.path, route)
        self.assertTrue(page.get_elements(selector, max_timeout=15))

    def test_today_tab(self):
        self._assert_tab(TAB_ROUTES[0], ".today-content")

    def test_ability_tab(self):
        self._assert_tab(TAB_ROUTES[1], ".ability-content")

    def test_companion_tab(self):
        self._assert_tab(TAB_ROUTES[2], ".coach-content")

    def test_profile_tab(self):
        self._assert_tab(TAB_ROUTES[3], ".profile-content")


class ETodayNavigationJourneyTest(minium.MiniTest):
    def test_today_opens_plan_basis(self):
        page, _ = open_ready_page(self, "/pages/today/index")
        tap_and_expect_route(self, page, ".basis-button", {"/plans/plan-basis/index"})

    def test_today_opens_day_adjustment(self):
        page, _ = open_ready_page(self, "/pages/today/index")
        tap_and_expect_route(self, page, ".adjust-button", {"/plans/day-adjustment/index"})

    def test_today_opens_daily_settlement(self):
        page, _ = open_ready_page(self, "/pages/today/index")
        tap_and_expect_route(
            self,
            page,
            ".settlement-button",
            {"/plans/daily-settlement/index"},
        )

    def test_today_opens_weekly_plan(self):
        page, _ = open_ready_page(self, "/pages/today/index")
        tap_and_expect_route(self, page, ".tool-link", {"/plans/weekly-plan/index"}, index=1)

    def test_today_primary_task_opens_real_task_route(self):
        page, data = open_ready_page(self, "/pages/today/index")
        self.assertTrue(data["view"].get("activeTasks"), "测试账号今天没有可执行任务")
        tap_and_expect_route(
            self,
            page,
            ".primary-button",
            {
                "/xingce/xingce-task-intro/index",
                "/pages/practice/index",
                "/pages/mock-exam/index",
                "/pages/essay-task/index",
            },
            timeout=30,
        )


class FProfileAssetJourneyTest(minium.MiniTest):
    def test_profile_opens_notification_settings(self):
        page, _ = open_ready_page(self, "/pages/me/index")
        tap_and_expect_route(
            self,
            page,
            ".coach-setting",
            {"/account/notification-settings/index"},
        )

    def test_profile_opens_private_library(self):
        page, _ = open_ready_page(self, "/pages/me/index")
        tap_and_expect_route(
            self,
            page,
            ".asset-item",
            {"/pages/private-knowledge/index"},
            index=0,
        )

    def test_profile_opens_learning_packets(self):
        page, _ = open_ready_page(self, "/pages/me/index")
        tap_and_expect_route(
            self,
            page,
            ".asset-item",
            {"/xingce/learning-packets/index"},
            index=2,
        )

    def test_profile_opens_report_center(self):
        page, _ = open_ready_page(self, "/pages/me/index")
        tap_and_expect_route(
            self,
            page,
            ".asset-item",
            {"/reports/report-center/index"},
            index=3,
        )

    def test_profile_opens_privacy_center(self):
        page, _ = open_ready_page(self, "/pages/me/index")
        tap_and_expect_route(
            self,
            page,
            ".profile-menu-row",
            {"/account/privacy/index"},
            index=0,
        )


class GPositionAndReportJourneyTest(minium.MiniTest):
    def test_position_search_opens_real_position_detail(self):
        page, _ = open_ready_page(
            self,
            "/pages/positions/search/index",
            allowed_states=("ready", "empty"),
        )
        cards = page.get_elements(".all-position-card", max_timeout=20)
        self.assertTrue(cards, "测试账号或当前岗位目录没有可打开的岗位")
        cards[0].tap()
        detail = wait_for_path(self.app, {"/pages/positions/detail/index"}, timeout=20)
        self.assertIsNotNone(detail)

    def test_ability_opens_real_module_report(self):
        page, _ = open_ready_page(self, "/pages/ability/index")
        modules = page.get_elements(".module-row", max_timeout=15)
        enabled = [item for item in modules if not bool(item.attribute("disabled")[0])]
        self.assertTrue(enabled, "成熟测试账号还没有任何可打开的模块能力记录")
        enabled[0].tap()
        module_page = wait_for_path(self.app, {"/reports/ability-module/index"}, timeout=20)
        self.assertIsNotNone(module_page)

    def test_report_center_opens_external_score_flow(self):
        page, _ = open_ready_page(self, "/reports/report-center/index")
        tap_and_expect_route(
            self,
            page,
            ".external-card",
            {"/reports/external-report/index"},
        )

    def test_mastery_and_favorites_are_reachable(self):
        page, _ = open_ready_page(
            self,
            "/xingce/xingce-mastery/index",
            allowed_states=("ready", "empty"),
        )
        self.assertTrue(page.get_elements(".mastery-page"))
        favorites = self.app.relaunch("/xingce/xingce-mastery/favorites/index")
        self.assertEqual(favorites.path, "/xingce/xingce-mastery/favorites/index")
        self.assertTrue(favorites.get_elements("view", max_timeout=15))


class HContentAndCommunityJourneyTest(minium.MiniTest):
    def test_capture_import_switches_to_import_mode(self):
        page, _ = open_ready_page(
            self,
            "/pages/capture-import/index",
            allowed_states=("ready", "idle", "draft"),
        )
        modes = page.get_elements(".capture-mode", max_timeout=15)
        self.assertGreaterEqual(len(modes), 2)
        modes[1].tap()
        self.assertTrue(page.get_elements("view"))

    def test_learning_packets_switch_track(self):
        page, _ = open_ready_page(
            self,
            "/xingce/learning-packets/index",
            allowed_states=("ready", "empty", "idle"),
        )
        choices = page.get_elements(".choice", max_timeout=15)
        self.assertGreaterEqual(len(choices), 2)
        choices[1].tap()
        self.assertTrue(page.get_elements("view"))

    def test_community_creation_chain_renders(self):
        for route, selector in (
            ("/community/community-draft/index", ".choice-card"),
            ("/community/community-structure/index", ".rights-row"),
            ("/community/community-trial/index", ".action-button"),
            ("/community/community-contributions/index", "view"),
        ):
            page = self.app.relaunch(route)
            self.assertEqual(page.path, route)
            self.assertTrue(page.get_elements(selector, max_timeout=15), route)
