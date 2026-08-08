from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_xingce_planning_closure import _submitted_attempt


def test_legacy_session_missing_completion_is_backfilled_at_cause_decision(
    db_session_factory: sessionmaker[Session],
) -> None:
    """2026-08-04 之前的历史会话：执行事实尚未 complete、evidence 仍是
    pending_reflection、代际标记与 completion replay 为空。decide_cause 必须把它幂等补齐：
    写入 complete 执行事实并 settle 证据，且不破坏主决策流程。
    """
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="legacy-completion-backfill-token",
        choices=["A", "B", "C"],
    )
    session_id = submitted["sessionId"]
    _rewind_to_legacy_pending_completion(
        db_session_factory,
        token="legacy-completion-backfill-token",
        session_id=session_id,
    )
    _assert_legacy_pending_state(
        db_session_factory,
        user_id=_user_id(db_session_factory, session_id),
        session_id=session_id,
        task_id=task["id"],
    )

    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "legacy-backfill-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "历史会话在旧版本完成了复盘，计划完成当时尚未落库。",
            "tags": ["历史补齐"],
            "strategyNote": None,
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    candidate_id = reflected_data["review"]["aiCandidate"]["candidateVersionId"]

    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "legacy-backfill-cause-decision"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert decided.status_code == 200, decided.text
    completed = decided.json()["data"]
    assert completed["state"] == "completed"
    assert completed["review"]["state"] == "immediate_verification_due"
    assert completed["completion"]["receiptId"]
    assert completed["completion"]["settlementStatus"] == "completed"
    assert all(planned["id"] != task["id"] for planned in completed["completion"]["plan"]["tasks"])

    with db_session_factory() as session:
        execution = (
            session.execute(
                text(
                    "select status, evidence_refs "
                    "from learning.task_execution_versions "
                    "where task_id = cast(:task_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"task_id": task["id"]},
            )
            .mappings()
            .one()
        )
        assert execution["status"] == "complete"
        assert execution["evidence_refs"][0]["owner"] == "xingce_learning"
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "order by version_no desc limit 1"
                )
            ).scalar_one()
            == "settled"
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
            == "settled"
        )


def test_new_session_does_not_rewrite_completion_at_cause_decision(
    db_session_factory: sessionmaker[Session],
) -> None:
    """带 v1 代际标记的新会话：交卷时已写入 complete 执行事实并保存
    completion replay。decide_cause 必须幂等：不再重复写入执行事实，
    evidence 只 settle 一次，公开 completion receipt 沿用交卷时的那份。
    """
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="new-completion-idempotent-token",
        choices=["A", "B", "C"],
    )
    assert submitted["completion"]["receiptId"]
    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "new-completion-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "新会话复盘：确认错因并让证据结算。",
            "tags": ["审题"],
            "strategyNote": None,
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    candidate_id = reflected_data["review"]["aiCandidate"]["candidateVersionId"]

    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "new-completion-cause-decision"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert decided.status_code == 200, decided.text
    completed = decided.json()["data"]
    assert completed["state"] == "completed"
    assert completed["completion"]["receiptId"] == submitted["completion"]["receiptId"]

    with db_session_factory() as session:
        # 交卷写入的 complete 执行事实没有被重复写：仍是 1 条 in_progress + 1 条 complete。
        assert (
            session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one()
            == 2
        )
        latest_status = session.execute(
            text(
                "select status from learning.task_execution_versions "
                "where task_id = cast(:task_id as uuid) "
                "order by version_no desc limit 1"
            ),
            {"task_id": task["id"]},
        ).scalar_one()
        assert latest_status == "complete"
        # evidence 只 settle 一次：1 条 pending_reflection + 1 条 settled。
        assert (
            session.execute(
                text("select count(*) from learning.xingce_plan_evidence_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "order by version_no desc limit 1"
                )
            ).scalar_one()
            == "settled"
        )


