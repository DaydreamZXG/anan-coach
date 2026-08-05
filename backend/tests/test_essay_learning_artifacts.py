from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.repository import token_hash_for_tests
from app.domains.planning.repository import PlanningRepository
from app.domains.planning.service import PlanningService
from app.main import create_app
from tests.test_planning_routes import (
    _insert_foreign_rubric_successor,
    _seed_learner_and_published_essay_content,
)


NOW = datetime(2026, 7, 13, 8, tzinfo=UTC)


def test_artifact_routes_require_authentication() -> None:
    client = TestClient(create_app())
    task_id = str(uuid4())
    assert client.get(
        f"/api/v2/essay-intelligence/tasks/{task_id}/workspace"
    ).status_code == 401
    assert client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={"Idempotency-Key": "essay-artifact-auth"},
        json=_material_command(task_id, str(uuid4())),
    ).status_code == 401


def test_workspace_and_all_canonical_artifact_commands_are_owned_and_versioned(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-artifact-owner-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    other_token = "essay-artifact-other-token"
    other_user_id = _seed_other_learner(
        db_session_factory,
        access_token=other_token,
    )
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    other_auth = {"Authorization": f"Bearer {other_token}"}

    workspace_response = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers=auth,
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace = workspace_response.json()["data"]
    assert workspace["task"]["questionType"] == "summary"
    assert workspace["material"]["segments"][0]["segmentId"] == "segment-1"
    assert workspace["rubricAvailability"]["status"] == "published"
    assert workspace["rubricAvailability"]["rubric"]["points"][0][
        "rubricPointId"
    ] == "point-1"
    assert workspace["currentArtifacts"] == {}

    material_command = _material_command(ids["task"], ids["material"])
    material_response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "material-annotation-0001"},
        json=material_command,
    )
    assert material_response.status_code == 200, material_response.text
    material = material_response.json()["data"]
    replay = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "material-annotation-0001"},
        json=material_command,
    )
    assert replay.status_code == 200
    assert replay.json()["data"] == material
    conflict = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "material-annotation-0001"},
        json={
            **material_command,
            "payload": {**material_command["payload"], "overallTheme": "changed"},
        },
    )
    assert conflict.status_code == 409

    revised_command = {
        **material_command,
        "basedOnArtifactVersionId": material["artifactVersionId"],
        "payload": {
            **material_command["payload"],
            "overallTheme": "基层治理中的执行问题",
        },
    }
    revised = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "material-annotation-0002"},
        json=revised_command,
    )
    assert revised.status_code == 200, revised.text
    revised_material = revised.json()["data"]
    assert revised_material["versionNo"] == 2
    assert revised_material["supersedesArtifactVersionId"] == material[
        "artifactVersionId"
    ]
    stale = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "material-annotation-stale"},
        json={
            **material_command,
            "basedOnArtifactVersionId": material["artifactVersionId"],
        },
    )
    assert stale.status_code == 409

    other_material = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**other_auth, "Idempotency-Key": "other-material-0001"},
        json=material_command,
    )
    assert other_material.status_code == 200, other_material.text
    other_artifact_id = other_material.json()["data"]["artifactVersionId"]
    assert client.get(
        f"/api/v2/essay-intelligence/artifacts/{other_artifact_id}",
        headers=auth,
    ).status_code == 404

    cross_owner_point = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "cross-owner-point"},
        json=_point_command(ids, other_artifact_id),
    )
    assert cross_owner_point.status_code == 409

    point_response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "point-extraction-0001"},
        json=_point_command(ids, revised_material["artifactVersionId"]),
    )
    assert point_response.status_code == 200, point_response.text
    point = point_response.json()["data"]
    outline_response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "outline-0001"},
        json=_outline_command(ids, point["artifactVersionId"]),
    )
    assert outline_response.status_code == 200, outline_response.text

    answer_id, review_id = _seed_answer_and_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    reflection_response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "reflection-0001"},
        json=_reflection_command(ids, answer_id),
    )
    assert reflection_response.status_code == 200, reflection_response.text
    acknowledgement_response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "review-acknowledgement-0001"},
        json=_review_acknowledgement_command(ids, review_id),
    )
    assert acknowledgement_response.status_code == 200, acknowledgement_response.text

    state = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/artifacts/current",
        headers=auth,
    ).json()["data"]["currentByType"]
    assert set(state) == {
        "material_annotation",
        "point_extraction",
        "outline",
        "reflection",
        "review_acknowledgement",
    }
    workspace = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers=auth,
    ).json()["data"]
    current_review = workspace["progression"]["currentReview"]
    assert current_review["reviewVersionId"] == review_id
    assert current_review["findings"][0]["findingId"] == "finding-1"

    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.essay_learning_artifact_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 6
        assert session.execute(
            text(
                "select count(*) from platform.outbox_events "
                "where event_type = 'essay_learning_artifact.recorded'"
            )
        ).scalar_one() == 7
        assert other_user_id != user_id


