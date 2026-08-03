from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.domains.ilink_channel.control_plane import ILinkControlPlane


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-0000-0000-000000009301")


def _task(priority: int, title: str, status: str, minutes: int):
    return SimpleNamespace(
        priority=priority,
        title=title,
        status=status,
        estimated_minutes=minutes,
        completion_rule="完成真实作答并提交结果",
    )


def _dashboard(*, minutes: int = 600):
    return SimpleNamespace(
        headline="今天按双轨容量推进",
        local_date=date(2026, 8, 2),
        capacity=SimpleNamespace(
            capacity_version_id=uuid4(),
            available_minutes=minutes,
            energy=4,
            stress=2,
            confidence_level=3,
        ),
        tasks=[
            _task(1, "资料分析专项", "complete", 60),
            _task(2, "申论材料阅读", "in_progress", 90),
            _task(3, "言语理解训练", "ready", 80),
        ],
    )


class FakePlanning:
    def __init__(self) -> None:
        self.dashboard = _dashboard()
        self.capacity_calls = []

    def get_or_create_today(self, _session, **kwargs):
        assert kwargs["user_id"] == str(USER_ID)
        return self.dashboard

    def set_today_capacity(self, _session, **kwargs):
        self.capacity_calls.append(kwargs)
        command = kwargs["command"]
        changed = _dashboard(minutes=command.available_minutes)
        changed.capacity.capacity_version_id = command.based_on_capacity_version_id
        return SimpleNamespace(plan=changed)


class FakeKnowledge:
    def __init__(self) -> None:
        self.source = SimpleNamespace(
            source_id=UUID("00000000-0000-0000-0000-000000009302"),
            title="资料分析笔记",
            page_count=12,
        )
        self.deletions = []

    def list(self, *, owner_user_id: str):
        assert owner_user_id == str(USER_ID)
        return SimpleNamespace(sources=(self.source,))

    def search(self, *, owner_user_id: str, query: str, maximum_sources: int):
        assert owner_user_id == str(USER_ID)
        assert maximum_sources == 5
        return SimpleNamespace(
            citations=(
                SimpleNamespace(
                    title=self.source.title,
                    page_no=3,
                    excerpt=f"关于{query}的关键笔记",
                ),
            )
        )

    def request_delete(self, *, owner_user_id: str, source_id: UUID, idempotency_key: str):
        assert owner_user_id == str(USER_ID)
        self.deletions.append((source_id, idempotency_key))
        return SimpleNamespace(deletion_request_id=uuid4())


class FakeReceipts:
    def __init__(self) -> None:
        self.rows = {}

    def command_receipt(self, *, command_id: str):
        return self.rows.get(command_id)

    def save_command_receipt(self, **kwargs):
        self.rows[kwargs["command_id"]] = {
            "command_id": kwargs["command_id"],
            "command_type": kwargs["command_type"],
            "user_id": kwargs["user_id"],
            "result_payload": kwargs["result_payload"],
            "created_at": kwargs["now"],
        }

    def pending_material_delete(self, *, user_id: UUID, code: str):
        matches = [
            row
            for row in self.rows.values()
            if row["command_type"] == "control_material_delete_pending"
            and row["user_id"] == user_id
            and row["result_payload"]["code"] == code
        ]
        return matches[-1] if matches else None

    def material_delete_consumed(self, *, pending_command_id: str) -> bool:
        return any(
            row["command_type"] == "control_material_delete_consumed"
            and row["result_payload"]["pendingCommandId"] == pending_command_id
            for row in self.rows.values()
        )


def _control():
    planning = FakePlanning()
    knowledge = FakeKnowledge()
    receipts = FakeReceipts()
    control = ILinkControlPlane(
        planning=planning,  # type: ignore[arg-type]
        knowledge_factory=lambda _session: knowledge,  # type: ignore[arg-type]
        receipt_factory=lambda _session: receipts,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return control, planning, knowledge, receipts


def test_plan_next_progress_and_help_use_current_account_dashboard() -> None:
    control, _planning, _knowledge, _receipts = _control()

    plan = control.handle(None, user_id=USER_ID, source_message_id="m1", text="今天")
    next_task = control.handle(None, user_id=USER_ID, source_message_id="m2", text="下一步")
    progress = control.handle(None, user_id=USER_ID, source_message_id="m3", text="进度")
    help_result = control.handle(None, user_id=USER_ID, source_message_id="m4", text="主控台")

    assert plan is not None and "资料分析专项" in plan.reply and "申论材料阅读" in plan.reply
    assert next_task is not None and "下一步：申论材料阅读" in next_task.reply
    assert progress is not None and "已完成 1/3" in progress.reply and "剩余约 170 分钟" in progress.reply
    assert help_result is not None and "调整今天 10小时" in help_result.reply and "查资料 关键词" in help_result.reply


def test_capacity_update_preserves_state_and_uses_source_message_idempotency() -> None:
    control, planning, _knowledge, _receipts = _control()

    result = control.handle(
        None,
        user_id=USER_ID,
        source_message_id="capacity-1",
        text="调整今天 10小时",
    )

    assert result is not None and result.mutation_applied
    call = planning.capacity_calls[0]
    assert call["command_id"] == "ilink-capacity:capacity-1"
    assert call["command"].available_minutes == 600
    assert call["command"].energy == 4
    assert call["command"].stress == 2
    assert call["command"].confidence_level == 3


def test_materials_are_owner_scoped_and_delete_requires_single_use_confirmation() -> None:
    control, _planning, knowledge, receipts = _control()

    listed = control.handle(None, user_id=USER_ID, source_message_id="l1", text="我的资料")
    searched = control.handle(None, user_id=USER_ID, source_message_id="s1", text="查资料 增长率")
    pending = control.handle(None, user_id=USER_ID, source_message_id="d1", text="删除资料 1")

    assert listed is not None and "资料分析笔记" in listed.reply
    assert searched is not None and "第 3 页" in searched.reply and "增长率" in searched.reply
    assert pending is not None and pending.requires_confirmation and not knowledge.deletions
    pending_row = receipts.rows["ilink-material-delete-pending:d1"]
    code = pending_row["result_payload"]["code"]

    confirmed = control.handle(
        None,
        user_id=USER_ID,
        source_message_id="d2",
        text=f"确认删除资料 {code}",
    )
    replay = control.handle(
        None,
        user_id=USER_ID,
        source_message_id="d3",
        text=f"确认删除资料 {code}",
    )

    assert confirmed is not None and confirmed.mutation_applied
    assert len(knowledge.deletions) == 1
    assert replay is not None and "已经使用" in replay.reply
    assert len(knowledge.deletions) == 1


def test_unmatched_text_falls_through_to_companion() -> None:
    control, _planning, _knowledge, _receipts = _control()
    assert control.handle(None, user_id=USER_ID, source_message_id="q1", text="帮我解释这道题") is None
