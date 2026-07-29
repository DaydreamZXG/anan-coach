from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from threading import Event
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from app.domains.auth.repository import token_hash_for_tests
from app.domains.planning.repository import PlanningRepository
from app.domains.planning.service import PlanningService
from app.domains.xingce_learning.real_attempt.repository import (
    XingceAttemptRepository,
    _bounded_question_candidate_batch_size,
    _stable_question_distribution_key,
)
import app.domains.xingce_learning.real_attempt.repository as repository_module
from app.domains.task_content.service import TaskContentContractError
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
from tests.test_planning_xingce_assessment_facts import (
    _insert_assessment_facts,
    _insert_objective_questions,
)
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
    assert {item["question_id"] for item in selection.questions} == {
        "planning-shared-question-1",
        "planning-shared-question-2",
        "planning-shared-question-6",
    }


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
        other_user_before = repository.select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=3,
            user_id=other_user_id,
            now=now,
        )

    task = _targeted_task(client, auth)
    started = _start(client, auth, task, key="targeted-dedupe-start")
    first_ids = [item["questionId"] for item in started["bundle"]["questions"]]

    with db_session_factory.begin() as session:
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
        other_user_replay = repository.select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=3,
            user_id=other_user_id,
            now=now + timedelta(days=1),
        )

    assert other_user_before is not None
    assert next_day is not None
    assert other_user is not None
    assert other_user_replay is not None
    assert set(first_ids).isdisjoint({str(item["question_id"]) for item in next_day.questions})
    assert other_user.questions == other_user_before.questions
    assert other_user.questions == other_user_replay.questions