def test_workspace_exposes_only_the_materials_named_by_the_task_prompt(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-material-scope-token"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    prompt = "请根据材料2概括主要问题。"
    second_text = "第二则材料只服务于当前题目。"
    with db_session_factory.begin() as session:
        session.execute(
            text(
                "update product.essay_task_versions "
                "set prompt = :prompt, prompt_sha256 = :prompt_sha "
                "where task_version_id = cast(:task_id as uuid)"
            ),
            {
                "task_id": ids["task"],
                "prompt": prompt,
                "prompt_sha": sha256(prompt.encode("utf-8")).hexdigest(),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_segments (
                  material_segment_id, material_bundle_version_id, material_id,
                  segment_id, material_order, segment_order, text, text_sha256
                ) values (
                  cast(:row_id as uuid), cast(:bundle_id as uuid), 'material-2',
                  'segment-2', 2, 1, :text, :text_sha
                )
                """
            ),
            {
                "row_id": str(uuid4()),
                "bundle_id": ids["material"],
                "text": second_text,
                "text_sha": sha256(second_text.encode("utf-8")).hexdigest(),
            },
        )

    response = _client(db_session_factory).get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    workspace = response.json()["data"]
    assert workspace["task"]["materialScope"] == {
        "mode": "explicit",
        "materialOrders": [2],
    }
    assert [
        item["materialOrder"] for item in workspace["material"]["segments"]
    ] == [2]
    assert workspace["material"]["segments"][0]["text"] == second_text

    scoped_command = _material_command(ids["task"], ids["material"])
    scoped_command["payload"] = {
        "schemaType": "material_annotation",
        "overallTheme": "第二则材料主题",
        "annotations": [
            {
                "segmentId": "segment-2",
                "paragraphSummary": "第二则材料服务于当前题目",
                "subjects": ["当前题目"],
                "evidenceQuote": second_text,
            }
        ],
    }
    saved = _client(db_session_factory).post(
        "/api/v2/essay-intelligence/artifacts",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "scoped-material-annotation-0001",
        },
        json=scoped_command,
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["payload"]["annotations"][0]["segmentId"] == "segment-2"


def test_workspace_and_planning_bind_review_to_the_current_answer_version(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-current-answer-review-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    first_answer_id, attempt_id = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    old_review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
        answer_id=first_answer_id,
    )
    current_answer_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  answer_sha256, input_fingerprint, supersedes_answer_version_id,
                  created_at
                ) values (
                  cast(:answer_id as uuid), :command_id, cast(:attempt_id as uuid),
                  cast(:user_id as uuid), cast(:task_id as uuid), 2, 'typed',
                  '基层治理需要明确责任并完善协同机制。', :answer_sha,
                  :input_sha, cast(:supersedes_id as uuid), :created_at
                )
                """
            ),
            {
                "answer_id": current_answer_id,
                "command_id": f"answer:{current_answer_id}",
                "attempt_id": attempt_id,
                "user_id": user_id,
                "task_id": ids["task"],
                "answer_sha": sha256(
                    "基层治理需要明确责任并完善协同机制。".encode()
                ).hexdigest(),
                "input_sha": sha256(current_answer_id.encode()).hexdigest(),
                "supersedes_id": first_answer_id,
                "created_at": NOW + timedelta(minutes=2),
            },
        )

    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    workspace = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers=auth,
    ).json()["data"]
    assert workspace["progression"]["answerVersionId"] == current_answer_id
    assert workspace["progression"]["reviewVersionId"] is None
    assert workspace["progression"]["currentReview"] is None

    repository = PlanningRepository()
    with db_session_factory() as session:
        goal = repository.goal_and_ability(session, user_id=user_id)
        snapshot = repository.essay_snapshot(session, user_id=user_id, goal=goal)
    assert snapshot.next_stage == "evaluation_pending"
    assert snapshot.answer_version_id == current_answer_id
    assert snapshot.review_version_id is None

    mismatched_reflection = _reflection_command(ids, current_answer_id)
    mismatched_reflection["reviewVersionId"] = old_review_id
    response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "reflection-old-review-answer"},
        json=mismatched_reflection,
    )
    assert response.status_code == 409


