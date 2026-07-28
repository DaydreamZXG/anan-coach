from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from app.domains.xingce_learning.real_attempt.repository import XingceAttemptRepository
from app.domains.xingce_learning.real_attempt.service import (
    XingceAttemptError,
    _hash,
    _selection_lineage_refs,
)
from tests.factories import (
    FrozenKnowledgeNodeSeed,
    FrozenQuestionSeed,
    NegativeFrozenReleaseScenario,
    seed_canonical_frozen_release,
    seed_negative_frozen_release,
)
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_planning_routes import (
    _client,
    _seed_active_route_day_preference,
    _seed_learner_and_published_essay_content,
)


def test_targeted_selection_uses_frozen_eligibility_after_live_option_deletion(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    user_id = _seed_targeted_learner(
        db_session_factory,
        token="targeted-next-node-token",
        now=now,
        nodes=(
            _node("top-node", "micro_skill", "micro_skill", "高权重技巧", 0.90, 12),
            _node(
                "eligible-node",
                "micro_skill",
                "micro_skill",
                "可训练技巧",
                0.80,
                10,
            ),
        ),
        tags={
            "高权重技巧": (1, 2, 6),
            "可训练技巧": (3, 4, 5),
        },
    )
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "delete from product.question_options "
                "where question_id = 'planning-shared-question-6'"
            )
        )
        selection = XingceAttemptRepository().select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=3,
            user_id=user_id,
            now=now,
        )

    assert selection is not None
    assert selection.node_id == "top-node"
    assert selection.node_type == "micro_skill"
    assert [item["question_id"] for item in selection.questions] == [
        "planning-shared-question-1",
        "planning-shared-question-2",
        "planning-shared-question-6",
    ]


def test_targeted_selection_fails_closed_for_stale_release_and_never_crosses_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    user_id = _seed_targeted_learner(
        db_session_factory,
        token="targeted-module-token",
        now=now,
        fresh_until=now - timedelta(minutes=1),
        nodes=(
            _node(
                "quantity-node",
                "micro_skill",
                "micro_skill",
                "数量技巧",
                0.99,
                20,
                module_name="数量关系",
            ),
        ),
        tags={"数量技巧": (1, 2, 3, 4, 5, 6)},
    )
    repository = XingceAttemptRepository()
    with db_session_factory.begin() as session:
        assert (
            repository.select_targeted_frozen_questions(
                session,
                module_name="言语理解",
                limit=3,
                user_id=user_id,
                now=now,
            )
            is None
        )
        assert (
            repository.select_targeted_frozen_questions(
                session,
                module_name=None,
                limit=3,
                user_id=user_id,
                now=now,
            )
            is None
        )


def test_targeted_selection_is_user_isolated_and_does_not_repeat_exposed_questions(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-dedupe-token"
    user_id = _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "dedupe-node",
                "knowledge_point",
                "knowledge_point",
                "语义辨析",
                0.80,
                18,
            ),
        ),
        tags={"语义辨析": (1, 2, 3, 4, 5, 6)},
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _targeted_task(client, auth)
    started = _start(client, auth, task, key="targeted-dedupe-start")
    first_ids = [item["questionId"] for item in started["bundle"]["questions"]]

    other_user_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '隔离用户')"
            ),
            {"user_id": other_user_id},
        )
        repository = XingceAttemptRepository()
        next_day = repository.select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=3,
            user_id=user_id,
            now=now + timedelta(days=1),
        )
        other_user = repository.select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=3,
            user_id=other_user_id,
            now=now + timedelta(days=1),
        )

    assert next_day is not None
    assert other_user is not None
    assert set(first_ids).isdisjoint(
        {str(item["question_id"]) for item in next_day.questions}
    )
    assert [str(item["question_id"]) for item in other_user.questions] == first_ids


def test_baseline_selection_fails_closed_without_active_knowledge_release(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token="baseline-without-tree-token",
        include_objective_questions=False,
    )
    with db_session_factory.begin() as session:
        seed_negative_frozen_release(
            session,
            scenario=NegativeFrozenReleaseScenario.MISSING,
            release_key="baseline-without-active-release",
            questions=_shared_question_seeds(
                node_id_by_index={
                    index: "canonical-verbal-practice"
                    for index in range(1, 7)
                }
            ),
        )
        selected = XingceAttemptRepository().select_frozen_questions(
            session,
            module_name=None,
            limit=3,
            user_id=user_id,
        )

    assert selected == []


