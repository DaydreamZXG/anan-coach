from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.test_ability_estimation import _client, _seed_user_and_release


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def test_external_report_requires_confirmed_printed_ocr_and_separate_field_confirmation(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(
        db_session_factory, token="stage6-external-boundary-token"
    )
    artifact_id, candidate_revision_id = _seed_printed_report_recognition(
        db_session_factory, user_id=user_id, state="candidate"
    )
    client = _client(db_session_factory)
    auth = {"Authorization": "Bearer stage6-external-boundary-token"}
    draft_command = _draft_command(
        artifact_id=artifact_id,
        recognition_revision_id=candidate_revision_id,
    )

    rejected = client.post(
        "/api/v2/reporting/external-mocks",
        headers={**auth, "Idempotency-Key": "stage6-draft-unconfirmed-0001"},
        json=draft_command,
    )
    assert rejected.status_code == 409, rejected.text

    confirmed_revision_id = _add_confirmed_revision(
        db_session_factory, user_id=user_id, artifact_id=artifact_id
    )
    draft_command["recognitionRevisionId"] = confirmed_revision_id
    candidate = client.post(
        "/api/v2/reporting/external-mocks",
        headers={**auth, "Idempotency-Key": "stage6-draft-confirmed-0001"},
        json=draft_command,
    )
    assert candidate.status_code == 200, candidate.text
    report = candidate.json()["data"]
    assert report["state"] == "candidate"
    assert report["evidenceGrade"] == "candidate_unconfirmed"
    assert report["abilityImpact"] == "blocked_until_confirmed"
    assert report["scoreEvidenceId"] is None
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.score_evidence "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 0
        assert session.execute(
            text(
                "select count(*) from learning.ability_estimate_versions "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 0


def test_confirmed_timed_external_report_enters_ability_once_and_duplicate_is_replayed(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(
        db_session_factory, token="stage6-external-confirm-token"
    )
    artifact_id, revision_id = _seed_printed_report_recognition(
        db_session_factory, user_id=user_id, state="confirmed"
    )
    client = _client(db_session_factory)
    auth = {"Authorization": "Bearer stage6-external-confirm-token"}
    candidate = client.post(
        "/api/v2/reporting/external-mocks",
        headers={**auth, "Idempotency-Key": "stage6-draft-eligible-0001"},
        json=_draft_command(
            artifact_id=artifact_id,
            recognition_revision_id=revision_id,
        ),
    )
    assert candidate.status_code == 200, candidate.text
    candidate_id = candidate.json()["data"]["reportVersionId"]
    confirmed = client.post(
        f"/api/v2/reporting/external-mocks/{candidate_id}/confirm",
        headers={**auth, "Idempotency-Key": "stage6-confirm-eligible-0001"},
        json={"expectedReportVersionId": candidate_id},
    )
    assert confirmed.status_code == 200, confirmed.text
    report = confirmed.json()["data"]
    assert report["state"] == "confirmed"
    assert report["evidenceGrade"] == "ability_eligible"
    assert report["abilityImpact"] == "entered_estimate"
    assert report["scoreEvidenceId"]
    assert report["estimateVersionId"]

    second_revision_id = _add_confirmed_revision(
        db_session_factory, user_id=user_id, artifact_id=artifact_id
    )
    duplicate_candidate = client.post(
        "/api/v2/reporting/external-mocks",
        headers={**auth, "Idempotency-Key": "stage6-draft-duplicate-0001"},
        json=_draft_command(
            artifact_id=artifact_id,
            recognition_revision_id=second_revision_id,
        ),
    )
    assert duplicate_candidate.status_code == 200, duplicate_candidate.text
    duplicate_candidate_id = duplicate_candidate.json()["data"]["reportVersionId"]
    duplicate = client.post(
        f"/api/v2/reporting/external-mocks/{duplicate_candidate_id}/confirm",
        headers={**auth, "Idempotency-Key": "stage6-confirm-duplicate-0001"},
        json={"expectedReportVersionId": duplicate_candidate_id},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["data"]["reportVersionId"] == report["reportVersionId"]
    assert "未重复写入" in duplicate.json()["data"]["confirmationMessage"]

    with db_session_factory() as session:
        evidence = session.execute(
            text(
                """
                select source_type, verification_status, xingce_score,
                       shenlun_score, timed_status
                from learning.score_evidence
                where user_id = cast(:user_id as uuid)
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
        assert len(evidence) == 1
        assert dict(evidence[0]) == {
            "source_type": "external_mock",
            "verification_status": "user_confirmed",
            "xingce_score": 68,
            "shenlun_score": 62,
            "timed_status": "timed",
        }


def test_untimed_external_report_is_confirmed_for_trend_but_not_ability(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(
        db_session_factory, token="stage6-external-trend-token"
    )
    artifact_id, revision_id = _seed_printed_report_recognition(
        db_session_factory, user_id=user_id, state="confirmed"
    )
    client = _client(db_session_factory)
    auth = {"Authorization": "Bearer stage6-external-trend-token"}
    command = _draft_command(
        artifact_id=artifact_id,
        recognition_revision_id=revision_id,
    )
    command["timedStatus"] = "untimed"
    command["durationMinutes"] = None
    candidate = client.post(
        "/api/v2/reporting/external-mocks",
        headers={**auth, "Idempotency-Key": "stage6-draft-trend-0001"},
        json=command,
    )
    assert candidate.status_code == 200, candidate.text
    candidate_id = candidate.json()["data"]["reportVersionId"]
    confirmed = client.post(
        f"/api/v2/reporting/external-mocks/{candidate_id}/confirm",
        headers={**auth, "Idempotency-Key": "stage6-confirm-trend-0001"},
        json={"expectedReportVersionId": candidate_id},
    )
    assert confirmed.status_code == 200, confirmed.text
    report = confirmed.json()["data"]
    assert report["evidenceGrade"] == "trend_only_untimed"
    assert report["abilityImpact"] == "trend_only"
    assert report["scoreEvidenceId"] is None
    with db_session_factory() as session:
        assert session.execute(
            text(
                "select count(*) from learning.score_evidence "
                "where user_id = cast(:user_id as uuid)"
            ),
            {"user_id": user_id},
        ).scalar_one() == 0


def test_global_report_has_complete_chart_semantics_and_plan_evidence_refs(
    db_session_factory: sessionmaker[Session],
) -> None:
    user_id = _seed_user_and_release(
        db_session_factory, token="stage6-global-report-token"
    )
    artifact_id, revision_id = _seed_printed_report_recognition(
        db_session_factory, user_id=user_id, state="confirmed"
    )
    client = _client(db_session_factory)
    auth = {"Authorization": "Bearer stage6-global-report-token"}
    candidate = client.post(
        "/api/v2/reporting/external-mocks",
        headers={**auth, "Idempotency-Key": "stage6-global-draft-0001"},
        json=_draft_command(
            artifact_id=artifact_id,
            recognition_revision_id=revision_id,
        ),
    ).json()["data"]
    confirmed = client.post(
        f"/api/v2/reporting/external-mocks/{candidate['reportVersionId']}/confirm",
        headers={**auth, "Idempotency-Key": "stage6-global-confirm-0001"},
        json={"expectedReportVersionId": candidate["reportVersionId"]},
    )
    assert confirmed.status_code == 200, confirmed.text

    response = client.get("/api/v2/reporting/reports/global", headers=auth)
    assert response.status_code == 200, response.text
    report = response.json()["data"]
    assert report["chartContractVersion"] == "report-chart@v1"
    assert report["state"] == "partial"
    assert len(report["charts"]) == 6
    for chart in report["charts"]:
        assert chart["title"]
        assert chart["definition"]
        assert chart["period"]
        assert chart["unit"]
        assert chart["confidence"]
        assert chart["summary"]
        assert "evidenceRefs" in chart
        assert "partialReasons" in chart
    assert report["conclusion"]["nextPlanEvidenceRefs"] == [
        f"ability-estimate:{confirmed.json()['data']['estimateVersionId']}"
    ]
    with db_session_factory() as session:
        stored = session.execute(
            text(
                """
                select chart_contract_version, next_plan_evidence_refs
                from learning.report_projection_versions
                where user_id = cast(:user_id as uuid) and report_kind = 'global'
                """
            ),
            {"user_id": user_id},
        ).mappings().one()
        assert stored["chart_contract_version"] == "report-chart@v1"
        assert stored["next_plan_evidence_refs"] == report["conclusion"][
            "nextPlanEvidenceRefs"
        ]


def _draft_command(*, artifact_id: str, recognition_revision_id: str) -> dict[str, object]:
    return {
        "artifactVersionId": artifact_id,
        "recognitionRevisionId": recognition_revision_id,
        "platformName": "第三方模考平台",
        "examName": "2026 国考双科联考",
        "occurredAt": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "timedStatus": "timed",
        "durationMinutes": 300,
        "totalScore": 130,
        "xingceScore": 68,
        "shenlunScore": 62,
        "moduleScores": {"资料分析": 74, "言语理解与表达": 65},
    }


def _seed_printed_report_recognition(
    factory: sessionmaker[Session], *, user_id: str, state: str
) -> tuple[str, str]:
    artifact_version_id = str(uuid4())
    artifact_id = str(uuid4())
    recognition_revision_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.source_artifact_versions (
                  artifact_version_id, artifact_id, owner_user_id, version_no,
                  source_kind, state, object_ref, mime_type, content_sha256,
                  byte_size, page_count, supersedes_artifact_version_id,
                  contract_payload, created_at
                ) values (
                  cast(:artifact_version_id as uuid), cast(:artifact_id as uuid),
                  cast(:user_id as uuid), 1, 'image_import', 'available',
                  :object_ref, 'image/png', :content_sha256, 2048, null, null,
                  cast(:contract_payload as jsonb), :created_at
                )
                """
            ),
            {
                "artifact_version_id": artifact_version_id,
                "artifact_id": artifact_id,
                "user_id": user_id,
                "object_ref": f"tos://test/private/reports/{artifact_version_id}.png",
                "content_sha256": sha256(artifact_version_id.encode()).hexdigest(),
                "contract_payload": json.dumps({"source": "stage6-test"}),
                "created_at": NOW,
            },
        )
        _insert_recognition(
            session,
            user_id=user_id,
            artifact_id=artifact_version_id,
            recognition_revision_id=recognition_revision_id,
            state=state,
            revision_no=1,
            predecessor_revision_id=None,
        )
    return artifact_version_id, recognition_revision_id


def _add_confirmed_revision(
    factory: sessionmaker[Session], *, user_id: str, artifact_id: str
) -> str:
    recognition_revision_id = str(uuid4())
    with factory.begin() as session:
        predecessor = session.execute(
            text(
                """
                select recognition_revision_id::text, revision_no
                from learning.recognition_revisions
                where artifact_version_id = cast(:artifact_id as uuid)
                  and recognition_kind = 'printed_ocr'
                order by revision_no desc
                limit 1
                """
            ),
            {"artifact_id": artifact_id},
        ).mappings().one()
        _insert_recognition(
            session,
            user_id=user_id,
            artifact_id=artifact_id,
            recognition_revision_id=recognition_revision_id,
            state="confirmed",
            revision_no=int(predecessor["revision_no"]) + 1,
            predecessor_revision_id=str(predecessor["recognition_revision_id"]),
        )
    return recognition_revision_id


def _insert_recognition(
    session: Session,
    *,
    user_id: str,
    artifact_id: str,
    recognition_revision_id: str,
    state: str,
    revision_no: int,
    predecessor_revision_id: str | None,
) -> None:
    payload = {
        "schemaVersion": "recognition-candidate-payload@v1",
        "kind": "printed_ocr",
        "textSpans": [
            {
                "spanId": "score-line",
                "text": "行测 68 申论 62 总分 130",
                "box": {
                    "pageNo": 1,
                    "x": 0.05,
                    "y": 0.1,
                    "width": 0.9,
                    "height": 0.2,
                },
                "confidence": 0.93,
            }
        ],
        "segments": [],
        "omrMarks": [],
        "pageImageSha256": ["1" * 64],
        "visualFingerprints": ["2" * 64],
        "processorRequestIdHash": "3" * 64,
        "processorOutputContract": "recognition-processor-output@v1",
        "requestHash": "4" * 64,
        "predecessorRevisionId": predecessor_revision_id,
        "correctionReason": "测试确认修订" if predecessor_revision_id else None,
    }
    output_sha = sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    session.execute(
        text(
            """
            insert into learning.recognition_revisions (
              recognition_revision_id, artifact_version_id, owner_user_id,
              revision_no, recognition_kind, state, processor_policy_version,
              processor_route, input_sha256, output_sha256, candidate_payload,
              confidence, expires_at, confirmed_at, created_at,
              supersedes_recognition_revision_id
            ) values (
              cast(:revision_id as uuid), cast(:artifact_id as uuid),
              cast(:user_id as uuid), :revision_no, 'printed_ocr', :state,
              'stage6-test-policy', 'stage6-score-report-route',
              :input_sha, :output_sha, cast(:payload as jsonb), 0.93,
              :expires_at, :confirmed_at, :created_at,
              cast(:predecessor_revision_id as uuid)
            )
            """
        ),
        {
            "revision_id": recognition_revision_id,
            "artifact_id": artifact_id,
            "user_id": user_id,
            "state": state,
            "revision_no": revision_no,
            "predecessor_revision_id": predecessor_revision_id,
            "input_sha": "5" * 64,
            "output_sha": output_sha,
            "payload": json.dumps(payload, ensure_ascii=False),
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "confirmed_at": datetime.now(UTC) if state == "confirmed" else None,
            "created_at": NOW,
        },
    )