def test_reflection_rejects_answer_from_another_material_and_rubric_tuple(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-reflection-content-tuple-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    _insert_foreign_rubric_successor(
        db_session_factory,
        ids=ids,
        foreign_lineage="other_material",
    )
    with db_session_factory() as session:
        foreign = session.execute(
            text(
                """
                select rubric.material_bundle_version_id::text as material,
                       rubric.rubric_version_id::text as rubric
                from product.essay_rubric_versions rubric
                where rubric.task_version_id = cast(:task_id as uuid)
                  and rubric.version_no = 2
                """
            ),
            {"task_id": ids["task"]},
        ).mappings().one()
    foreign_ids = {
        "task": ids["task"],
        "material": str(foreign["material"]),
        "rubric": str(foreign["rubric"]),
    }
    foreign_answer_id, _ = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=foreign_ids,
    )

    response = _client(db_session_factory).post(
        "/api/v2/essay-intelligence/artifacts",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "reflection-cross-content-tuple",
        },
        json=_reflection_command(ids, foreign_answer_id),
    )
    assert response.status_code == 409


@pytest.mark.parametrize("foreign_lineage", ["other_material", "legacy_unbound"])
def test_workspace_keeps_the_exact_material_rubric_when_foreign_lineage_is_newer(
    db_session_factory: sessionmaker[Session],
    foreign_lineage: str,
) -> None:
    token = f"essay-workspace-rubric-lineage-{foreign_lineage}"
    _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        material_version_no=2 if foreign_lineage == "other_material" else 1,
    )
    ids = _content_ids(db_session_factory)
    _insert_foreign_rubric_successor(
        db_session_factory,
        ids=ids,
        foreign_lineage=foreign_lineage,
    )

    response = _client(db_session_factory).get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    workspace = response.json()["data"]
    assert workspace["material"]["materialBundleVersionId"] == ids["material"]
    assert workspace["rubricAvailability"]["status"] == "published"
    assert workspace["rubricAvailability"]["rubric"]["rubricVersionId"] == ids[
        "rubric"
    ]


def test_optional_material_training_does_not_fake_drafting_without_a_rubric(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-planning-no-rubric-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
        include_rubric=False,
    )
    ids = _content_ids(db_session_factory)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    service = PlanningService()

    with db_session_factory.begin() as session:
        initial = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    assert not any(item.track == "essay" for item in initial.tasks)
    assert "评分标准还没准备好" in initial.dual_track_explanation.omitted_track_reasons[
        "essay"
    ]

    artifact = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": "pending-rubric-material"},
        json=_material_command(ids["task"], ids["material"]),
    )
    assert artifact.status_code == 200, artifact.text
    with db_session_factory.begin() as session:
        pending = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    assert not any(item.track == "essay" for item in pending.tasks)
    assert "评分标准还没准备好" in pending.dual_track_explanation.omitted_track_reasons[
        "essay"
    ]
    workspace = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers=auth,
    ).json()["data"]
    assert workspace["rubricAvailability"] == {"status": "pending", "rubric": None}


def test_first_time_essay_progresses_material_to_point_to_outline_to_drafting(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-planning-progression-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    client = _client(db_session_factory)
    auth = {"Authorization": f"Bearer {token}"}
    service = PlanningService()

    # 首次接触申论：先完整学一遍「材料 → 要点 → 提纲 → 作答」。
    assert _essay_stage(db_session_factory, service, user_id) == "essay_material_reading"
    material = _post(
        client,
        auth,
        "progress-material",
        _material_command(ids["task"], ids["material"]),
    )
    assert _essay_stage(db_session_factory, service, user_id) == "essay_point_extraction"
    point = _post(
        client,
        auth,
        "progress-point",
        _point_command(ids, material["artifactVersionId"]),
    )
    assert _essay_stage(db_session_factory, service, user_id) == "essay_outlining"
    _post(
        client,
        auth,
        "progress-outline",
        _outline_command(ids, point["artifactVersionId"]),
    )
    assert _essay_stage(db_session_factory, service, user_id) == "essay_drafting"

    answer_id, _ = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    with db_session_factory.begin() as session:
        pending = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    assert not any(item.track == "essay" for item in pending.tasks)
    assert "评阅还在路上" in pending.dual_track_explanation.omitted_track_reasons[
        "essay"
    ]

    review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
        answer_id=answer_id,
    )
    assert _essay_stage(db_session_factory, service, user_id) == "essay_review"
    _post(
        client,
        auth,
        "progress-review-ack",
        _review_acknowledgement_command(ids, review_id),
    )
    assert _essay_stage(db_session_factory, service, user_id) == "essay_rewrite"