def test_retired_release_publishes_no_targeted_task_and_blocks_previously_planned_start(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-stale-plan-token"
    _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "stale-plan-node",
                "knowledge_point",
                "knowledge_point",
                "中心理解",
                0.70,
                14,
            ),
        ),
        tags={"中心理解": (1, 2, 3, 4, 5, 6)},
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _targeted_task(client, auth)

    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                update product.knowledge_map_releases
                   set status = 'retired', retired_at = :retired_at
                 where status = 'active'
                """
            ),
            {"retired_at": now},
        )
    blocked = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": "targeted-stale-start"},
        json={"planTaskId": task["id"]},
    )
    assert blocked.status_code == 503, blocked.text
    assert blocked.json()["code"] == "xingce_content_unavailable"

    other_token = "targeted-stale-plan-other-token"
    _seed_second_planning_user(db_session_factory, token=other_token, now=now)
    other_plan = client.get(
        "/api/v2/plans/today",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other_plan.status_code == 200, other_plan.text
    assert all(
        task["stage"] != "objective_practice"
        for task in other_plan.json()["data"]["tasks"]
    )


def test_targeted_start_freezes_selection_context_and_submit_lineage(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-lineage-token"
    _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "lineage-node",
                "micro_skill",
                "micro_skill",
                "转折定位",
                0.88,
                16,
            ),
        ),
        tags={"转折定位": (1, 2, 3, 4, 5, 6)},
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _targeted_task(client, auth)
    assert task["title"] == "行测针对训练：国考高频模块"
    started = _start(client, auth, task, key="targeted-lineage-start")
    assert "selectionContext" not in started
    assert "selectionContext" not in started["bundle"]

    with db_session_factory() as session:
        scoring = session.execute(
            text(
                """
                select scoring_bundle
                from learning.xingce_task_sessions
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
    context = scoring["selectionContext"]
    assert context == {
        "contractVersion": "xingce-targeted-selection@v1",
        **_active_release_metadata(db_session_factory),
        "freshUntil": context["freshUntil"],
        "nodeId": "lineage-node",
        "nodeType": "micro_skill",
        "moduleName": "言语理解与表达",
    }
    assert datetime.fromisoformat(context["freshUntil"]) > now

    submitted = _checkpoint_and_submit(
        client,
        auth,
        started,
        key_prefix="targeted-lineage",
    )
    assert submitted.status_code == 200, submitted.text
    with db_session_factory() as session:
        lineage_rows = session.execute(
            text(
                """
                select source_lineage_refs
                from learning.xingce_attempt_records
                where session_id = cast(:session_id as uuid)
                order by question_id
                """
            ),
            {"session_id": started["sessionId"]},
        ).scalars().all()
    expected_tree_refs = {
        f"knowledge-map-release:{context['releaseId']}",
        f"knowledge-map-release-version:{context['releaseVersion']}",
        f"knowledge-map-source-data-version:{context['sourceDataVersion']}",
        f"knowledge-map-vocab-version:{context['vocabVersion']}",
        "knowledge-map-node:lineage-node",
    }
    assert lineage_rows
    assert all(expected_tree_refs <= set(refs) for refs in lineage_rows)


