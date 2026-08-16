from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.external_learning.models import (
    ContentMatchVersion,
    MatchCandidate,
)
from app.domains.auth.repository import token_hash_for_tests
from app.domains.external_ingestion.matching.catalog import (
    SQLQuestionContentCatalog,
)
from app.domains.external_ingestion.matching.repository import (
    SQLContentMatchRepository,
)
from tests.factories import seed_planning_shared_frozen_release
from tests.task_editing_helpers import claim_task_edit_headers
from tests.test_planning_routes import (
    _client,
    _seed_active_route_day_preference,
)
from tests.test_private_question_books import (
    _create_command,
    _seed_book_context,
    _service,
)


@dataclass(frozen=True, slots=True)
class _PracticeWorld:
    token: str
    owner: str
    book_id: str
    source_version_id: str
    artifact_version_id: str
    recognition_revision_id: str
    plan_task_id: str
    release_id: str
    question_ids: tuple[str, ...]
    content_version_ids: tuple[str, ...]
    content_match_version_ids: tuple[str, ...]

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def test_question_book_practice_starts_existing_runtime_with_partial_matches_and_source_lineage(
    db_session_factory: sessionmaker[Session],
) -> None:
    world = _seed_world(db_session_factory, unit_count=3, matched_release_indexes=(0, 1))
    client = _client(db_session_factory)

    launch = _launch(client, world, key="book-practice-happy-020")

    assert launch["bookId"] == world.book_id
    assert launch["sourceVersionId"] == world.source_version_id
    assert launch["selectedQuestionCount"] == 2
    assert launch["excludedQuestionCount"] == 1
    assert launch["excludedCounts"]["unmatched"] == 1
    assert launch["planTaskId"] == world.plan_task_id
    assert launch["startUrl"].endswith(f"planTaskId={world.plan_task_id}")
    session_response = client.get(
        f"/api/v2/xingce/task-sessions/{launch['sessionId']}",
        headers=world.auth,
    )
    assert session_response.status_code == 200, session_response.text
    questions = session_response.json()["data"]["bundle"]["questions"]
    assert [item["questionId"] for item in questions] == list(world.question_ids[:2])
    with db_session_factory() as session:
        scoring = session.execute(
            text(
                "select scoring_bundle from learning.xingce_task_sessions "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": launch["sessionId"]},
        ).scalar_one()
    context = scoring["selectionContext"]
    assert context["contractVersion"] == "private-question-book-selection@v1"
    assert context["bookId"] == world.book_id
    assert context["sourceVersionId"] == world.source_version_id
    assert [item["contentVersionId"] for item in context["selectedItems"]] == list(
        world.content_version_ids[:2]
    )


def test_question_book_practice_rejects_cross_owner_incompatible_plan_and_zero_eligible(
    db_session_factory: sessionmaker[Session],
) -> None:
    world = _seed_world(
        db_session_factory,
        unit_count=1,
        matched_release_indexes=(),
        unbound_current_matches=1,
        compatible_plan=False,
    )
    client = _client(db_session_factory)
    other_token = "question-book-practice-other-owner"
    with db_session_factory.begin() as session:
        other_owner = _seed_authenticated_user(session, token=other_token)
        assert other_owner != world.owner

    cross_owner = client.post(
        f"/api/v2/external-learning/question-books/{world.book_id}/practice-sessions",
        headers={
            "Authorization": f"Bearer {other_token}",
            "Idempotency-Key": "book-practice-cross-owner-020",
        },
        json={"planTaskId": world.plan_task_id},
    )
    incompatible = client.post(
        f"/api/v2/external-learning/question-books/{world.book_id}/practice-sessions",
        headers={**world.auth, "Idempotency-Key": "book-practice-incompatible-020"},
        json={"planTaskId": world.plan_task_id},
    )
    assert cross_owner.status_code == 409
    assert incompatible.status_code == 409

    _make_plan_task_compatible(db_session_factory, plan_task_id=world.plan_task_id)
    unavailable = client.post(
        f"/api/v2/external-learning/question-books/{world.book_id}/practice-sessions",
        headers={**world.auth, "Idempotency-Key": "book-practice-zero-eligible-020"},
        json={"planTaskId": world.plan_task_id},
    )
    assert unavailable.status_code == 409
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.xingce_task_sessions")
        ).scalar_one() == 0