@pytest.mark.parametrize("review_status", ["blocked", "failed"])
def test_noncompleted_ai_review_never_creates_user_review_task(
    db_session_factory: sessionmaker[Session],
    review_status: str,
) -> None:
    user_id = _seed_learner_and_published_essay_content(db_session_factory)
    ids = _content_ids(db_session_factory)
    answer_id, _ = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    _insert_noncompleted_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
        answer_id=answer_id,
        review_status=review_status,
    )

    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )

    assert not any(item.stage == "essay_review" for item in plan.tasks)
    assert "申论评阅还没通过" in plan.dual_track_explanation.omitted_track_reasons[
        "essay"
    ]


def test_workspace_recovers_latest_review_job_before_review_row_exists(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-review-recovery-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    answer_id, _ = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    review_id = str(uuid4())
    with db_session_factory() as session:
        attempt_id = str(
            session.execute(
                text(
                    "select attempt_id from learning.essay_answer_versions "
                    "where answer_version_id = cast(:answer_id as uuid)"
                ),
                {"answer_id": answer_id},
            ).scalar_one()
        )
    older_job_id = str(uuid4())
    latest_job_id = str(uuid4())
    with db_session_factory.begin() as session:
        for job_id, created_at in (
            (older_job_id, NOW + timedelta(minutes=2)),
            (latest_job_id, NOW + timedelta(minutes=3)),
        ):
            session.execute(
                text(
                    """
                    insert into platform.background_jobs (
                      job_id, job_key, job_type, input_version, payload,
                      status, created_at, updated_at
                    ) values (
                      cast(:job_id as uuid), :job_key,
                      'essay_grounded_review_v2', 'essay-review-job-v2',
                      cast(:payload as jsonb), 'failed', :created_at, :created_at
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "job_key": f"essay-review-recovery:{job_id}",
                    "payload": json.dumps(
                        {
                            "userId": user_id,
                            "reviewVersionId": review_id,
                            "request": {
                                "attemptId": attempt_id,
                                "answerVersionId": answer_id,
                            },
                        }
                    ),
                    "created_at": created_at,
                },
            )

    response = _client(db_session_factory).get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    progression = response.json()["data"]["progression"]
    assert progression["reviewVersionId"] is None
    assert progression["reviewStatus"] is None
    assert progression["reviewJobId"] == latest_job_id


def test_draft_or_incomplete_successors_do_not_hide_latest_consumable_content(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-consumable-successor-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    draft_rubric_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type, prompt,
                  prompt_sha256, word_limit, maximum_score, state,
                  supersedes_task_version_id
                ) values (
                  cast(:task_v2 as uuid), 'guokao-summary-baseline', 2, 'summary',
                  'draft prompt', :sha, 200, 20, 'draft', cast(:task_v1 as uuid)
                )
                """
            ),
            {"task_v2": str(uuid4()), "task_v1": ids["task"], "sha": "e" * 64},
        )
        session.execute(
            text(
                """
                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key, version_no,
                  completeness, source_kind, content_sha256,
                  supersedes_material_bundle_version_id
                ) values (
                  cast(:material_v2 as uuid), cast(:task_v1 as uuid),
                  'guokao-summary-baseline', 2, 'incomplete', 'own_bank', :sha,
                  cast(:material_v1 as uuid)
                )
                """
            ),
            {
                "material_v2": str(uuid4()),
                "task_v1": ids["task"],
                "material_v1": ids["material"],
                "sha": "f" * 64,
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id, compilation_input_fingerprint,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256, compilation_audit,
                  supersedes_rubric_version_id
                ) select cast(:rubric_v2 as uuid), task_version_id, task_key, 2,
                         material_bundle_version_id, compilation_input_fingerprint,
                         source_kind, verification_status, 'draft', total_maximum,
                         criteria, points, :sha, compilation_audit, rubric_version_id
                  from product.essay_rubric_versions
                 where rubric_version_id = cast(:rubric_v1 as uuid)
                """
            ),
            {
                "rubric_v2": draft_rubric_id,
                "rubric_v1": ids["rubric"],
                "sha": "1" * 64,
            },
        )

    client = _client(db_session_factory)
    response = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    workspace = response.json()["data"]
    assert workspace["material"]["materialBundleVersionId"] == ids["material"]
    assert workspace["rubricAvailability"]["rubric"]["rubricVersionId"] == ids[
        "rubric"
    ]
    with db_session_factory.begin() as session:
        plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    essay = next(item for item in plan.tasks if item.track == "essay")
    assert any(
        ref.entity_id == ids["material"] for ref in essay.evidence_refs
    )

    auth = {"Authorization": f"Bearer {token}"}
    material = _post(
        client,
        auth,
        "successor-material",
        _material_command(ids["task"], ids["material"]),
    )
    point = _post(
        client,
        auth,
        "successor-point",
        _point_command(ids, material["artifactVersionId"]),
    )
    _post(
        client,
        auth,
        "successor-outline",
        _outline_command(ids, point["artifactVersionId"]),
    )
    published_rubric_id = str(uuid4())
    with db_session_factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id, compilation_input_fingerprint,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256, compilation_audit,
                  supersedes_rubric_version_id
                ) select cast(:rubric_v3 as uuid), task_version_id, task_key, 3,
                         material_bundle_version_id, compilation_input_fingerprint,
                         source_kind, verification_status, 'published', total_maximum,
                         criteria, points, :sha, compilation_audit,
                         cast(:rubric_v2 as uuid)
                  from product.essay_rubric_versions
                 where rubric_version_id = cast(:rubric_v1 as uuid)
                """
            ),
            {
                "rubric_v3": published_rubric_id,
                "rubric_v2": draft_rubric_id,
                "rubric_v1": ids["rubric"],
                "sha": "9" * 64,
            },
        )
    upgraded_workspace = client.get(
        f"/api/v2/essay-intelligence/tasks/{ids['task']}/workspace",
        headers=auth,
    ).json()["data"]
    assert upgraded_workspace["rubricAvailability"]["rubric"][
        "rubricVersionId"
    ] == published_rubric_id
    assert set(upgraded_workspace["currentArtifacts"]) == {"material_annotation"}
    with db_session_factory.begin() as session:
        upgraded_plan = PlanningService().get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    upgraded_essay = next(item for item in upgraded_plan.tasks if item.track == "essay")
    assert upgraded_essay.stage == "essay_drafting"


