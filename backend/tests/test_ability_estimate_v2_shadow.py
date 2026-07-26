from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import log
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.ability_estimation.schemas import ReportedScoreEvidenceRequest
from app.domains.ability_estimation.service import AbilityEstimateService
from app.domains.ability_estimation.shadow_v2 import (
    ALGORITHM_VERSION_V2_SHADOW,
    GUESS_FLOOR,
    MOCK_ANSWER_WEIGHT,
    ShadowEstimateContractError,
    ShadowObservation,
    aggregate_question_difficulty,
    answer_probability,
    classify_timing_layer,
    difficulty_from_correct_ratio,
    estimate_theta_eap,
    map_theta_to_v1_score,
    recompute_user_shadow_estimate,
)


NOW = datetime(2026, 7, 27, 8, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 难度锚定换算：b = logit(1 - 全国正确率)，多次出现按样本加权。
# ---------------------------------------------------------------------------


def test_difficulty_anchor_is_logit_of_wrong_ratio() -> None:
    assert difficulty_from_correct_ratio(0.5) == pytest.approx(0.0)
    # 正确率 0.8：题目更容易，b = ln(0.2/0.8) < 0。
    assert difficulty_from_correct_ratio(0.8) == pytest.approx(log(0.2 / 0.8))
    # 正确率 0.2：题目更难，b 与上式对称为正。
    assert difficulty_from_correct_ratio(0.2) == pytest.approx(log(0.8 / 0.2))
    # 极端正确率被截断，logit 不发散。
    assert difficulty_from_correct_ratio(1.0) == pytest.approx(log(0.02 / 0.98))
    assert difficulty_from_correct_ratio(0.0) == pytest.approx(log(0.98 / 0.02))


def test_multiple_occurrences_use_sample_weighted_ratio() -> None:
    # (0.9 × 300 + 0.5 × 100) / 400 = 0.8
    assert aggregate_question_difficulty([(0.9, 300), (0.5, 100)]) == pytest.approx(
        difficulty_from_correct_ratio(0.8)
    )
    # 缺样本量的出现按权重 1 计：均值 0.7。
    assert aggregate_question_difficulty([(0.9, None), (0.5, None)]) == pytest.approx(
        difficulty_from_correct_ratio(0.7)
    )
    # 没有任何正确率数据：题目不参与 v2，显式缺省。
    assert aggregate_question_difficulty([]) is None
    assert aggregate_question_difficulty([(None, 500)]) is None


# ---------------------------------------------------------------------------
# 蒙猜校正：4 选 1 下界 c = 0.25 的 3PL 简化。
# ---------------------------------------------------------------------------


def test_guessing_floor_bounds_answer_probability() -> None:
    # θ → -∞ 时答对概率趋于蒙猜下界 0.25，而不是 0。
    assert answer_probability(-10.0, 0.0) == pytest.approx(GUESS_FLOOR, abs=1e-3)
    # θ = b 时 P = c + (1-c)/2 = 0.625。
    assert answer_probability(0.0, 0.0) == pytest.approx(0.625)
    # θ → +∞ 时趋于 1。
    assert answer_probability(10.0, 0.0) == pytest.approx(1.0, abs=1e-3)
    # 单调性。
    assert (
        answer_probability(-1.0, 0.0)
        < answer_probability(0.0, 0.0)
        < answer_probability(1.0, 0.0)
    )


def test_eap_stays_finite_for_extreme_response_patterns() -> None:
    all_correct = [
        ShadowObservation(
            question_id=f"q-{index}",
            correct=True,
            difficulty_b=0.0,
            layer="untimed",
            weight=1.0,
            source="practice",
        )
        for index in range(20)
    ]
    all_wrong = [
        ShadowObservation(
            question_id=f"q-{index}",
            correct=False,
            difficulty_b=0.0,
            layer="untimed",
            weight=1.0,
            source="practice",
        )
        for index in range(20)
    ]
    assert estimate_theta_eap([]) is None
    theta_high, se_high = estimate_theta_eap(all_correct)
    theta_low, se_low = estimate_theta_eap(all_wrong)
    # 全对/全错时 MLE 会发散，EAP 必须有限且方向正确。
    assert -4.0 <= theta_low < 0.0 < theta_high <= 4.0
    assert se_high > 0.0 and se_low > 0.0


# ---------------------------------------------------------------------------
# 限时分层：超建议用时或无计时证据 → untimed；毫秒时钟优先。
# ---------------------------------------------------------------------------


def test_timing_layer_classification() -> None:
    assert classify_timing_layer(None) == "untimed"
    assert classify_timing_layer({"timingStatus": "within_advisory"}) == "timed"
    assert classify_timing_layer({"timingStatus": "advisory_exceeded"}) == "untimed"
    assert (
        classify_timing_layer({"questionElapsedSeconds": 40, "advisorySeconds": 50})
        == "timed"
    )
    assert (
        classify_timing_layer({"questionElapsedSeconds": 70, "advisorySeconds": 50})
        == "untimed"
    )
    # 采集契约 v1 的毫秒时钟优先于秒级检查点字段。
    assert (
        classify_timing_layer(
            {
                "questionElapsedMs": 30_000,
                "questionElapsedSeconds": 999,
                "advisorySeconds": 50,
            }
        )
        == "timed"
    )
    # 证据不完整（缺建议用时）无法证明限时环境。
    assert classify_timing_layer({"questionElapsedSeconds": 40}) == "untimed"


# ---------------------------------------------------------------------------
# 数据库集成：样本不足缺省 / 分层与模考加权 / v1 输出零变化。
# ---------------------------------------------------------------------------


def test_insufficient_sample_defaults_instead_of_numbers(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    with db_session_factory.begin() as session:
        for index in range(3):
            question_id = f"q-few-{index}"
            _seed_question(session, question_id, occurrences=[(0.5, 200)])
            _seed_practice_answer(session, user_id=user_id, question_id=question_id, correct=True)
    with db_session_factory.begin() as session:
        row = recompute_user_shadow_estimate(session, user_id=user_id, now=NOW)

    assert row["sample_size"] == 3
    assert row["insufficient_sample"] is True
    assert row["theta"] is None
    assert row["theta_se"] is None
    assert row["ci_lower"] is None
    assert row["ci_upper"] is None
    assert row["mapped_v1_xingce_score"] is None
    strata = dict(row["strata"])
    assert strata["untimed"]["sampleSize"] == 3
    assert strata["untimed"]["insufficientSample"] is True
    assert strata["untimed"]["theta"] is None
    assert strata["timed"]["sampleSize"] == 0
    assert strata["timed"]["insufficientSample"] is True
    divergence = dict(row["divergence"])
    assert divergence["basis"] == "v1_unavailable"
    assert divergence["mappedV2XingceScore"] is None
    assert row["algorithm_version"] == ALGORITHM_VERSION_V2_SHADOW


def test_layers_mock_weight_and_append_only(
    db_session_factory: sessionmaker[Session],
) -> None:
    strong_user = _seed_user(db_session_factory)
    weak_user = _seed_user(db_session_factory)
    practice_ids = [f"q-practice-{index}" for index in range(12)]
    mock_ids = [f"q-mock-{index}" for index in range(12)]
    with db_session_factory.begin() as session:
        for question_id in practice_ids + mock_ids:
            _seed_question(session, question_id, occurrences=[(0.5, 200)])
        # 无全国正确率的题：作答后必须被显式排除，不参与 v2。
        _seed_question(session, "q-no-ratio", occurrences=[(None, None)])
        for question_id in practice_ids:
            _seed_practice_answer(
                session, user_id=strong_user, question_id=question_id, correct=True
            )
            _seed_practice_answer(
                session, user_id=weak_user, question_id=question_id, correct=False
            )
        _seed_practice_answer(
            session, user_id=strong_user, question_id="q-no-ratio", correct=True
        )
        _seed_mock_session(
            session,
            user_id=strong_user,
            answers=[(question_id, "A") for question_id in mock_ids],
        )
    with db_session_factory.begin() as session:
        strong_row = recompute_user_shadow_estimate(session, user_id=strong_user, now=NOW)
    with db_session_factory.begin() as session:
        weak_row = recompute_user_shadow_estimate(session, user_id=weak_user, now=NOW)

    # 分层明细：12 条练习无计时证据 → untimed；12 条模考 → timed（权重 ×1.5）。
    assert strong_row["sample_size"] == 24
    strata = dict(strong_row["strata"])
    assert strata["untimed"]["sampleSize"] == 12
    assert strata["timed"]["sampleSize"] == 12
    assert strata["timed"]["mockAnswerCount"] == 12
    assert strata["policy"]["mockAnswerWeight"] == MOCK_ANSWER_WEIGHT
    assert strata["policy"]["excludedNoDifficulty"] == 1
    assert strata["timed"]["insufficientSample"] is False
    assert strata["untimed"]["insufficientSample"] is False

    strong_theta = float(strong_row["theta"])
    weak_theta = float(weak_row["theta"])
    assert strong_theta > 0.0 > weak_theta
    assert float(strong_row["ci_lower"]) <= strong_theta <= float(strong_row["ci_upper"])
    # 线性锚定：θ=0 对应 v1 均值口径，全对用户映射分应高于均值锚点。
    assert float(strong_row["mapped_v1_xingce_score"]) == pytest.approx(
        map_theta_to_v1_score(strong_theta)
    )
    assert float(strong_row["mapped_v1_xingce_score"]) > 55.0

    # append-only：重算同一用户只追加新行。
    with db_session_factory.begin() as session:
        recompute_user_shadow_estimate(session, user_id=strong_user, now=NOW)
    with db_session_factory() as session:
        count = session.execute(
            text(
                "select count(*) from learning.ability_estimate_v2_shadow "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": strong_user},
        ).scalar_one()
    assert count == 2


def test_v1_visible_output_is_untouched_by_shadow_recompute(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user(db_session_factory)
    _seed_release(db_session_factory)
    v1_service = AbilityEstimateService()
    with db_session_factory.begin() as session:
        v1_service.record_reported_score(
            session,
            user_id=user_id,
            command_id="v2-shadow-regression-1",
            report=ReportedScoreEvidenceRequest(
                reported_context="full_mock",
                xingce_score=62,
                timed_status="timed",
                occurred_at=NOW - timedelta(days=1),
            ),
            now=NOW,
        )
    with db_session_factory.begin() as session:
        for index in range(12):
            question_id = f"q-v1-{index}"
            _seed_question(session, question_id, occurrences=[(0.6, 150)])
            _seed_practice_answer(
                session, user_id=user_id, question_id=question_id, correct=index % 2 == 0
            )

    with db_session_factory() as session:
        before_current = v1_service.get_current(session, user_id=user_id, now=NOW)
        before_versions = _v1_rows(session)

    with db_session_factory.begin() as session:
        shadow_row = recompute_user_shadow_estimate(session, user_id=user_id, now=NOW)

    with db_session_factory() as session:
        after_current = v1_service.get_current(session, user_id=user_id, now=NOW)
        after_versions = _v1_rows(session)

    # 铁律：v1 用户可见输出一字不改。
    assert after_current.model_dump(by_alias=True, mode="json") == before_current.model_dump(
        by_alias=True, mode="json"
    )
    assert after_versions == before_versions

    # 对比留痕：影子行同步快照了 v1 当前区间并落 divergence。
    snapshot = dict(shadow_row["v1_snapshot"])
    assert snapshot["xingceRecommended"] == 62.0
    divergence = dict(shadow_row["divergence"])
    assert divergence["basis"] == "score_evidence_interval"
    assert divergence["v1Recommended"] == 62.0
    mapped = float(shadow_row["mapped_v1_xingce_score"])
    assert divergence["delta"] == pytest.approx(round(mapped - 62.0, 2))
    assert divergence["outsideV1Range"] in (True, False)


def test_unknown_user_is_rejected(db_session_factory: sessionmaker[Session]) -> None:
    with db_session_factory() as session:
        with pytest.raises(ShadowEstimateContractError):
            recompute_user_shadow_estimate(session, user_id=str(uuid4()), now=NOW)


# ---------------------------------------------------------------------------
# 播种辅助
# ---------------------------------------------------------------------------


def _seed_user(factory: sessionmaker[Session]) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id, display_name) values (:id, '学习者')"),
            {"id": user_id},
        )
    return user_id


def _seed_release(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.position_rule_releases (
                  release_id, exam_code, exam_year, rule_set_version,
                  position_data_version, status, activated_at
                ) values (
                  :release_id, 'guokao', 2026, 'rules-v1', 'positions-v1',
                  'active', :activated_at
                )
                """
            ),
            {"release_id": str(uuid4()), "activated_at": NOW},
        )


def _seed_question(
    session: Session,
    question_id: str,
    *,
    answer: str = "A",
    occurrences: list[tuple[float | None, int | None]] | None = None,
) -> None:
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, answer
            ) values (:question_id, 'xingce', 'active', '言语理解', :answer)
            """
        ),
        {"question_id": question_id, "answer": answer},
    )
    for ratio, sample_size in occurrences or []:
        session.execute(
            text(
                """
                insert into product.question_occurrences (
                  occurrence_id, question_id,
                  occurrence_correct_ratio, occurrence_sample_size
                ) values (:occurrence_id, :question_id, :ratio, :sample_size)
                """
            ),
            {
                "occurrence_id": f"occ-{uuid4()}",
                "question_id": question_id,
                "ratio": ratio,
                "sample_size": sample_size,
            },
        )


def _seed_practice_answer(
    session: Session,
    *,
    user_id: str,
    question_id: str,
    correct: bool,
) -> None:
    session.execute(
        text(
            """
            insert into learner.practice_answers (
              answer_id, user_id, task_id, question_id,
              selected_answer, correct_answer, correct, answered_at
            ) values (
              gen_random_uuid(), cast(:user_id as uuid), 'shadow-v2-test',
              :question_id, :selected, 'A', :correct, :answered_at
            )
            """
        ),
        {
            "user_id": user_id,
            "question_id": question_id,
            "selected": "A" if correct else "B",
            "correct": correct,
            "answered_at": NOW - timedelta(hours=1),
        },
    )


def _seed_mock_session(
    session: Session,
    *,
    user_id: str,
    answers: list[tuple[str, str]],
) -> None:
    paper_version_id = str(uuid4())
    session.execute(
        text(
            """
            insert into product.mock_exam_paper_versions (
              paper_version_id, paper_key, version_no, state, exam_scope,
              track, title, duration_minutes, question_ids, blueprint,
              learner_bundle_version_id, scoring_policy_version_id,
              exposure_policy, equivalence_metadata, content_fingerprint,
              published_at, created_at
            ) values (
              cast(:paper_version_id as uuid), :paper_key, 1, 'retired',
              'guokao', 'xingce', '影子测试卷', 120,
              '[]'::jsonb, '[]'::jsonb, 'shadow-bundle',
              'mock-objective-exact-v1', '{}'::jsonb, '{}'::jsonb,
              :fingerprint, :now, :now
            )
            """
        ),
        {
            "paper_version_id": paper_version_id,
            "paper_key": f"shadow:{paper_version_id}",
            "fingerprint": uuid4().hex + uuid4().hex,
            "now": NOW,
        },
    )
    session.execute(
        text(
            """
            insert into learner.mock_exam_sessions (
              session_id, user_id, exam_scope, track, status,
              question_ids, blueprint, answers,
              paper_version_id, scoring_policy_version_id,
              submitted_at, created_at, updated_at
            ) values (
              gen_random_uuid(), cast(:user_id as uuid), 'guokao', 'xingce',
              'submitted', cast(:question_ids as jsonb), '[]'::jsonb,
              cast(:answers as jsonb),
              cast(:paper_version_id as uuid), 'mock-objective-exact-v1',
              :now, :now, :now
            )
            """
        ),
        {
            "user_id": user_id,
            "question_ids": _json([question_id for question_id, _ in answers]),
            "answers": _json(
                [
                    {"questionId": question_id, "selectedAnswer": selected}
                    for question_id, selected in answers
                ]
            ),
            "paper_version_id": paper_version_id,
            "now": NOW,
        },
    )


def _v1_rows(session: Session) -> list[dict[str, object]]:
    rows = session.execute(
        text(
            """
            select estimate_version_id::text as estimate_version_id,
                   xingce_lower, xingce_recommended, xingce_upper,
                   confidence, algorithm_version, generated_at
            from learning.ability_estimate_versions
            order by generated_at, estimate_version_id
            """
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
