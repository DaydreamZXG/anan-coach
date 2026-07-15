from __future__ import annotations

from app.domains.task_help.repository import TaskHelpRepository
from app.domains.task_help.schemas import TaskHelpRequest, TaskHelpResponse


class TaskHelpError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TaskHelpService:
    def __init__(self, repository: TaskHelpRepository) -> None:
        self._repository = repository

    def get_help(self, *, user_id: str, request: TaskHelpRequest) -> TaskHelpResponse:
        context = self._repository.session_context(
            user_id=user_id,
            session_id=str(request.session_id),
            requested_mode=request.mode,
        )
        if context is None:
            raise TaskHelpError(
                "task_help_session_not_found",
                "作答会话不存在。",
                status_code=404,
            )
        expected_runtime_mode = {
            "retest": "verification",
            "practice": "practice",
            "targeted": "targeted",
            "baseline": "baseline",
            "verification": "verification",
            "mock": "mock",
        }[request.mode]
        if context.runtime_mode != expected_runtime_mode:
            raise TaskHelpError(
                "task_help_mode_mismatch",
                "帮助模式与作答会话不匹配。",
                status_code=409,
            )

        if request.scope == "operation":
            title = "作答操作帮助"
            steps = [
                "切题、答题卡和材料阅读位置会随断点一起保存。",
                "退出前请等待页面显示已保存；再次进入会从服务端断点恢复。",
                "题面或图片异常可从工具区提交题目争议。",
            ]
            permitted = [
                "navigation_help",
                "checkpoint_status",
                "timer_recovery",
                "exit_policy",
                "dispute_entry",
            ]
        else:
            title = "作答故障处理"
            steps = [
                "先保持当前页面，等待本次保存结果返回。",
                "若网络失败，使用原操作重试；服务端会按幂等键返回同一结果。",
                "恢复后核对当前题号、答题卡和剩余时间再继续。",
            ]
            permitted = [
                "checkpoint_status",
                "timer_recovery",
                "fault_retry",
                "dispute_entry",
            ]
        return TaskHelpResponse(
            sessionId=str(request.session_id),
            mode=request.mode,
            scope=request.scope,
            sessionState=context.state,
            title=title,
            steps=steps,
            permittedTools=permitted,
            blockedCapabilities=[
                "current_question_answer",
                "answer_analysis",
                "formula_card",
                "solution_ai",
            ],
            contentLookupPerformed=False,
        )