def _material_command(task_id: str, material_id: str) -> dict[str, object]:
    return {
        "artifactType": "material_annotation",
        "taskVersionId": task_id,
        "materialBundleVersionId": material_id,
        "rubricVersionId": None,
        "reviewVersionId": None,
        "answerVersionId": None,
        "basedOnArtifactVersionId": None,
        "payload": {
            "schemaType": "material_annotation",
            "overallTheme": "基层治理问题",
            "annotations": [
                {
                    "segmentId": "segment-1",
                    "paragraphSummary": "材料说明基层治理存在问题",
                    "subjects": ["基层"],
                    "evidenceQuote": "基层治理材料。",
                }
            ],
        },
    }


def _point_command(ids: dict[str, str], material_artifact_id: str) -> dict[str, object]:
    return {
        "artifactType": "point_extraction",
        "taskVersionId": ids["task"],
        "materialBundleVersionId": ids["material"],
        "rubricVersionId": ids["rubric"],
        "reviewVersionId": None,
        "answerVersionId": None,
        "basedOnArtifactVersionId": None,
        "payload": {
            "schemaType": "point_extraction",
            "sourceMaterialAnnotationVersionId": material_artifact_id,
            "points": [
                {
                    "pointId": "learner-point-1",
                    "category": "problem",
                    "statement": "基层治理执行存在问题",
                    "sourceSegmentIds": ["segment-1"],
                }
            ],
        },
    }


