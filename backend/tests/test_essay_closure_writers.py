from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_essay_learning_artifacts import (
    _client,
    _content_ids,
    _insert_completed_review,
    _seed_answer_without_review,
)
from tests.test_planning_routes import _seed_learner_and_published_essay_content


def test_rewrite_and_fresh_transfer_are_atomic_owned_and_replayable(
    db_session_factory: sessionmaker[Session],
) -> None:
    token = "essay-closure-writer-token"
    user_id = _seed_learner_and_published_essay_content(
        db_session_factory,
        access_token=token,
    )
    ids = _content_ids(db_session_factory)
    base_answer_id, attempt_id = _seed_answer_without_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
    )
    review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=ids,
        answer_id=base_answer_id,
    )
    client = _client(db_session_factory)
    headers = {"Authorization": f"Bearer {token}"}
    source_decision = _post_decision(
        client,
        headers,
        key="essay-source-decision",
        ids=ids,
        review_id=review_id,
        decision="accepted",
    )
    rewrite_command = {
        "attemptId": attempt_id,
        "baseReviewVersionId": review_id,
        "baseAnswerVersionId": base_answer_id,
        "findingDecisionArtifactVersionId": source_decision,
        "basedOnAnswerVersionId": base_answer_id,
        "basedOnRewriteVersionId": None,
        "sourceType": "typed",
        "answerText": "基层治理需要归纳共性问题，并明确责任和协同机制。",
        "thinking": None,
        "resolutions": [
            {
                "findingId": "finding-1",
                "status": "resolved",
                "learnerEvidence": "将零散事实归并为责任与协同两类上位概念",
            }
        ],
    }
    rewrite_response = client.post(
        "/api/v2/essay-intelligence/rewrites",
        headers={**headers, "Idempotency-Key": "essay-rewrite-writer-0001"},
        json=rewrite_command,
    )
    assert rewrite_response.status_code == 201, rewrite_response.text
    rewrite = rewrite_response.json()["data"]
    assert rewrite["rewrite"]["contractVersion"] == "essay-rewrite-writer@v2"
    assert rewrite["rewrite"]["status"] == "passed"
    assert rewrite["answer"]["answerVersionId"] != base_answer_id
    assert {item["state"] for item in rewrite["masteryVersions"]} == {
        "transfer_due"
    }
    assert all(
        item["state"] != "stable_mastered" for item in rewrite["masteryVersions"]
    )
    replay = client.post(
        "/api/v2/essay-intelligence/rewrites",
        headers={**headers, "Idempotency-Key": "essay-rewrite-writer-0001"},
        json=rewrite_command,
    )
    assert replay.status_code == 201
    assert replay.json()["data"] == rewrite
    idempotency_conflict = client.post(
        "/api/v2/essay-intelligence/rewrites",
        headers={**headers, "Idempotency-Key": "essay-rewrite-writer-0001"},
        json={**rewrite_command, "answerText": "同一幂等键下的其他内容"},
    )
    assert idempotency_conflict.status_code == 409
    stale_cas = client.post(
        "/api/v2/essay-intelligence/rewrites",
        headers={**headers, "Idempotency-Key": "essay-rewrite-writer-stale"},
        json={
            **rewrite_command,
            "basedOnAnswerVersionId": rewrite["answer"]["answerVersionId"],
            "answerText": "试图跳过当前复写版本。",
        },
    )
    assert stale_cas.status_code == 409

    fresh_ids = _insert_fresh_content(db_session_factory)
    transfer_answer_id, transfer_attempt_id = _seed_fresh_answer(
        db_session_factory,
        user_id=user_id,
        ids=fresh_ids,
    )
    transfer_review_id = _insert_completed_review(
        db_session_factory,
        user_id=user_id,
        ids=fresh_ids,
        answer_id=transfer_answer_id,
    )
    transfer_decision = _post_decision(
        client,
        headers,
        key="essay-transfer-decision",
        ids=fresh_ids,
        review_id=transfer_review_id,
        decision="rejected",
    )
    transfer_command = {
        "sourceRewriteVersionId": rewrite["rewrite"]["rewriteVersionId"],
        "sourceFindingDecisionArtifactVersionId": source_decision,
        "transferAttemptId": transfer_attempt_id,
        "transferAnswerVersionId": transfer_answer_id,
        "transferReviewVersionId": transfer_review_id,
        "transferFindingDecisionArtifactVersionId": transfer_decision,
        "basedOnTransferVerificationVersionId": None,
        "testedCriterionCodes": ["point_coverage"],
    }
    transfer_response = client.post(
        "/api/v2/essay-intelligence/transfers",
        headers={**headers, "Idempotency-Key": "essay-transfer-writer-0001"},
        json=transfer_command,
    )
    assert transfer_response.status_code == 201, transfer_response.text
    transfer = transfer_response.json()["data"]
    assert transfer["transfer"]["contractVersion"] == "essay-transfer-writer@v2"
    assert transfer["transfer"]["status"] == "verified"
    assert {item["state"] for item in transfer["masteryVersions"]} == {
        "stable_mastered"
    }
    transfer_replay = client.post(
        "/api/v2/essay-intelligence/transfers",
        headers={**headers, "Idempotency-Key": "essay-transfer-writer-0001"},
        json=transfer_command,
    )
    assert transfer_replay.status_code == 201
    assert transfer_replay.json()["data"] == transfer
    projection = client.get(
        "/api/v2/essay-intelligence/mastery",
        headers=headers,
    )
    assert projection.status_code == 200
    assert projection.json()["data"]["stableCount"] == 1

    with db_session_factory() as session:
        base_answer = session.execute(
            text(
                "select answer_text from learning.essay_answer_versions "
                "where answer_version_id = cast(:answer_id as uuid)"
            ),
            {"answer_id": base_answer_id},
        ).scalar_one()
        assert base_answer == "基层治理存在执行问题。"
        assert session.execute(
            text(
                "select count(*) from learning.essay_rewrite_versions "
                "where writer_contract_version = 'essay-rewrite-writer@v2'"
            )
        ).scalar_one() == 1
        assert session.execute(
            text(
                "select count(*) from learning.essay_transfer_verification_versions "
                "where writer_contract_version = 'essay-transfer-writer@v2'"
            )
        ).scalar_one() == 1