def test_selection_is_stable_diverse_and_uses_indexed_bounded_keyset_queries(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    _seed_targeted_learner(
        db_session_factory,
        token="targeted-diversity-token",
        now=now,
        nodes=(
            _node(
                "diversity-node",
                "micro_skill",
                "micro_skill",
                "片段阅读",
                0.92,
                30,
            ),
        ),
        tags={"片段阅读": tuple(range(1, 13))},
    )
    user_ids = tuple(f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 9))
    captured_candidate_queries: list[tuple[str, Any]] = []

    with db_session_factory.begin() as session:
        engine = session.get_bind()

        def capture_candidate_query(
            _connection: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            if "candidate_ids as materialized" in statement:
                captured_candidate_queries.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", capture_candidate_query)
        try:
            repository = XingceAttemptRepository()
            groups: list[tuple[str, ...]] = []
            for user_id in user_ids:
                first = repository.select_targeted_frozen_questions(
                    session,
                    module_name="言语理解",
                    limit=3,
                    user_id=user_id,
                    now=now,
                )
                replay = repository.select_targeted_frozen_questions(
                    session,
                    module_name="言语理解",
                    limit=3,
                    user_id=user_id,
                    now=now,
                )
                assert first is not None
                assert replay is not None
                first_ids = tuple(str(item["question_id"]) for item in first.questions)
                assert first_ids == tuple(str(item["question_id"]) for item in replay.questions)
                groups.append(first_ids)
        finally:
            event.remove(engine, "before_cursor_execute", capture_candidate_query)

    assert len(set(groups)) > 1
    quality_top_three = {
        "planning-shared-question-1",
        "planning-shared-question-2",
        "planning-shared-question-3",
    }
    assert any(set(group) != quality_top_three for group in groups)
    assert len(captured_candidate_queries) >= len(user_ids) * 2
    for statement, parameters in captured_candidate_queries:
        assert "random(" not in statement.lower()
        assert "offset" not in statement.lower()
        assert "jsonb_array_elements" not in statement.lower()
        assert "xingce_task_sessions" not in statement.lower()
        assert "safe_bundle" not in statement.lower()
        assert "xingce_question_exposures" in statement.lower()
        assert "digest(binding.question_id, 'sha256')" in statement.lower()
        assert isinstance(parameters["selection_cursor"], bytes)
        assert len(parameters["selection_cursor"]) == sha256().digest_size
        assert parameters["candidate_batch_size"] <= 24


def test_user_cursor_reaches_irregular_text_ids_beyond_the_initial_candidate_pool(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    irregular_suffixes = (
        "amber",
        "aurora",
        "birch",
        "brook",
        "cedar",
        "cobalt",
        "drift",
        "dune",
        "elm",
        "ember",
        "fern",
        "flint",
        "garnet",
        "grove",
        "harbor",
        "haze",
        "indigo",
        "ivory",
        "jade",
        "juniper",
        "kestrel",
        "linen",
        "maple",
        "nectar",
        "onyx",
        "pebble",
        "quartz",
        "raven",
        "saffron",
        "thistle",
        "umber",
        "velvet",
        "willow",
        "xenon",
        "yarrow",
        "zephyr",
    )
    question_ids = {
        index: f"readable-{suffix}-case" for index, suffix in enumerate(irregular_suffixes, start=1)
    }
    _seed_targeted_learner(
        db_session_factory,
        token="targeted-reachable-token",
        now=now,
        nodes=(
            _node(
                "reachable-node",
                "micro_skill",
                "micro_skill",
                "细节判断",
                0.91,
                len(question_ids),
            ),
        ),
        tags={"细节判断": tuple(question_ids)},
        question_ids_by_index=question_ids,
    )
    initial_pool = {
        question_ids[index]
        for index in range(
            1,
            _bounded_question_candidate_batch_size(3) + 1,
        )
    }

    with db_session_factory.begin() as session:
        repository = XingceAttemptRepository()
        selections = [
            repository.select_targeted_frozen_questions(
                session,
                module_name="言语理解",
                limit=3,
                user_id=f"10000000-0000-0000-0000-{index:012d}",
                now=now,
            )
            for index in range(1, 25)
        ]

    assert all(selection is not None for selection in selections)
    selected_groups = {
        tuple(str(question["question_id"]) for question in selection.questions)
        for selection in selections
        if selection is not None
    }
    selected_union = {question_id for group in selected_groups for question_id in group}
    assert len(selected_groups) > 1
    assert selected_union - initial_pool


def test_prefix_shaped_text_ids_do_not_collapse_all_users_to_one_candidate_segment(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    question_ids = {
        1: "a",
        **{index: f"ag{index - 2:04d}" for index in range(2, 42)},
    }
    _seed_targeted_learner(
        db_session_factory,
        token="targeted-prefix-id-diversity-token",
        now=now,
        nodes=(
            _node(
                "prefix-id-diversity-node",
                "micro_skill",
                "micro_skill",
                "前缀文本标识",
                0.93,
                len(question_ids),
            ),
        ),
        tags={"前缀文本标识": tuple(question_ids)},
        question_ids_by_index=question_ids,
    )

    with db_session_factory.begin() as session:
        repository = XingceAttemptRepository()
        groups = {
            tuple(str(question["question_id"]) for question in selection.questions)
            for index in range(1, 17)
            if (
                selection := repository.select_targeted_frozen_questions(
                    session,
                    module_name="言语理解",
                    limit=3,
                    user_id=f"20000000-0000-0000-0000-{index:012d}",
                    now=now,
                )
            )
            is not None
        }

    assert len(groups) > 1
    assert len({question_id for group in groups for question_id in group}) > 3


def test_repeated_training_reaches_every_eligible_question_without_a_total_pool_cap(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-full-reachability-token"
    question_ids = {
        index: f"full-reachability-{index:03d}-{sha256(str(index).encode()).hexdigest()[:7]}"
        for index in range(1, 41)
    }
    user_id = _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "full-reachability-node",
                "micro_skill",
                "micro_skill",
                "全量可达",
                0.94,
                len(question_ids),
            ),
        ),
        tags={"全量可达": tuple(question_ids)},
        question_ids_by_index=question_ids,
    )
    task = _targeted_task(
        _client(db_session_factory),
        {"Authorization": f"Bearer {token}"},
    )
    selected_union: set[str] = set()

    for batch_index in range(8):
        with db_session_factory.begin() as session:
            selection = XingceAttemptRepository().select_targeted_frozen_questions(
                session,
                module_name="言语理解",
                limit=5,
                user_id=user_id,
                now=now,
            )
            assert selection is not None
            selected_ids = tuple(str(question["question_id"]) for question in selection.questions)
            assert len(selected_ids) == len(set(selected_ids)) == 5
            assert selected_union.isdisjoint(selected_ids)
            selected_union.update(selected_ids)
            task_row = (
                session.execute(
                    text(
                        """
                        select plan_version_id, local_date
                        from learning.daily_plan_tasks
                        where task_id = cast(:task_id as uuid)
                        """
                    ),
                    {"task_id": task["id"]},
                )
                .mappings()
                .one()
            )
            session.execute(
                text(
                    """
                    insert into learning.xingce_task_sessions (
                      session_id, user_id, source_plan_task_id,
                      source_plan_version_id, local_date, work_item_key,
                      mode, state, revision, bundle_version_id,
                      safe_bundle, scoring_bundle, current_question_id,
                      checkpoint, total_elapsed_seconds, created_at, updated_at
                    ) values (
                      cast(:session_id as uuid), cast(:user_id as uuid),
                      cast(:task_id as uuid), cast(:plan_version_id as uuid),
                      :local_date, :work_item_key, 'targeted', 'active', 1,
                      :bundle_version_id, cast(:safe_bundle as jsonb),
                      '{}'::jsonb, :current_question_id,
                      '{"answers":[]}'::jsonb, 0, :created_at, :created_at
                    )
                    """
                ),
                {
                    "session_id": str(uuid4()),
                    "user_id": user_id,
                    "task_id": task["id"],
                    "plan_version_id": str(task_row["plan_version_id"]),
                    "local_date": task_row["local_date"],
                    "work_item_key": sha256(
                        f"full-reachability:{batch_index}".encode()
                    ).hexdigest(),
                    "bundle_version_id": sha256("\x1f".join(selected_ids).encode()).hexdigest(),
                    "safe_bundle": json.dumps(
                        {"questions": [{"questionId": question_id} for question_id in selected_ids]}
                    ),
                    "current_question_id": selected_ids[0],
                    "created_at": now,
                },
            )

    with db_session_factory.begin() as session:
        exhausted = XingceAttemptRepository().select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=5,
            user_id=user_id,
            now=now,
        )

    assert selected_union == set(question_ids.values())
    assert exhausted is None