def _outline_command(ids: dict[str, str], point_artifact_id: str) -> dict[str, object]:
    return {
        "artifactType": "outline",
        "taskVersionId": ids["task"],
        "materialBundleVersionId": ids["material"],
        "rubricVersionId": ids["rubric"],
        "reviewVersionId": None,
        "answerVersionId": None,
        "basedOnArtifactVersionId": None,
        "payload": {
            "schemaType": "outline",
            "sourcePointExtractionVersionId": point_artifact_id,
            "thesis": "概括基层治理问题",
            "sections": [
                {
                    "sectionId": "section-1",
                    "heading": "执行问题",
                    "argument": "基层执行仍有不足",
                    "sourcePointIds": ["learner-point-1"],
                    "plannedWords": 100,
                }
            ],
        },
    }


def _reflection_command(ids: dict[str, str], answer_id: str) -> dict[str, object]:
    return {
        "artifactType": "reflection",
        "taskVersionId": ids["task"],
        "materialBundleVersionId": ids["material"],
        "rubricVersionId": ids["rubric"],
        "reviewVersionId": None,
        "answerVersionId": answer_id,
        "basedOnArtifactVersionId": None,
        "payload": {
            "schemaType": "reflection",
            "causeCodes": ["wrong_abstraction"],
            "whatIThought": "我把事实直接抄进了答案",
            "whatIWillChange": "先合并同类事实再概括",
            "verificationPlan": "下一题检查上位概念是否准确",
        },
    }


def _review_acknowledgement_command(
    ids: dict[str, str], review_id: str
) -> dict[str, object]:
    return {
        "artifactType": "review_acknowledgement",
        "taskVersionId": ids["task"],
        "materialBundleVersionId": ids["material"],
        "rubricVersionId": ids["rubric"],
        "reviewVersionId": review_id,
        "answerVersionId": None,
        "basedOnArtifactVersionId": None,
        "payload": {
            "schemaType": "review_acknowledgement",
            "acknowledgedFindingIds": ["finding-1"],
            "priorityFindingIds": ["finding-1"],
            "learnerUnderstanding": "需要把材料事实归纳成上位概念",
        },
    }


def _content_ids(factory: sessionmaker[Session]) -> dict[str, str]:
    with factory() as session:
        row = session.execute(
            text(
                """
                select task.task_version_id::text as task,
                       material.material_bundle_version_id::text as material,
                       rubric.rubric_version_id::text as rubric
                from product.essay_task_versions task
                join product.essay_material_bundle_versions material
                  on material.task_version_id = task.task_version_id
                left join product.essay_rubric_versions rubric
                  on rubric.task_version_id = task.task_version_id
                where task.task_key = 'guokao-summary-baseline'
                order by task.version_no, material.version_no, rubric.version_no
                limit 1
                """
            )
        ).mappings().one()
    return {
        "task": str(row["task"]),
        "material": str(row["material"]),
        "rubric": str(row["rubric"]) if row["rubric"] is not None else str(uuid4()),
    }


