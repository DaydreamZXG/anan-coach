"""只允许专用可抛弃测试账号执行的真实写入与训练闭环。"""

import minium

from minium_support import (
    assert_disposable_account,
    current_page,
    find_task_navigation,
    open_ready_page,
    wait_for_data,
    wait_for_path,
    wait_until,
)


class ZDisposableAccountMutationTest(minium.MiniTest):
    """这些用例会改计划、消耗岸岸额度或产生训练记录，不能用于普通账号。"""

    def _assert_disposable(self):
        return assert_disposable_account(self)

    def test_day_adjustment_preview_and_commit(self):
        self._assert_disposable()
        page, data = open_ready_page(self, "/plans/day-adjustment/index")
        original_version = str((data.get("dashboard") or {}).get("capacity", {}).get("capacityVersionId") or "")
        original_energy = data.get("energy")
        options = page.get_elements(".energy-option", max_timeout=15)
        self.assertGreaterEqual(len(options), 3)
        option_index = 0 if original_energy != 2 else 1
        options[option_index].tap()
        self.assertTrue(
            wait_until(lambda: page.data.get("energy") != original_energy, timeout=5),
            "精力选项没有写入页面草稿",
        )
        page.get_element(".s9-primary-button", max_timeout=10).tap()
        preview = wait_for_data(
            page,
            lambda: page.data.get("step") == "preview"
            and isinstance(page.data.get("preview"), dict),
            timeout=45,
        )
        self.assertIsNotNone(preview, "真实后端没有返回调整预览")
        page.get_element(".s9-primary-button", max_timeout=10).tap()
        today = wait_for_path(self.app, {"/pages/today/index"}, timeout=45)
        self.assertIsNotNone(today, "确认调整后没有回到今日计划")
        today_data = wait_until(
            lambda: (
                lambda current: current
                if current.get("status") == "ready" and isinstance(current.get("view"), dict)
                else None
            )(today.data),
            timeout=45,
        )
        self.assertIsNotNone(today_data, "调整后的今日计划没有加载成功")
        next_version = str(today_data["view"].get("capacityVersionId") or "")
        self.assertTrue(next_version)
        if original_version:
            self.assertNotEqual(next_version, original_version, "确认调整后容量版本没有变化")

    def test_notification_preferences_save(self):
        self._assert_disposable()
        page, _ = open_ready_page(self, "/account/notification-settings/index")
        page.get_element(".save-button", max_timeout=10).tap()
        result = wait_until(
            lambda: (
                lambda current: current
                if current.get("saving") is False and str(current.get("message") or "").strip()
                else None
            )(page.data),
            timeout=45,
        )
        self.assertIsNotNone(result, "提醒设置保存没有返回结果")
        self.assertIn("已保存", result.get("message"), result.get("message"))

    def test_companion_returns_grounded_answer(self):
        self._assert_disposable()
        page = self.app.relaunch("/pages/anan/index")
        self.assertEqual(page.path, "/pages/anan/index")
        self.assertTrue(
            wait_until(lambda: page.data.get("askAvailable") is True, timeout=45),
            "专用账号当前没有岸岸提问额度或上下文",
        )
        page.call_method(
            "inputQuestion",
            {"detail": {"value": "用一句话说明我今天最该完成什么，并给出依据"}},
        )
        page.get_element(".composer-send", max_timeout=10).tap()
        result = wait_until(
            lambda: (
                lambda current: current
                if current.get("submitting") is False
                and (current.get("answer") or current.get("errorMessage"))
                else None
            )(page.data),
            timeout=90,
        )
        self.assertIsNotNone(result, "岸岸在 90 秒内没有返回回答或明确错误")
        self.assertFalse(result.get("errorMessage"), result.get("errorMessage"))
        answer = result.get("answer")
        self.assertIsInstance(answer, dict)
        self.assertTrue(str(answer.get("answer") or "").strip())
        self.assertIsInstance(answer.get("citations"), list)

    def test_personal_data_export_is_created_or_restored(self):
        self._assert_disposable()
        page, data = open_ready_page(self, "/account/privacy/index")
        if not data.get("exportRequest"):
            page.get_element(".privacy-secondary", max_timeout=10).tap()
        result = wait_until(
            lambda: (
                lambda current: current
                if current.get("exporting") is False and current.get("exportRequest")
                else None
            )(page.data),
            timeout=60,
        )
        self.assertIsNotNone(result, "个人数据导出请求没有创建或恢复")
        self.assertTrue(str(result.get("exportStateLabel") or "").strip())

    def test_xingce_submission_reaches_reflection(self):
        self._assert_disposable()
        _, today_data = open_ready_page(self, "/pages/today/index")
        navigation_url, _ = find_task_navigation(
            today_data["view"],
            {"/xingce/xingce-task-intro/index", "/pages/practice/index"},
        )
        self.assertTrue(navigation_url, "专用账号今天没有可执行的真实行测任务")

        page = self.app.relaunch(navigation_url)
        if page.path == "/xingce/xingce-task-intro/index":
            ready = wait_for_data(page, lambda: page.data.get("status") == "ready", timeout=45)
            self.assertIsNotNone(ready, "行测任务说明没有加载成功")
            page.get_element(".s9-primary-button", max_timeout=15).tap()
            page = wait_for_path(self.app, {"/pages/practice/index"}, timeout=45)
            self.assertIsNotNone(page, "开始行测任务后没有进入作答页")

        self.assertEqual(page.path, "/pages/practice/index")
        ready = wait_for_data(
            page,
            lambda: page.data.get("status") == "ready"
            and page.data.get("sessionState") == "answering"
            and isinstance(page.data.get("view"), dict),
            timeout=45,
        )
        self.assertIsNotNone(ready, "行测作答会话没有进入 answering")
        options = page.get_elements(".option-card", max_timeout=15)
        self.assertTrue(options, "当前行测题没有选项")
        options[0].tap()
        self.assertTrue(
            wait_until(
                lambda: bool(
                    ((page.data.get("view") or {}).get("question") or {}).get(
                        "selectedOptionIds"
                    )
                ),
                timeout=10,
            ),
            "答案没有写入行测草稿",
        )
        confidence = page.get_elements(".confidence-chip--question", max_timeout=10)
        self.assertTrue(confidence)
        confidence[min(1, len(confidence) - 1)].tap()

        page.call_method("submitAttempt")
        reflection = wait_for_path(self.app, {"/pages/reflection/index"}, timeout=90)
        self.assertIsNotNone(reflection, "交卷后没有进入真实复盘页")
        reflection_data = wait_until(
            lambda: (
                lambda current: current
                if current.get("status") == "ready"
                and current.get("phase")
                in (
                    "summary",
                    "reflection",
                    "cause_processing",
                    "cause_followup",
                    "cause_decision",
                    "cause_insufficient",
                    "cause_terminated",
                    "completed",
                )
                else None
            )(reflection.data),
            timeout=90,
        )
        self.assertIsNotNone(reflection_data, "复盘数据没有进入稳定阶段")
        if reflection_data.get("phase") == "summary":
            reflection.call_method("startReflection")
            self.assertTrue(
                wait_until(lambda: reflection.data.get("phase") == "reflection", timeout=10)
            )
        if reflection.data.get("phase") == "reflection":
            review_count = len(reflection.data.get("reviewItems") or [])
            self.assertGreater(review_count, 0, "复盘阶段没有关键题")
            for index in range(review_count):
                reflection.call_method("confirmCauseAndNext")
                if index < review_count - 1:
                    self.assertTrue(
                        wait_until(
                            lambda expected=index + 1: reflection.data.get("currentReviewIndex")
                            == expected,
                            timeout=10,
                        )
                    )
            terminal = wait_until(
                lambda: (
                    current_page(self.app).path == "/pages/today/index"
                    or reflection.data.get("phase") != "reflection"
                ),
                timeout=90,
            )
            self.assertTrue(terminal, "提交复盘后仍停留在逐题阶段")

    def test_essay_task_accepts_structured_draft_edit(self):
        self._assert_disposable()
        _, today_data = open_ready_page(self, "/pages/today/index")
        navigation_url, _ = find_task_navigation(
            today_data["view"],
            {"/pages/essay-task/index"},
        )
        self.assertTrue(navigation_url, "专用账号今天没有可执行的真实申论任务")
        page = self.app.relaunch(navigation_url)
        ready = wait_for_data(
            page,
            lambda: page.data.get("status") == "ready"
            and isinstance(page.data.get("capability"), dict),
            timeout=60,
        )
        self.assertIsNotNone(ready, "申论任务工作区没有加载成功")
        stage = ready["capability"].get("stage")
        if stage == "essay_point_extraction":
            before = len(ready.get("points") or [])
            page.call_method("addPoint")
            self.assertTrue(
                wait_until(lambda: len(page.data.get("points") or []) == before + 1, timeout=10),
                "申论要点草稿没有新增",
            )
        elif stage == "essay_outlining":
            before = len(ready.get("outlineSections") or [])
            page.call_method("addOutlineSection")
            self.assertTrue(
                wait_until(
                    lambda: len(page.data.get("outlineSections") or []) == before + 1,
                    timeout=10,
                ),
                "申论提纲草稿没有新增",
            )
        elif stage == "essay_material_reading":
            page.call_method(
                "inputOverallTheme",
                {"detail": {"value": "Minium 全量测试草稿，不提交"}},
            )
            self.assertEqual(page.data.get("overallTheme"), "Minium 全量测试草稿，不提交")
        else:
            self.assertTrue(page.get_elements(".essay-workspace", max_timeout=15))