def test_invalid_first_ninety_six_candidates_still_fetches_later_valid_questions(
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    question_ids = {index: f"validation-window-{index:04d}" for index in range(1, 121)}
    _seed_targeted_learner(
        db_session_factory,
        token="targeted-validation-window-token",
        now=now,
        nodes=(
            _node(
                "validation-window-node",
                "knowledge_point",
                "knowledge_point",
                "语句排序",
                0.89,
                120,
            ),
        ),
        tags={"语句排序": tuple(range(1, 121))},
        question_ids_by_index=question_ids,
    )
    original_validate = repository_module.validate_learner_question
    invalid_ids = set(
        sorted(
            question_ids.values(),
            key=_stable_question_distribution_key,
        )[:96]
    )

    def reject_initial_window(question: dict[str, Any]) -> None:
        if str(question["question_id"]) in invalid_ids:
            raise TaskContentContractError(
                "test_invalid_candidate",
                "test invalid candidate",
            )
        original_validate(question)

    monkeypatch.setattr(
        repository_module,
        "validate_learner_question",
        reject_initial_window,
    )
    with db_session_factory.begin() as session:
        release_id = str(
            session.execute(
                text(
                    """
                    select release_id
                    from product.knowledge_map_releases
                    where status = 'active'
                    """
                )
            ).scalar_one()
        )
        selected = XingceAttemptRepository()._select_eligible_questions(
            session,
            release_id=release_id,
            node_id="validation-window-node",
            canonical_modules=("言语理解与表达",),
            limit=3,
            user_id=None,
            excluded_question_ids=None,
        )

    assert len(selected) == 3
    assert {str(question["question_id"]) for question in selected}.isdisjoint(invalid_ids)


def test_common_user_transaction_lock_serializes_targeted_and_baseline_selection(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-concurrent-selection-token"
    user_id = _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "concurrent-selection-node",
                "micro_skill",
                "micro_skill",
                "并发选题",
                0.93,
                12,
            ),
        ),
        tags={"并发选题": tuple(range(1, 13))},
    )
    client = _client(db_session_factory)
    task = _targeted_task(
        client,
        {"Authorization": f"Bearer {token}"},
    )
    first_selected = Event()
    second_attempted = Event()
    allow_first_commit = Event()

    def select_and_expose() -> tuple[str, ...]:
        with db_session_factory.begin() as session:
            selection = XingceAttemptRepository().select_targeted_frozen_questions(
                session,
                module_name="言语理解",
                limit=3,
                user_id=user_id,
                now=now,
            )
            assert selection is not None
            selected_ids = tuple(str(question["question_id"]) for question in selection.questions)
            first_selected.set()
            assert allow_first_commit.wait(timeout=10)
            task_row = (
                session.execute(
                    text(
                        """
                        select plan_version_id, local_date, work_item_key
                        from learning.daily_plan_tasks
                        where task_id = cast(:task_id as uuid)
                        """
                    ),
                    {"task_id": task["id"]},
                )
                .mappings()
                .one()
            )
            session.execute(
                text(
                    """
                    insert into learning.xingce_task_sessions (
                      session_id, user_id, source_plan_task_id,
                      source_plan_version_id, local_date, work_item_key,
                      mode, state, revision, bundle_version_id,
                      safe_bundle, scoring_bundle, current_question_id,
                      checkpoint, total_elapsed_seconds, created_at, updated_at
                    ) values (
                      cast(:session_id as uuid), cast(:user_id as uuid),
                      cast(:task_id as uuid), cast(:plan_version_id as uuid),
                      :local_date, :work_item_key, 'targeted', 'active', 1,
                      :bundle_version_id, cast(:safe_bundle as jsonb),
                      '{}'::jsonb, :current_question_id,
                      '{"answers":[]}'::jsonb, 0, :created_at, :created_at
                    )
                    """
                ),
                {
                    "session_id": str(uuid4()),
                    "user_id": user_id,
                    "task_id": task["id"],
                    "plan_version_id": str(task_row["plan_version_id"]),
                    "local_date": task_row["local_date"],
                    "work_item_key": str(task_row["work_item_key"]),
                    "bundle_version_id": "b" * 64,
                    "safe_bundle": json.dumps(
                        {"questions": [{"questionId": question_id} for question_id in selected_ids]}
                    ),
                    "current_question_id": selected_ids[0],
                    "created_at": now,
                },
            )
            return selected_ids

    def select_after_exposure() -> tuple[str, ...]:
        assert first_selected.wait(timeout=10)
        with db_session_factory.begin() as session:
            second_attempted.set()
            questions = XingceAttemptRepository().select_frozen_questions(
                session,
                module_name="言语理解",
                limit=3,
                user_id=user_id,
            )
            return tuple(str(question["question_id"]) for question in questions)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(select_and_expose)
        second_future = executor.submit(select_after_exposure)
        assert first_selected.wait(timeout=10)
        assert second_attempted.wait(timeout=10)
        with db_session_factory.begin() as observer:
            lock_available = observer.execute(
                text(
                    """
                    select pg_try_advisory_xact_lock(
                      hashtextextended(:selection_lock_key, 0)
                    )
                    """
                ),
                {"selection_lock_key": (f"xingce-question-selection:{user_id}")},
            ).scalar_one()
        assert lock_available is False
        allow_first_commit.set()
        first_ids = first_future.result(timeout=10)
        second_ids = second_future.result(timeout=10)

    assert set(first_ids).isdisjoint(second_ids)