def test_cause_decision_revision_does_not_resettle_completion(
    db_session_factory: sessionmaker[Session],
) -> None:
    """decide_cause 的 is_revision 分支（review 已是 immediate_verification_due）
    不进入 completion coordinator：不重复写入执行事实、不再 settle 证据，
    completion 仍由 _projection 从 replay payload 回填。
    """
    client, auth, task, submitted = _submitted_attempt(
        db_session_factory,
        token="revision-no-resettle-token",
        choices=["A", "B", "C"],
    )
    assert submitted["completion"]["receiptId"]
    review = submitted["review"]
    reflected = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "revision-no-resettle-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "首次决策后我调整了错因判断，再次提交。",
            "tags": ["错因复核"],
            "strategyNote": None,
            "doubtNote": None,
        },
    )
    assert reflected.status_code == 200, reflected.text
    reflected_data = reflected.json()["data"]
    candidate_id = reflected_data["review"]["aiCandidate"]["candidateVersionId"]

    decided = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "revision-no-resettle-decision"},
        json={
            "expectedRevision": reflected_data["review"]["revision"],
            "candidateVersionId": candidate_id,
            "decision": "reject",
            "replacementCause": None,
        },
    )
    assert decided.status_code == 200, decided.text
    completed = decided.json()["data"]
    assert completed["state"] == "completed"
    assert completed["review"]["state"] == "immediate_verification_due"
    assert completed["completion"]["receiptId"]

    revision_payload = {
        "expectedRevision": completed["review"]["revision"],
        "candidateVersionId": candidate_id,
        "decision": "replace",
        "replacementCause": "第二题其实是我看错了，替换为正确表述。",
    }
    revised = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/cause-decision",
        headers={**auth, "Idempotency-Key": "revision-no-resettle-revision"},
        json=revision_payload,
    )
    assert revised.status_code == 200, revised.text
    revised_data = revised.json()["data"]
    assert revised_data["state"] == "completed"
    assert revised_data["completion"]["receiptId"]

    with db_session_factory() as session:
        # 执行事实仍是 in_progress + complete 两条，没有第三条重复写入。
        assert (
            session.execute(
                text("select count(*) from learning.task_execution_versions")
            ).scalar_one()
            == 2
        )
        # 证据仍是 pending_reflection + settled 两条，没有二次 settle。
        assert (
            session.execute(
                text("select count(*) from learning.xingce_plan_evidence_versions")
            ).scalar_one()
            == 2
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "order by version_no desc limit 1"
                )
            ).scalar_one()
            == "settled"
        )