def test_pre_selection_contract_targeted_session_remains_submittable_without_fake_lineage(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-legacy-contract-token"
    _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "legacy-compatible-node",
                "knowledge_point",
                "knowledge_point",
                "逻辑填空",
                0.75,
                11,
            ),
        ),
        tags={"逻辑填空": (1, 2, 3, 4, 5, 6)},
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    started = _start(
        client,
        auth,
        _targeted_task(client, auth),
        key="targeted-legacy-start",
    )
    with db_session_factory.begin() as session:
        scoring = session.execute(
            text(
                """
                select scoring_bundle
                from learning.xingce_task_sessions
                where session_id = cast(:session_id as uuid)
                for update
                """
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
        legacy_scoring = dict(scoring)
        legacy_scoring.pop("selectionContext")
        legacy_scoring.pop("scoringBundleVersion")
        legacy_scoring["scoringBundleVersion"] = _hash(legacy_scoring)
        session.execute(
            text(
                """
                update learning.xingce_task_sessions
                   set scoring_bundle = cast(:scoring_bundle as jsonb)
                 where session_id = cast(:session_id as uuid)
                """
            ),
            {
                "session_id": started["sessionId"],
                "scoring_bundle": json.dumps(legacy_scoring, ensure_ascii=False),
            },
        )

    submitted = _checkpoint_and_submit(
        client,
        auth,
        started,
        key_prefix="targeted-legacy",
    )
    assert submitted.status_code == 200, submitted.text
    with db_session_factory() as session:
        refs = session.execute(
            text(
                """
                select source_lineage_refs
                from learning.xingce_attempt_records
                where session_id = cast(:session_id as uuid)
                """
            ),
            {"session_id": started["sessionId"]},
        ).scalars().all()
    assert refs
    assert all(not any(str(ref).startswith("knowledge-map-") for ref in row) for row in refs)


@pytest.mark.parametrize(
    "context",
    [
        {
            "contractVersion": "xingce-targeted-selection@v0",
            "releaseId": "release-1",
            "releaseVersion": "release-v1",
            "sourceDataVersion": "source-v1",
            "vocabVersion": "vocab-v1",
            "freshUntil": "2026-08-01T00:00:00+00:00",
            "nodeId": "node-1",
            "nodeType": "micro_skill",
            "moduleName": "言语理解",
        },
        {
            "contractVersion": "xingce-targeted-selection@v1",
            "releaseId": "release-1",
            "releaseVersion": "release-v1",
            "sourceDataVersion": "source-v1",
            "vocabVersion": "vocab-v1",
            "freshUntil": "2026-08-01T00:00:00+00:00",
            "nodeType": "micro_skill",
            "moduleName": "言语理解",
        },
    ],
)
def test_present_selection_context_must_match_the_complete_frozen_contract(
    context: dict[str, str],
) -> None:
    with pytest.raises(XingceAttemptError) as captured:
        _selection_lineage_refs(
            {"selectionContext": context},
            mode="targeted",
        )

    assert captured.value.code == "xingce_selection_context_invalid"


def _node(
    node_id: str,
    node_type: str,
    tag_type: str,
    tag_value: str,
    weight: float,
    occurrences: int,
    *,
    module_name: str = "言语理解与表达",
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "tag_type": tag_type,
        "tag_value": tag_value,
        "weight": weight,
        "occurrences": occurrences,
        "module_name": module_name,
    }


def _seed_targeted_learner(
    factory: sessionmaker[Session],
    *,
    token: str,
    now: datetime,
    fresh_until: datetime | None = None,
    nodes: tuple[dict[str, Any], ...],
    tags: dict[str, tuple[int, ...]],
) -> str:
    user_id = _seed_learner_and_published_essay_content(
        factory,
        access_token=token,
        include_objective_questions=False,
    )
    node_seeds = tuple(
        FrozenKnowledgeNodeSeed(
            node_id=str(node["node_id"]),
            module_name=str(node["module_name"]),
            node_type=str(node["node_type"]),
            tag_type=str(node["tag_type"]),
            tag_value=str(node["tag_value"]),
            near5_weight=float(node["weight"]),
            near5_occurrence_count=int(node["occurrences"]),
        )
        for node in nodes
    )
    node_id_by_index = {
        index: str(
            next(item for item in nodes if item["tag_value"] == tag_value)["node_id"]
        )
        for tag_value, indexes in tags.items()
        for index in indexes
    }
    module_name_by_index = {
        index: str(
            next(item for item in nodes if item["tag_value"] == tag_value)[
                "module_name"
            ]
        )
        for tag_value, indexes in tags.items()
        for index in indexes
    }
    questions = _shared_question_seeds(
        node_id_by_index=node_id_by_index,
        module_name_by_index=module_name_by_index,
    )
    with factory.begin() as session:
        _insert_complete_ability_estimate(session, user_id=user_id, now=now)
        if fresh_until is not None and fresh_until <= now:
            seed_negative_frozen_release(
                session,
                scenario=NegativeFrozenReleaseScenario.STALE,
                release_key=f"targeted:{token}",
                questions=questions,
                nodes=node_seeds,
                reference_time=now,
            )
        else:
            seed_canonical_frozen_release(
                session,
                release_key=f"targeted:{token}",
                questions=questions,
                nodes=node_seeds,
                reference_time=now,
            )
    return user_id


def _shared_question_seeds(
    *,
    node_id_by_index: dict[int, str],
    module_name_by_index: dict[int, str] | None = None,
) -> tuple[FrozenQuestionSeed, ...]:
    labels = ("A", "B", "C", "D")
    return tuple(
        FrozenQuestionSeed(
            question_id=f"planning-shared-question-{index}",
            stem=f"计划供给验收题 {index}",
            answer=labels[(index - 1) % len(labels)],
            node_id=node_id_by_index[index],
            module_name=(module_name_by_index or {}).get(index, "言语理解"),
            occurrence_count=100 - index,
            latest_year=2025,
        )
        for index in sorted(node_id_by_index)
    )


def _insert_complete_ability_estimate(
    session: Session,
    *,
    user_id: str,
    now: datetime,
) -> None:
    session.execute(
        text(
            """
            insert into learning.ability_estimate_versions (
              estimate_version_id, user_id, exam_code, exam_year, status,
              xingce_lower, xingce_recommended, xingce_upper,
              shenlun_lower, shenlun_recommended, shenlun_upper,
              total_lower, total_recommended, total_upper,
              confidence, source_evidence_ids, evidence_summary,
              algorithm_version, input_fingerprint, generated_at, valid_until
            ) values (
              cast(:estimate_id as uuid), cast(:user_id as uuid),
              'guokao', 2026, 'complete', 58, 62, 66, 52, 56, 60,
              110, 118, 126, 'medium', '[]'::jsonb, '{}'::jsonb,
              'targeted-selection-test-v1', :fingerprint,
              :generated_at, :valid_until
            )
            """
        ),
        {
            "estimate_id": str(uuid4()),
            "user_id": user_id,
            "fingerprint": sha256(f"estimate:{user_id}".encode()).hexdigest(),
            "generated_at": now - timedelta(minutes=5),
            "valid_until": now + timedelta(days=30),
        },
    )


def _seed_second_planning_user(
    factory: sessionmaker[Session],
    *,
    token: str,
    now: datetime,
) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                "insert into learner.users (user_id, display_name) "
                "values (cast(:user_id as uuid), '过期发布验收用户')"
            ),
            {"user_id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid),
                  :token_hash, 'targeted-stale-device', :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(token),
                "expires_at": now + timedelta(days=1),
            },
        )
        session.execute(
            text(
                """
                insert into learner.profiles (
                  user_id, daily_minutes, study_mode, onboarding_complete,
                  profile_version
                ) values (cast(:user_id as uuid), 45, 'parttime', true, 1)
                """
            ),
            {"user_id": user_id},
        )
        _seed_active_route_day_preference(session, user_id=user_id)
        _insert_complete_ability_estimate(session, user_id=user_id, now=now)
    return user_id