def test_question_book_practice_is_idempotent_and_rejects_source_mismatch(
    db_session_factory: sessionmaker[Session],
) -> None:
    world = _seed_world(db_session_factory, unit_count=2, matched_release_indexes=(0, 1))
    client = _client(db_session_factory)

    first = _launch(client, world, key="book-practice-replay-020")
    replay = _launch(client, world, key="book-practice-replay-020")
    resumed = _launch(client, world, key="book-practice-resume-020")
    assert replay == first
    assert resumed["sessionId"] == first["sessionId"]

    reused_key = client.post(
        f"/api/v2/external-learning/question-books/{world.book_id}/practice-sessions",
        headers={**world.auth, "Idempotency-Key": "book-practice-replay-020"},
        json={"planTaskId": str(uuid4())},
    )
    other_source = client.post(
        f"/api/v2/external-learning/question-books/{uuid4()}/practice-sessions",
        headers={**world.auth, "Idempotency-Key": "book-practice-other-source-020"},
        json={"planTaskId": world.plan_task_id},
    )
    assert reused_key.status_code == 409
    assert other_source.status_code == 409
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.xingce_task_sessions")
        ).scalar_one() == 1


def test_question_book_practice_skips_changed_content_head_and_unpublished_match(
    db_session_factory: sessionmaker[Session],
) -> None:
    world = _seed_world(
        db_session_factory,
        unit_count=3,
        matched_release_indexes=(0,),
        stale_matches=1,
        unbound_current_matches=1,
    )
    client = _client(db_session_factory)

    launch = _launch(client, world, key="book-practice-stale-020")

    assert launch["selectedQuestionCount"] == 1
    assert launch["excludedQuestionCount"] == 2
    assert launch["excludedCounts"]["contentHeadChanged"] == 1
    assert launch["excludedCounts"]["notDeliveryEligible"] == 1
    session_response = client.get(
        f"/api/v2/xingce/task-sessions/{launch['sessionId']}",
        headers=world.auth,
    )
    assert [
        item["questionId"]
        for item in session_response.json()["data"]["bundle"]["questions"]
    ] == [world.question_ids[0]]