def test_submit_replay_stale_revision_and_same_key_payload_drift_do_not_duplicate_facts(
    db_session_factory: sessionmaker[Session],
) -> None:
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token="submit-retry-state-machine-token",
        choices=["A", "B", "C"],
    )
    session_id = submitted["sessionId"]
    submit_payload = {
        "expectedRevision": submitted["revision"] - 1,
        "totalElapsedSeconds": submitted["totalElapsedSeconds"],
    }
    with db_session_factory() as session:
        counts_before = _completion_fact_counts(session)

    replay = client.post(
        f"/api/v2/xingce/task-sessions/{session_id}/submit",
        headers={**auth, "Idempotency-Key": "submit-submit-retry-state-machine-token"},
        json=submit_payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == submitted

    drifted = client.post(
        f"/api/v2/xingce/task-sessions/{session_id}/submit",
        headers={**auth, "Idempotency-Key": "submit-submit-retry-state-machine-token"},
        json={**submit_payload, "totalElapsedSeconds": submit_payload["totalElapsedSeconds"] + 1},
    )
    assert drifted.status_code == 409, drifted.text
    assert drifted.json()["code"] == "idempotency_key_reused"

    stale = client.post(
        f"/api/v2/xingce/task-sessions/{session_id}/submit",
        headers={**auth, "Idempotency-Key": "stale-submit-state-machine"},
        json=submit_payload,
    )
    assert stale.status_code == 409, stale.text
    with db_session_factory() as session:
        assert _completion_fact_counts(session) == counts_before


def _completion_fact_counts(session: Session) -> dict[str, int]:
    return {
        "attempts": session.execute(
            text("select count(*) from learning.xingce_attempt_records")
        ).scalar_one(),
        "assessments": session.execute(
            text("select count(*) from learning.xingce_assessments")
        ).scalar_one(),
        "executions": session.execute(
            text("select count(*) from learning.task_execution_versions")
        ).scalar_one(),
        "evidence": session.execute(
            text("select count(*) from learning.xingce_plan_evidence_versions")
        ).scalar_one(),
        "outbox": session.execute(
            text("select count(*) from platform.outbox_events")
        ).scalar_one(),
    }


def _rewind_to_legacy_pending_completion(
    factory: sessionmaker[Session],
    *,
    token: str,
    session_id: str,
) -> None:
    """把新会话回退到 2026-08-04 之前的历史状态：
    - scoring_bundle 去掉新会话显式代际标记；
    - xingce_commands 中 attempt_submit 的 response_payload 去掉 completion，
      模拟旧会话没有可回放的完成 receipt；
    - 删除交卷写入的 complete 执行事实，仅保留 in_progress 起始事实；
    - evidence 保持 pending_reflection（旧会话证据确实没被 settle）。
    """
    submit_command_id = f"submit-{token}"
    with factory.begin() as session:
        session.execute(
            text(
                "update learning.xingce_task_sessions "
                "set scoring_bundle = scoring_bundle - 'sessionGeneration' "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": session_id},
        )
        session.execute(
            text(
                "alter table learning.xingce_commands "
                "disable trigger xingce_commands_append_only"
            )
        )
        try:
            session.execute(
                text(
                    "update learning.xingce_commands "
                    "set response_payload = response_payload - 'completion' "
                    "where user_id = :user_id "
                    "  and command_id = :command_id "
                    "  and command_type = 'attempt_submit'"
                ),
                {
                    "user_id": _user_id(factory, session_id),
                    "command_id": submit_command_id,
                },
            )
        finally:
            session.execute(
                text(
                    "alter table learning.xingce_commands "
                    "enable trigger xingce_commands_append_only"
                )
            )
        session.execute(
            text(
                "delete from learning.task_execution_versions "
                "where user_id = :user_id "
                "  and work_item_key = :work_item_key "
                "  and status = 'complete'"
            ),
            {
                "user_id": _user_id(factory, session_id),
                "work_item_key": _work_item_key(factory, session_id),
            },
        )


def _assert_legacy_pending_state(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    session_id: str,
    task_id: str,
) -> None:
    with factory() as session:
        assert (
            session.execute(
                text(
                    """
                    select count(*) from learning.xingce_commands
                    where user_id = cast(:user_id as uuid)
                      and command_type = 'attempt_submit'
                      and response_payload->'completion' <> 'null'::jsonb
                    """
                ),
                {"user_id": user_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text(
                    "select status from learning.task_execution_versions "
                    "where task_id = cast(:task_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"task_id": task_id},
            ).scalar_one()
            == "in_progress"
        )
        assert (
            session.execute(
                text(
                    "select state from learning.xingce_plan_evidence_versions "
                    "where session_id = cast(:session_id as uuid) "
                    "order by version_no desc limit 1"
                ),
                {"session_id": session_id},
            ).scalar_one()
            == "pending_reflection"
        )


def _user_id(factory: sessionmaker[Session], session_id: str) -> str:
    with factory() as session:
        return str(
            session.execute(
                text(
                    "select user_id from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
        )


def _work_item_key(factory: sessionmaker[Session], session_id: str) -> str:
    with factory() as session:
        return str(
            session.execute(
                text(
                    "select work_item_key from learning.xingce_task_sessions "
                    "where session_id = cast(:session_id as uuid)"
                ),
                {"session_id": session_id},
            ).scalar_one()
        )