def _seed_other_learner(
    factory: sessionmaker[Session], *, access_token: str
) -> str:
    user_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text("insert into learner.users (user_id) values (cast(:id as uuid))"),
            {"id": user_id},
        )
        session.execute(
            text(
                """
                insert into learner.app_sessions (
                  session_id, user_id, session_token_hash, device_id, expires_at
                ) values (
                  cast(:session_id as uuid), cast(:user_id as uuid), :token_hash,
                  'essay-artifact-test',
                  :expires_at
                )
                """
            ),
            {
                "session_id": str(uuid4()),
                "user_id": user_id,
                "token_hash": token_hash_for_tests(access_token),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
    return user_id


def _seed_answer_without_review(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    ids: dict[str, str],
) -> tuple[str, str]:
    attempt_id = str(uuid4())
    answer_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.essay_attempts (
                  attempt_id, command_id, user_id, task_version_id, task_key,
                  material_bundle_version_id, rubric_version_id, source_context,
                  timed_status, input_fingerprint, submitted_at
                ) values (
                  cast(:attempt_id as uuid), :attempt_command, cast(:user_id as uuid),
                  cast(:task_id as uuid), 'guokao-summary-baseline',
                  cast(:material_id as uuid), cast(:rubric_id as uuid),
                  'in_program', 'untimed', :attempt_sha, :now
                )
                """
            ),
            {
                "attempt_id": attempt_id,
                "attempt_command": f"attempt:{attempt_id}",
                "user_id": user_id,
                "task_id": ids["task"],
                "material_id": ids["material"],
                "rubric_id": ids["rubric"],
                "attempt_sha": "2" * 64,
                "now": NOW,
            },
        )
        session.execute(
            text(
                """
                insert into learning.essay_answer_versions (
                  answer_version_id, command_id, attempt_id, user_id,
                  task_version_id, version_no, source_type, answer_text,
                  answer_sha256, input_fingerprint
                ) values (
                  cast(:answer_id as uuid), :answer_command,
                  cast(:attempt_id as uuid), cast(:user_id as uuid),
                  cast(:task_id as uuid), 1, 'typed', '基层治理存在执行问题。',
                  :answer_sha, :input_sha
                )
                """
            ),
            {
                "answer_id": answer_id,
                "answer_command": f"answer:{answer_id}",
                "attempt_id": attempt_id,
                "user_id": user_id,
                "task_id": ids["task"],
                "answer_sha": sha256("基层治理存在执行问题。".encode("utf-8")).hexdigest(),
                "input_sha": "4" * 64,
            },
        )
    return answer_id, attempt_id


def _insert_completed_review(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    ids: dict[str, str],
    answer_id: str,
) -> str:
    review_id = str(uuid4())
    with factory.begin() as session:
        attempt_id = str(
            session.execute(
                text(
                    "select attempt_id from learning.essay_answer_versions "
                    "where answer_version_id = cast(:answer_id as uuid)"
                ),
                {"answer_id": answer_id},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                insert into learning.essay_review_versions (
                  review_version_id, command_id, attempt_id, user_id,
                  task_version_id, answer_version_id, material_bundle_version_id,
                  rubric_version_id, status, review_scope,
                  thinking_evidence_available, readiness, score_lower,
                  score_recommended, score_upper, score_maximum, score_confidence,
                  score_confidence_reasons, grounding_contract_version,
                  grounding_report,
                  dimensions, point_coverage, claim_evidence, findings,
                  rewrite_plan, contract_version, provider_schema_version,
                  rubric_contract_version, prompt_version, provider, model,
                  input_fingerprint, execution_fingerprint,
                  execution_attempt_no, output_fingerprint, generated_at, valid_until
                ) values (
                  cast(:review_id as uuid), :command_id, cast(:attempt_id as uuid),
                  cast(:user_id as uuid), cast(:task_id as uuid),
                  cast(:answer_id as uuid), cast(:material_id as uuid),
                  cast(:rubric_id as uuid), 'completed', 'grounded_full', false,
                  cast(:readiness as jsonb),
                  10, 12, 14, 20, 'low', '["test_grounded_review"]'::jsonb,
                  'essay-grounding-v2', jsonb_build_object(
                    'verified', true, 'eligibleForLearning', true
                  ),
                  cast(:dimensions as jsonb), cast(:point_coverage as jsonb),
                  '[]'::jsonb,
                  cast(:findings as jsonb),
                  cast(:rewrite_plan as jsonb),
                  'essay-review-v2', 'provider-v1', 'rubric-v1', 'prompt-v1',
                  'provider', 'model', :input_sha, :execution_sha, 1,
                  :output_sha, :now, :valid_until
                )
                """
            ),
            {
                "review_id": review_id,
                "command_id": f"review:{review_id}",
                "attempt_id": attempt_id,
                "user_id": user_id,
                "task_id": ids["task"],
                "answer_id": answer_id,
                "material_id": ids["material"],
                "rubric_id": ids["rubric"],
                "readiness": json.dumps(
                    {
                        "materialComplete": True,
                        "promptComplete": True,
                        "answerComplete": True,
                        "ocrConfirmed": True,
                        "blockingCodes": [],
                    }
                ),
                "dimensions": json.dumps(
                    [{"criterionCode": "point_coverage"}]
                ),
                "point_coverage": json.dumps(
                    [{"rubricPointId": "point-1"}]
                ),
                "findings": _findings_json(answer_id, ids["material"]),
                "rewrite_plan": json.dumps(
                    {"priorityFindingIds": ["finding-1"]}
                ),
                "input_sha": "5" * 64,
                "execution_sha": sha256(review_id.encode()).hexdigest(),
                "output_sha": "6" * 64,
                "now": NOW + timedelta(minutes=1),
                "valid_until": NOW + timedelta(days=30),
            },
        )
    return review_id