def _post_decision(
    client: object,
    headers: dict[str, str],
    *,
    key: str,
    ids: dict[str, str],
    review_id: str,
    decision: str,
) -> str:
    payload: dict[str, object] = {
        "findingId": "finding-1",
        "decision": decision,
    }
    if decision != "accepted":
        payload["learnerReason"] = "迁移作答中已使用上位概念并有原文依据"
    response = client.post(  # type: ignore[attr-defined]
        "/api/v2/essay-intelligence/artifacts",
        headers={**headers, "Idempotency-Key": key},
        json={
            "artifactType": "review_acknowledgement",
            "taskVersionId": ids["task"],
            "materialBundleVersionId": ids["material"],
            "rubricVersionId": ids["rubric"],
            "reviewVersionId": review_id,
            "answerVersionId": None,
            "basedOnArtifactVersionId": None,
            "payload": {
                "schemaType": "review_acknowledgement",
                "contractVersion": "essay-finding-decision@v1",
                "decisions": [payload],
                "priorityFindingIds": (
                    ["finding-1"] if decision == "accepted" else []
                ),
                "learnerUnderstanding": "逐条核对批改意见与证据",
            },
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["artifactVersionId"])


def _insert_fresh_content(
    factory: sessionmaker[Session],
) -> dict[str, str]:
    ids = {
        "task": str(uuid4()),
        "material": str(uuid4()),
        "rubric": str(uuid4()),
    }
    task_key = f"guokao-transfer-{uuid4()}"
    ids["task_key"] = task_key
    prompt = "根据另一份资料概括协同治理问题。"
    material_text = "多部门协作中存在信息壁垒。"
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into product.essay_task_versions (
                  task_version_id, task_key, version_no, question_type, prompt,
                  prompt_sha256, word_limit, maximum_score, state
                ) values (
                  cast(:task as uuid), :task_key, 1, 'summary', :prompt,
                  :prompt_sha, 200, 20, 'published'
                )
                """
            ),
            {
                "task": ids["task"],
                "task_key": task_key,
                "prompt": prompt,
                "prompt_sha": sha256(prompt.encode()).hexdigest(),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_bundle_versions (
                  material_bundle_version_id, task_version_id, task_key,
                  version_no, completeness, source_kind, content_sha256
                ) values (
                  cast(:material as uuid), cast(:task as uuid), :task_key,
                  1, 'complete', 'own_bank', :content_sha
                )
                """
            ),
            {
                **ids,
                "task_key": task_key,
                "content_sha": sha256(material_text.encode()).hexdigest(),
            },
        )
        session.execute(
            text(
                """
                insert into product.essay_material_segments (
                  material_segment_id, material_bundle_version_id, material_id,
                  segment_id, material_order, segment_order, text, text_sha256
                ) values (
                  cast(:row_id as uuid), cast(:material as uuid), 'material-1',
                  'segment-1', 1, 1, :material_text, :text_sha
                )
                """
            ),
            {
                "row_id": str(uuid4()),
                "material": ids["material"],
                "material_text": material_text,
                "text_sha": sha256(material_text.encode()).hexdigest(),
            },
        )
        criteria = [
            {
                "criterionCode": "point_coverage",
                "label": "要点覆盖",
                "maximum": 20,
                "scoringRule": "按材料要点覆盖评分",
                "materialEvidenceRequired": True,
            }
        ]
        points = [
            {
                "rubricPointId": "point-1",
                "criterionCode": "point_coverage",
                "category": "problem",
                "label": "概括协同问题",
                "maximumCredit": 20,
                "materialSegmentIds": ["segment-1"],
                "materialEvidenceRefs": [
                    {
                        "materialBundleVersionId": ids["material"],
                        "materialId": "material-1",
                        "segmentId": "segment-1",
                        "startOffset": 0,
                        "endOffset": 4,
                        "quote": "多部门协",
                        "quoteSha256": sha256("多部门协".encode()).hexdigest(),
                    }
                ],
            }
        ]
        session.execute(
            text(
                """
                insert into product.essay_rubric_versions (
                  rubric_version_id, task_version_id, task_key, version_no,
                  material_bundle_version_id, compilation_input_fingerprint,
                  source_kind, verification_status, state, total_maximum,
                  criteria, points, content_sha256, compilation_audit
                ) values (
                  cast(:rubric as uuid), cast(:task as uuid), :task_key, 1,
                  cast(:material as uuid), :compile_fp, 'system_compiled',
                  'unverified', 'published', 20, cast(:criteria as jsonb),
                  cast(:points as jsonb), :rubric_sha, cast(:audit as jsonb)
                )
                """
            ),
            {
                **ids,
                "task_key": task_key,
                "compile_fp": sha256(task_key.encode()).hexdigest(),
                "criteria": json.dumps(criteria, ensure_ascii=False),
                "points": json.dumps(points, ensure_ascii=False),
                "rubric_sha": "d" * 64,
                "audit": json.dumps(
                    {
                        "contractVersion": "guokao-essay-rubric-v1",
                        "compilerVersion": "test-compiler-v1",
                        "promptVersion": "test-prompt-v1",
                        "provider": "test-provider",
                        "model": "test-model",
                        "inputFingerprint": sha256(task_key.encode()).hexdigest(),
                        "promptSha256": sha256(prompt.encode()).hexdigest(),
                        "materialContentSha256": sha256(
                            material_text.encode()
                        ).hexdigest(),
                    },
                    ensure_ascii=False,
                ),
            },
        )
    return ids


def _seed_fresh_answer(
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
                  cast(:attempt_id as uuid), :attempt_command,
                  cast(:user_id as uuid), cast(:task_id as uuid),
                  :task_key, cast(:material_id as uuid),
                  cast(:rubric_id as uuid), 'in_program', 'untimed',
                  :attempt_sha, :now
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
                "task_key": ids["task_key"],
                "attempt_sha": sha256(attempt_id.encode()).hexdigest(),
                "now": datetime(2026, 7, 16, 8, tzinfo=UTC),
            },
        )
        answer_text = "多部门协作需要打破信息壁垒并明确责任。"
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
                  cast(:task_id as uuid), 1, 'typed', :answer_text,
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
                "answer_text": answer_text,
                "answer_sha": sha256(answer_text.encode()).hexdigest(),
                "input_sha": sha256(answer_id.encode()).hexdigest(),
            },
        )
    return answer_id, attempt_id
