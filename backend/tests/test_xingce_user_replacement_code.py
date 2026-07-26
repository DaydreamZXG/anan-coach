"""用户改写错因的结构化回流：可选 8 码落库与聚合归桶。

约束（本轮范围）：
- ReplaceCause 命令新增可选 replacementCode（8 码分类之一，不含
  'user_replacement' 自身），落 confirmed_cause 新可选列；
- learning_brain 聚合：带码的改写按该码归桶，未选码的改写维持
  user_replacement 单桶原行为；
- 自由文本 replacementCause 语义不变，仍原样保存为 cause_text。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.learning_brain.service import _error_pattern
from app.domains.xingce_learning.real_attempt.schemas import (
    REPLACEMENT_CAUSE_CODES,
    CauseDecisionCommand,
)
from tests.test_xingce_cause_agent_workflow import SequenceProvider, _reflect, _run_one
from tests.test_xingce_planning_closure import _submitted_attempt
from tests.test_xingce_question_cause_candidates import _session_owner, _xingce_patterns


def test_replace_with_structured_code_persists_and_buckets_by_code(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="replacement-code-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    ready = _decision_ready(client, auth, db_session_factory, submitted, review)

    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "replacement-code-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": ready["review"]["aiCandidate"]["candidateVersionId"],
            "decision": "replace",
            "replacementCause": "其实是心算跳步导致的口算失误。",
            "replacementCode": "calculation_error",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["data"]["review"]["causeDecision"]["decision"] == "replace"

    with db_session_factory() as session:
        row = session.execute(
            text(
                "select source_kind, cause_code, replacement_code, cause_text "
                "from learning.xingce_confirmed_cause_versions"
            )
        ).mappings().one()
    # cause_code 保持 user_replacement 事实标记；结构化码只落新列。
    assert dict(row) == {
        "source_kind": "user_replacement",
        "cause_code": "user_replacement",
        "replacement_code": "calculation_error",
        "cause_text": "其实是心算跳步导致的口算失误。",
    }

    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])
    rows = _xingce_patterns(db_session_factory, user_id=user_id)
    assert len(rows) == 1
    assert rows[0]["cause_code"] == "calculation_error"
    assert rows[0]["occurrence_count"] == 1
    # 归桶后 display_name 置空，展示回落到已发布分类中文名。
    assert rows[0]["display_name"] is None
    pattern = _error_pattern(rows[0])
    assert pattern.cause_code == "calculation_error"
    assert pattern.display_name == "方法成立但计算、单位或抄写错误"


def test_replace_without_code_keeps_legacy_user_replacement_bucket(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="replacement-no-code-token",
        choices=["D", "B", "C"],
    )
    review = submitted["review"]
    ready = _decision_ready(client, auth, db_session_factory, submitted, review)

    decision = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "replacement-no-code-decision"},
        json={
            "expectedRevision": ready["review"]["revision"],
            "candidateVersionId": ready["review"]["aiCandidate"]["candidateVersionId"],
            "decision": "replace",
            "replacementCause": "我自己的说法：状态差，看什么都不进脑子。",
        },
    )
    assert decision.status_code == 200, decision.text

    with db_session_factory() as session:
        row = session.execute(
            text(
                "select cause_code, replacement_code, cause_text "
                "from learning.xingce_confirmed_cause_versions"
            )
        ).mappings().one()
    assert dict(row) == {
        "cause_code": "user_replacement",
        "replacement_code": None,
        "cause_text": "我自己的说法：状态差，看什么都不进脑子。",
    }

    user_id = _session_owner(db_session_factory, session_id=submitted["sessionId"])
    rows = _xingce_patterns(db_session_factory, user_id=user_id)
    # 未选码：维持 user_replacement 单桶与自由文本展示的旧行为。
    assert len(rows) == 1
    assert rows[0]["cause_code"] == "user_replacement"
    assert rows[0]["display_name"] == "我自己的说法：状态差，看什么都不进脑子。"
    assert _error_pattern(rows[0]).display_name == (
        "我自己的说法：状态差，看什么都不进脑子。"
    )


def test_replacement_code_command_validation() -> None:
    # 8 码枚举本身不含 user_replacement。
    assert "user_replacement" not in REPLACEMENT_CAUSE_CODES
    assert len(REPLACEMENT_CAUSE_CODES) == 8

    command = CauseDecisionCommand(
        expectedRevision=3,
        candidateVersionId=uuid4(),
        decision="replace",
        replacementCause="口算跳步出错",
        replacementCode="calculation_error",
    )
    assert command.replacement_code == "calculation_error"

    no_code = CauseDecisionCommand(
        expectedRevision=3,
        candidateVersionId=uuid4(),
        decision="replace",
        replacementCause="自由文本仍然可以单独提交",
    )
    assert no_code.replacement_code is None

    # 空白码等价于未选码。
    blank = CauseDecisionCommand(
        expectedRevision=3,
        candidateVersionId=uuid4(),
        decision="replace",
        replacementCause="空白码视同未选",
        replacementCode="  ",
    )
    assert blank.replacement_code is None

    with pytest.raises(ValidationError, match="published cause codes"):
        CauseDecisionCommand(
            expectedRevision=3,
            candidateVersionId=uuid4(),
            decision="replace",
            replacementCause="不接受未发布分类码",
            replacementCode="user_replacement",
        )
    with pytest.raises(ValidationError, match="only replace"):
        CauseDecisionCommand(
            expectedRevision=3,
            candidateVersionId=uuid4(),
            decision="confirm",
            replacementCode="calculation_error",
        )


def _decision_ready(
    client: Any,
    auth: dict[str, str],
    factory: sessionmaker[Session],
    submitted: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    _reflect(client, auth, review, key=f"reflect-{submitted['sessionId']}")
    _run_one(factory, SequenceProvider(["candidate_ready"]))
    current = client.get(
        f"/api/v2/xingce/task-sessions/{submitted['sessionId']}",
        headers=auth,
    )
    assert current.status_code == 200, current.text
    ready = current.json()["data"]
    assert ready["state"] == "cause_decision_required"
    return ready
