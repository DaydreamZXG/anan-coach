"""仅供全新虚拟账号执行的一次性登录与新手引导全链路。"""

import minium

from minium_support import current_page, open_ready_page, wait_for_path_change, wait_until


class YFreshAccountOnboardingJourneyTest(minium.MiniTest):
    def _tap_and_wait(self, page, selector, index=0, timeout=30):
        elements = page.get_elements(selector, max_timeout=20)
        self.assertGreater(len(elements), index, "缺少新手引导控件：%s" % selector)
        previous = page.path
        elements[index].tap()
        next_page = wait_for_path_change(self.app, previous, timeout=timeout)
        self.assertIsNotNone(next_page, "%s 没有进入下一步" % previous)
        return next_page

    def test_fresh_account_completes_login_and_onboarding(self):
        login = self.app.relaunch("/pages/login/index")
        self.assertEqual(
            login.path,
            "/pages/login/index",
            "该用例需要从未完成新手引导的全新虚拟账号。",
        )
        login.call_method("toggleLegalConsent", {"detail": {"value": ["accepted"]}})
        self.assertTrue(wait_until(lambda: login.data.get("legalAccepted"), timeout=5))
        page = self._tap_and_wait(login, ".wechat-button", timeout=45)

        visited = set()
        for _ in range(12):
            route = page.path
            self.assertNotIn(route, visited, "新手引导在页面之间循环：%s" % route)
            visited.add(route)
            if route == "/pages/today/index":
                break
            if route == "/pages/onboarding/entry/index":
                page = self._tap_and_wait(page, ".entry-action--primary")
                continue
            if route == "/pages/onboarding/preparation-experience/index":
                self.assertTrue(page.get_elements(".exam-card", max_timeout=20))
                page.get_elements(".exam-card")[0].tap()
                page.get_elements(".status-card")[1].tap()
                page = self._tap_and_wait(page, ".identity-primary")
                continue
            if route in ("/pages/onboarding/index", "/pages/onboarding/profile/index"):
                page.call_method(
                    "inputMajor",
                    {"detail": {"value": "计算机科学与技术"}},
                )
                page.call_method("chooseEducation", {"detail": {"value": "1"}})
                page.call_method("chooseDegree", {"detail": {"value": "1"}})
                page.call_method("chooseGraduation", {"detail": {"value": "0"}})
                page.call_method(
                    "chooseGender",
                    {"currentTarget": {"dataset": {"value": "男"}}},
                )
                page.call_method(
                    "choosePoliticalStatus",
                    {"currentTarget": {"dataset": {"value": "群众"}}},
                )
                page.call_method(
                    "toggleCertification",
                    {"currentTarget": {"dataset": {"value": "无"}}},
                )
                page.call_method(
                    "chooseServiceMode",
                    {"currentTarget": {"dataset": {"mode": "none"}}},
                )
                page.call_method(
                    "chooseWorkYears",
                    {"currentTarget": {"dataset": {"value": "0"}}},
                )
                self.assertTrue(
                    wait_until(lambda: page.data.get("primaryDisabled") is False, timeout=10),
                    "资格表单没有形成可提交状态",
                )
                page = self._tap_and_wait(page, ".eligibility-primary", timeout=45)
                continue
            if route == "/pages/onboarding/position-preference/index":
                regions = page.get_elements(".region-chip", max_timeout=30)
                self.assertTrue(regions, "地区目录没有加载出来")
                regions[0].tap()
                levels = page.get_elements(".level-row")
                if levels:
                    levels[0].tap()
                page = self._tap_and_wait(page, ".preference-primary")
                continue
            if route == "/pages/onboarding/study-preference/index":
                presets = page.get_elements(".time-preset", max_timeout=20)
                self.assertGreaterEqual(len(presets), 2)
                presets[1].tap()
                page = self._tap_and_wait(page, ".time-primary")
                continue
            if route == "/pages/onboarding/ability-baseline/index":
                page.get_elements(".baseline-card", max_timeout=20)[0].tap()
                page = self._tap_and_wait(page, ".baseline-primary")
                continue
            if route == "/pages/onboarding/result/index":
                page = self._tap_and_wait(page, ".result-primary", timeout=45)
                continue
            self.fail("新手引导进入了未覆盖页面：%s" % route)

        self.assertEqual(current_page(self.app).path, "/pages/today/index")
        _, data = open_ready_page(self, "/pages/today/index", timeout=45)
        self.assertTrue(data.get("view"))