def test_question_book_practice_concurrent_launch_converges_on_one_session(
    db_session_factory: sessionmaker[Session],
) -> None:
    world = _seed_world(db_session_factory, unit_count=2, matched_release_indexes=(0, 1))
    barrier = Barrier(2)

    def launch(index: int) -> dict[str, Any]:
        client = _client(db_session_factory)
        barrier.wait(timeout=10)
        return _launch(client, world, key=f"book-practice-race-020-{index}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(launch, (1, 2)))

    assert results[0]["sessionId"] == results[1]["sessionId"]
    with db_session_factory() as session:
        assert session.execute(
            text("select count(*) from learning.xingce_task_sessions")
        ).scalar_one() == 1


def test_question_book_practice_checkpoint_submit_and_attempt_lineage_use_existing_runtime(
    db_session_factory: sessionmaker[Session],
) -> None:
    world = _seed_world(db_session_factory, unit_count=2, matched_release_indexes=(0, 1))
    client = _client(db_session_factory)
    launch = _launch(client, world, key="book-practice-recovery-start-020")
    started_response = client.get(
        f"/api/v2/xingce/task-sessions/{launch['sessionId']}", headers=world.auth
    )
    started = started_response.json()["data"]
    edit_headers = claim_task_edit_headers(
        client,
        world.auth,
        resource_type="xingce_task_session",
        resource_id=started["sessionId"],
        revision=started["revision"],
    )
    questions = started["bundle"]["questions"]
    checkpoint = client.put(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/checkpoint",
        headers={
            **world.auth,
            **edit_headers,
            "Idempotency-Key": "book-practice-recovery-checkpoint-020",
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
            "totalElapsedSeconds": len(questions) * 20,
        },
    )
    assert checkpoint.status_code == 200, checkpoint.text
    submitted = client.post(
        f"/api/v2/xingce/task-sessions/{started['sessionId']}/submit",
        headers={
            **world.auth,
            **edit_headers,
            "Idempotency-Key": "book-practice-recovery-submit-020",
        },
        json={
            "expectedRevision": checkpoint.json()["data"]["revision"],
            "totalElapsedSeconds": len(questions) * 20,
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["state"] == "reflection_required"
    with db_session_factory() as session:
        attempts = list(
            session.execute(
                text(
                    "select source_lineage_refs from learning.xingce_attempt_records "
                    "where session_id = cast(:session_id as uuid) order by question_id"
                ),
                {"session_id": started["sessionId"]},
            ).scalars()
        )
        assessments = session.execute(
            text(
                "select count(*) from learning.xingce_assessments "
                "where session_id = cast(:session_id as uuid)"
            ),
            {"session_id": started["sessionId"]},
        ).scalar_one()
    assert assessments == 2
    assert len(attempts) == 2
    for refs in attempts:
        assert f"private-question-book:{world.book_id}" in refs
        assert f"private-question-book-version:{world.source_version_id}" in refs
        assert f"source-artifact-version:{world.artifact_version_id}" in refs
        assert f"recognition-revision:{world.recognition_revision_id}" in refs
        assert any(ref.startswith("content-match-version:") for ref in refs)
        assert f"knowledge-map-release:{world.release_id}" in refs


def _seed_world(
    factory: sessionmaker[Session],
    *,
    unit_count: int,
    matched_release_indexes: tuple[int, ...],
    stale_matches: int = 0,
    unbound_current_matches: int = 0,
    compatible_plan: bool = True,
) -> _PracticeWorld:
    token = f"question-book-practice-{uuid4()}"
    with factory.begin() as session:
        seeded = _seed_book_context(session, match_states=("missing",) * unit_count)
        _authenticate_existing_user(session, owner=seeded.owner, token=token)
        release = seed_planning_shared_frozen_release(session, count=max(6, unit_count))
        content_ids: list[str] = []
        question_ids: list[str] = []
        for index in matched_release_indexes:
            content_ids.append(release.content_version_ids[index])
            question_ids.append(release.question_ids[index])
        for index in range(stale_matches):
            question_id, old_content_id, _current_content_id = _seed_unbound_question(
                session,
                key=f"stale-{index}-{uuid4()}",
                advance_head=True,
            )
            question_ids.append(question_id)
            content_ids.append(old_content_id)
        for index in range(unbound_current_matches):
            question_id, current_content_id, _ = _seed_unbound_question(
                session,
                key=f"unbound-{index}-{uuid4()}",
                advance_head=False,
            )
            question_ids.append(question_id)
            content_ids.append(current_content_id)
        if len(content_ids) > unit_count:
            raise AssertionError("more confirmed matches than recognition units")
        match_version_ids = _insert_confirmed_matches(
            session,
            owner=seeded.owner,
            artifact_id=seeded.artifact_id,
            recognition_id=seeded.recognition_id,
            unit_ids=seeded.unit_ids,
            content_version_ids=tuple(content_ids),
        )
        book = _service(session).create(
            owner_user_id=seeded.owner,
            command=_create_command(seeded),
            idempotency_key=f"question-book-practice-create-{uuid4()}",
        )
    client = _client(factory)
    auth = {"Authorization": f"Bearer {token}"}
    plan_response = client.get("/api/v2/plans/today", headers=auth)
    assert plan_response.status_code == 200, plan_response.text
    plan_task = next(
        task for task in plan_response.json()["data"]["tasks"] if task["track"] == "xingce"
    )
    if compatible_plan:
        _make_plan_task_compatible(factory, plan_task_id=plan_task["id"])
    return _PracticeWorld(
        token=token,
        owner=seeded.owner,
        book_id=str(book.book_id),
        source_version_id=str(book.source_version_id),
        artifact_version_id=str(book.artifact_version_id),
        recognition_revision_id=str(book.recognition_revision_id),
        plan_task_id=str(plan_task["id"]),
        release_id=release.release_id,
        question_ids=tuple(question_ids),
        content_version_ids=tuple(content_ids),
        content_match_version_ids=match_version_ids,
    )


def _authenticate_existing_user(session: Session, *, owner: str, token: str) -> None:
    session.execute(
        text(
            """
            insert into learner.profiles (
              user_id, daily_minutes, study_mode, onboarding_complete, profile_version
            ) values (cast(:owner as uuid), 45, 'parttime', true, 1)
            """
        ),
        {"owner": owner},
    )
    session.execute(
        text(
            """
            insert into learner.app_sessions (
              session_id, user_id, session_token_hash, device_id, expires_at
            ) values (
              cast(:session_id as uuid), cast(:owner as uuid), :token_hash,
              'question-book-practice-test', :expires_at
            )
            """
        ),
        {
            "session_id": str(uuid4()),
            "owner": owner,
            "token_hash": token_hash_for_tests(token),
            "expires_at": datetime.now(UTC) + timedelta(days=1),
        },
    )
    _seed_active_route_day_preference(session, user_id=owner)


def _seed_authenticated_user(session: Session, *, token: str) -> str:
    owner = str(uuid4())
    session.execute(
        text(
            "insert into learner.users (user_id, display_name) "
            "values (cast(:owner as uuid), '其他题本学习者')"
        ),
        {"owner": owner},
    )
    _authenticate_existing_user(session, owner=owner, token=token)
    return owner


def _make_plan_task_compatible(
    factory: sessionmaker[Session], *, plan_task_id: str
) -> None:
    with factory.begin() as session:
        session.execute(
            text(
                """
                update learning.daily_plan_tasks
                set stage = 'objective_practice',
                    kind = 'practice',
                    candidate_key = 'xingce-practice:言语理解与表达',
                    estimated_minutes = 6,
                    start_url = '/xingce/xingce-task-intro/index?mode=targeted'
                where task_id = cast(:task_id as uuid)
                """
            ),
            {"task_id": plan_task_id},
        )


def _insert_confirmed_matches(
    session: Session,
    *,
    owner: str,
    artifact_id: UUID,
    recognition_id: UUID,
    unit_ids: tuple[str, ...],
    content_version_ids: tuple[str, ...],
) -> tuple[str, ...]:
    repository = SQLContentMatchRepository(session)
    version_ids: list[str] = []
    for ordinal, (unit_id, content_version_id) in enumerate(
        zip(unit_ids, content_version_ids, strict=False), start=1
    ):
        match_id = uuid4()
        version_id = uuid4()
        version_ids.append(str(version_id))
        repository.insert(
            ContentMatchVersion(
                contentMatchVersionId=version_id,
                contentMatchId=match_id,
                artifactVersionId=artifact_id,
                recognitionRevisionId=recognition_id,
                questionUnitId=unit_id,
                ownerUserId=UUID(owner),
                versionNo=1,
                state="confirmed",
                candidates=(
                    MatchCandidate(
                        contentVersionId=content_version_id,
                        matchKind="exact",
                        score=0.99,
                        evidenceRefs=(f"question-book-practice-unit:{ordinal}",),
                    ),
                ),
                evaluationReason="single_exact",
                confirmedContentVersionId=content_version_id,
                matchingPolicyVersion="external-hybrid-match-v1",
                inputFingerprint=sha256(
                    f"{recognition_id}:{unit_id}:{content_version_id}".encode()
                ).hexdigest(),
                createdAt=datetime.now(UTC),
            )
        )
    return tuple(version_ids)


def _seed_unbound_question(
    session: Session,
    *,
    key: str,
    advance_head: bool,
) -> tuple[str, str, str | None]:
    question_id = f"private-book-{sha256(key.encode()).hexdigest()[:20]}"
    session.execute(
        text(
            """
            insert into product.questions (
              question_id, domain, status, module_name, question_type,
              stem, answer, shared_context_required, visual_required,
              occurrence_count, latest_year
            ) values (
              :question_id, 'objective', 'canonical', '言语理解', 'single_choice',
              :stem, 'A', false, false, 1, 2026
            )
            """
        ),
        {"question_id": question_id, "stem": f"未发布题 {key}"},
    )
    for option_index, label in enumerate(("A", "B", "C", "D"), start=1):
        session.execute(
            text(
                """
                insert into product.question_options (
                  question_id, option_index, label, option_text
                ) values (:question_id, :option_index, :label, :option_text)
                """
            ),
            {
                "question_id": question_id,
                "option_index": option_index,
                "label": label,
                "option_text": f"选项 {label}",
            },
        )
    first = SQLQuestionContentCatalog(session).publish_current(
        question_id=question_id
    ).content_version_id
    if not advance_head:
        return question_id, first, None
    session.execute(
        text("update product.questions set stem = stem || '（新版）' where question_id = :id"),
        {"id": question_id},
    )
    current = SQLQuestionContentCatalog(session).publish_current(
        question_id=question_id
    ).content_version_id
    return question_id, first, current


def _launch(client: TestClient, world: _PracticeWorld, *, key: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v2/external-learning/question-books/{world.book_id}/practice-sessions",
        headers={**world.auth, "Idempotency-Key": key},
        json={"planTaskId": world.plan_task_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]