def _targeted_task(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/v2/plans/today", headers=auth)
    assert response.status_code == 200, response.text
    return next(
        task
        for task in response.json()["data"]["tasks"]
        if task["stage"] == "objective_practice"
    )


def _start(
    client: TestClient,
    auth: dict[str, str],
    task: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/v2/xingce/task-sessions",
        headers={**auth, "Idempotency-Key": key},
        json={"planTaskId": task["id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _checkpoint_and_submit(
    client: TestClient,
    auth: dict[str, str],
    started: dict[str, Any],
    *,
    key_prefix: str,
) -> Any:
    edit_headers = claim_task_edit_headers(
        client,
        auth,
        resource_type="xingce_task_session",
        resource_id=started["sessionId"],
        revision=started["revision"],
    )
    questions = started["bundle"]["questions"]
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={
            **auth,
            **edit_headers,
            "Idempotency-Key": f"{key_prefix}-checkpoint",
        },
        json={
            "expectedRevision": started["revision"],
            "currentQuestionId": questions[-1]["questionId"],
            "answers": [
                {
                    "questionId": item["questionId"],
                    "selectedOptionIds": ["A"],
                    "eliminatedOptionIds": [],
                    "confidence": "medium",
                    "marked": False,
                    "skipped": False,
                    "elapsedSeconds": 20,
                    "draftText": "",
                    "materialPosition": None,
                }
                for item in questions
            ],
            "totalElapsedSeconds": 60,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    return client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={
            **auth,
            **edit_headers,
            "Idempotency-Key": f"{key_prefix}-submit",
        },
        json={
            "expectedRevision": checkpoint.json()["data"]["revision"],
            "totalElapsedSeconds": 60,
        },
    )


def _active_release_metadata(
    factory: sessionmaker[Session],
) -> dict[str, str]:
    with factory() as session:
        row = (
            session.execute(
                text(
                    "select release_id, release_version, "
                    "source_data_version, vocab_version "
                    "from product.knowledge_map_releases "
                    "where status = 'active'"
                )
            )
            .mappings()
            .one()
        )
    return {
        "releaseId": str(row["release_id"]),
        "releaseVersion": str(row["release_version"]),
        "sourceDataVersion": str(row["source_data_version"]),
        "vocabVersion": str(row["vocab_version"]),
    }