def test_candidate_query_batch_has_a_hard_upper_bound_without_capping_reachability() -> None:
    assert _bounded_question_candidate_batch_size(3) == 24
    assert _bounded_question_candidate_batch_size(12) == 96
    assert _bounded_question_candidate_batch_size(10_000) == 96


def test_existing_task_session_replays_exact_bundle_and_exposure_shortfall_fails_closed(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    token = "targeted-replay-shortfall-token"
    user_id = _seed_targeted_learner(
        db_session_factory,
        token=token,
        now=now,
        nodes=(
            _node(
                "shortfall-node",
                "knowledge_point",
                "knowledge_point",
                "主旨概括",
                0.84,
                15,
            ),
        ),
        tags={"主旨概括": (1, 2, 3, 4, 5)},
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    task = _targeted_task(client, auth)
    started = _start(client, auth, task, key="targeted-shortfall-start")
    replayed = _start(client, auth, task, key="targeted-shortfall-replay")

    assert replayed == started
    with db_session_factory.begin() as session:
        shortfall = XingceAttemptRepository().select_targeted_frozen_questions(
            session,
            module_name="言语理解",
            limit=3,
            user_id=user_id,
            now=now + timedelta(days=1),
        )

    assert shortfall is None


def test_valid_weakness_evidence_selects_questions_from_the_weak_module(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC).replace(
        hour=6,
        minute=0,
        second=0,
        microsecond=0,
    )
    user_id = _seed_targeted_learner(
        db_session_factory,
        token="targeted-weakness-token",
        now=now,
        nodes=(
            _node(
                "weak-language-node",
                "micro_skill",
                "micro_skill",
                "中心理解",
                0.90,
                20,
            ),
            _node(
                "strong-quantity-node",
                "micro_skill",
                "micro_skill",
                "数字推理",
                0.88,
                18,
                module_name="数量关系",
            ),
        ),
        tags={
            "中心理解": (1, 2, 3, 4, 5, 6),
            "数字推理": (7, 8, 9, 10, 11, 12),
        },
    )
    planning = PlanningService()
    with db_session_factory.begin() as session:
        initial_plan = planning.get_or_create_today(
            session,
            user_id=user_id,
            now=now,
            local_date=now.date(),
        )
        language_history = _insert_objective_questions(
            session,
            prefix="selection-weak-language",
            module_name="言语理解",
            count=6,
        )
        quantity_history = _insert_objective_questions(
            session,
            prefix="selection-strong-quantity",
            module_name="数量关系",
            count=6,
        )
        _insert_assessment_facts(
            session,
            user_id=user_id,
            plan=initial_plan,
            question_results=[
                *zip(language_history, [True, False, False, False, False, False]),
                *zip(quantity_history, [True, True, True, True, True, False]),
            ],
        )

    next_day = now + timedelta(days=1)
    with db_session_factory.begin() as session:
        planning_repository = PlanningRepository()
        goal = planning_repository.goal_and_ability(
            session,
            user_id=user_id,
        )
        objective_snapshot = planning_repository.objective_snapshot(
            session,
            user_id=user_id,
            goal=goal,
            now=next_day,
        )
        selection = XingceAttemptRepository().select_targeted_frozen_questions(
            session,
            module_name=objective_snapshot.weakest_module,
            limit=3,
            user_id=user_id,
            now=next_day,
        )

    assert objective_snapshot.weakest_module == "言语理解与表达"
    assert objective_snapshot.targeted_question_count == 3
    assert selection is not None
    assert selection.module_name == "言语理解与表达"
    assert {str(item["question_id"]) for item in selection.questions} <= {
        f"planning-shared-question-{index}" for index in range(1, 7)
    }


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
                node_id_by_index={index: "canonical-verbal-practice" for index in range(1, 7)}
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
    assert all(task["stage"] != "objective_practice" for task in other_plan.json()["data"]["tasks"])


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
        lineage_rows = (
            session.execute(
                text(
                    """
                select source_lineage_refs
                from learning.xingce_attempt_records
                where session_id = cast(:session_id as uuid)
                order by question_id
                """
                ),
                {"session_id": started["sessionId"]},
            )
            .scalars()
            .all()
        )
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
        refs = (
            session.execute(
                text(
                    """
                select source_lineage_refs
                from learning.xingce_attempt_records
                where session_id = cast(:session_id as uuid)
                """
                ),
                {"session_id": started["sessionId"]},
            )
            .scalars()
            .all()
        )
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
    question_ids_by_index: dict[int, str] | None = None,
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
        index: str(next(item for item in nodes if item["tag_value"] == tag_value)["node_id"])
        for tag_value, indexes in tags.items()
        for index in indexes
    }
    module_name_by_index = {
        index: str(next(item for item in nodes if item["tag_value"] == tag_value)["module_name"])
        for tag_value, indexes in tags.items()
        for index in indexes
    }
    questions = _shared_question_seeds(
        node_id_by_index=node_id_by_index,
        module_name_by_index=module_name_by_index,
        question_ids_by_index=question_ids_by_index,
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
    question_ids_by_index: dict[int, str] | None = None,
) -> tuple[FrozenQuestionSeed, ...]:
    labels = ("A", "B", "C", "D")
    return tuple(
        FrozenQuestionSeed(
            question_id=(question_ids_by_index or {}).get(
                index,
                f"planning-shared-question-{index}",
            ),
            stem=f"计划供给验收题 {index}",
            answer=labels[(index - 1) % len(labels)],
            node_id=node_id_by_index[index],
            module_name=(module_name_by_index or {}).get(index, "言语理解"),
            occurrence_count=10_000 - index,
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
        task for task in response.json()["data"]["tasks"] if task["stage"] == "objective_practice"
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
