from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.domains.ai.model_cost import ModelPriceCard
from app.domains.xingce_learning.cause_agent.worker import CauseAgentWorker
from tests.test_xingce_cause_agent_workflow import SequenceProvider
from tests.test_xingce_planning_closure import _submitted_attempt


def _low_confidence_all_correct_review(
    db_session_factory: sessionmaker[Session],
    *,
    token: str,
) -> dict[str, object]:
    """构造全对但一题 low confidence 的复盘场景。

    服务层 `_all_correct_high_confidence` 要求全部 correct + 全部 high，
    所以 low-confidence 全对会绕过服务层短路、正常入队；worker 短路
    （result_codes <= {correct, guessed_correct}）才触发——这正覆盖
    worker 全对短路的回归路径。
    """
    client, auth, _task, submitted = _submitted_attempt(
        db_session_factory,
        token=token,
        choices=["A", "B", "C"],  # 全对
    )
    review = submitted["review"]
    # 把 checkpoint 里第一题的 confidence 改成 low（其余保持 high）
    with db_session_factory() as session:
        session.execute(
            text(
                "update learning.xingce_task_sessions "
                "set checkpoint = jsonb_set(checkpoint::jsonb, "
                "'{answers,0,confidence}', '\"low\"'::jsonb) "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": submitted["sessionId"]},
        )
        session.commit()
    return {"client": client, "auth": auth, "review": review, "session_id": submitted["sessionId"]}


def test_worker_short_circuits_all_correct_low_confidence(
    db_session_factory: sessionmaker[Session],
) -> None:
    """全对但低信心的错因分析：worker 短路生成 evidence_insufficient，不调模型。"""
    ctx = _low_confidence_all_correct_review(
        db_session_factory,
        token="cause-agent-shortcircuit-token",
    )
    client = ctx["client"]
    auth = ctx["auth"]
    review = ctx["review"]

    # 提交反思，触发 cause job
    reflection = client.put(
        f"/api/v2/xingce/attempt-reviews/{review['reviewId']}/reflection",
        headers={**auth, "Idempotency-Key": "shortcircuit-reflection"},
        json={
            "expectedRevision": review["revision"],
            "rawText": "这组题我基本都会，个别题信心不高。",
            "tags": ["审题稳定"],
            "strategyNote": "继续保持。",
            "doubtNote": None,
            "voiceArtifactVersionIds": [],
        },
    )
    assert reflection.status_code == 200, reflection.text

    # 驱动 worker（provider 本会返回 candidate_ready，但短路路径应拦截）
    provider = SequenceProvider(["candidate_ready"])
    worker = CauseAgentWorker(
        settings=Settings(),
        provider=provider,
        price_card=ModelPriceCard(
            version="cause-agent-test-v1",
            input_cny_per_million=Decimal("1.00"),
            cached_input_cny_per_million=Decimal("0.10"),
            output_cny_per_million=Decimal("2.00"),
        ),
    )
    # worker 消费是轮询式：queued → running → 完成；每轮 reset run_after。
    final: str | None = None
    for _ in range(5):
        with db_session_factory() as session:
            session.execute(
                text(
                    "update platform.background_jobs "
                    "set run_after = now() - interval '1 second' "
                    "where job_type = 'xingce_cause_agent_v1'"
                )
            )
            session.commit()
        with db_session_factory() as session:
            job = worker._jobs.claim_next(session, job_types=("xingce_cause_agent_v1",))
            if job is None:
                session.rollback()
                continue
            session.commit()
            completed = worker._process_claim(session, job=job)
        if completed is not None:
            final = completed.status
            if final == "succeeded":
                break
    assert final == "succeeded", f"worker did not complete, final={final}"

    # 前端可见结果：候选是 evidence_insufficient，额度 not_charged，未调模型
    current = client.get(
        f"/api/v2/xingce/task-sessions/{ctx['session_id']}",
        headers=auth,
    ).json()["data"]
    candidate = current["review"]["aiCandidate"]
    assert candidate["state"] == "evidence_insufficient"
    assert candidate["reason"] == "no_incorrect_attempt"
    assert candidate["quotaState"] == "not_charged", candidate["quotaState"]

    # 不调模型：无 model_call_ledger 行，预留被释放（不悬挂）
    with db_session_factory() as session:
        ledger_count = session.execute(
            text("select count(*) from ai_audit.model_call_ledger")
        ).scalar_one()
        reservation_status = session.execute(
            text(
                "select status from commerce.feature_usage_reservations "
                "where feature = 'xingce_cause_analysis'"
            )
        ).scalar_one_or_none()
    assert ledger_count == 0
    if reservation_status is not None:
        assert reservation_status == "refunded", f"reservation leaked: {reservation_status}"