def _seed_answer_and_completed_review(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    ids: dict[str, str],
) -> tuple[str, str]:
    answer_id, _ = _seed_answer_without_review(
        factory,
        user_id=user_id,
        ids=ids,
    )
    return answer_id, _insert_completed_review(
        factory,
        user_id=user_id,
        ids=ids,
        answer_id=answer_id,
    )


def _insert_noncompleted_review(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    ids: dict[str, str],
    answer_id: str,
    review_status: str,
) -> str:
    review_id = str(uuid4())
    with factory.begin() as session:
        attempt_id = session.execute(
            text(
                "select attempt_id from learning.essay_answer_versions "
                "where answer_version_id = cast(:answer_id as uuid)"
            ),
            {"answer_id": answer_id},
        ).scalar_one()
        session.execute(
            text(
                """
                insert into learning.essay_review_versions (
                  review_version_id, command_id, attempt_id, user_id,
                  task_version_id, answer_version_id, material_bundle_version_id,
                  rubric_version_id, status, review_scope,
                  thinking_evidence_available, readiness, dimensions,
                  point_coverage, claim_evidence, findings, contract_version,
                  provider_schema_version, rubric_contract_version, prompt_version,
                  input_fingerprint, execution_fingerprint,
                  execution_attempt_no, generated_at, valid_until
                ) values (
                  cast(:review_id as uuid), :command_id, cast(:attempt_id as uuid),
                  cast(:user_id as uuid), cast(:task_id as uuid),
                  cast(:answer_id as uuid), cast(:material_id as uuid),
                  cast(:rubric_id as uuid), :status, 'grounded_full', false,
                  cast(:readiness as jsonb), '[]'::jsonb, '[]'::jsonb,
                  '[]'::jsonb, '[]'::jsonb, 'essay-review-v2', 'provider-v1',
                  'rubric-v1', 'prompt-v1', :input_sha, :execution_sha, 1,
                  :now, :valid_until
                )
                """
            ),
            {
                "review_id": review_id,
                "command_id": f"review:{review_id}",
                "attempt_id": str(attempt_id),
                "user_id": user_id,
                "task_id": ids["task"],
                "answer_id": answer_id,
                "material_id": ids["material"],
                "rubric_id": ids["rubric"],
                "status": review_status,
                "readiness": json.dumps(
                    {
                        "materialComplete": True,
                        "promptComplete": True,
                        "answerComplete": True,
                        "ocrConfirmed": True,
                        "blockingCodes": ["provider_failure"],
                    }
                ),
                "input_sha": sha256(review_id.encode()).hexdigest(),
                "execution_sha": sha256(f"execution:{review_id}".encode()).hexdigest(),
                "now": NOW + timedelta(minutes=1),
                "valid_until": NOW + timedelta(days=30),
            },
        )
    return review_id


def _findings_json(answer_id: str, material_id: str) -> str:
    return json.dumps(
        [
            {
                "findingId": "finding-1",
                "type": "abstraction",
                "severity": "medium",
                "diagnosis": "概括层级不足",
                "action": "合并同类事实",
                "attribution": "unknown",
                "answerEvidenceRefs": [
                    {
                        "answerVersionId": answer_id,
                        "segmentId": "answer-1",
                        "quote": "基层治理",
                        "quoteSha256": sha256("基层治理".encode()).hexdigest(),
                    }
                ],
                "materialEvidenceRefs": [
                    {
                        "materialBundleVersionId": material_id,
                        "materialId": "material-1",
                        "segmentId": "segment-1",
                        "quote": "基层治理材料。",
                        "quoteSha256": sha256("基层治理材料。".encode()).hexdigest(),
                    }
                ],
            }
        ],
        ensure_ascii=False,
    )


def _post(
    client: TestClient,
    auth: dict[str, str],
    key: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response = client.post(
        "/api/v2/essay-intelligence/artifacts",
        headers={**auth, "Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _essay_stage(
    factory: sessionmaker[Session],
    service: PlanningService,
    user_id: str,
) -> str:
    with factory.begin() as session:
        plan = service.get_or_create_today(
            session,
            user_id=user_id,
            now=NOW,
            local_date=NOW.date(),
        )
    return next(item.stage for item in plan.tasks if item.track == "essay")


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    return TestClient(app)
