from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_db_session
from app.domains.auth.deps import get_wechat_identity_exchange
from app.domains.auth.wechat import WechatIdentityClaims
from app.main import create_app
from tests.position_release_helpers import seed_active_position_release


class _WechatExchange:
    def exchange(self, *, code: str) -> WechatIdentityClaims:
        return WechatIdentityClaims(
            app_id="wx-retake-contract",
            open_id="retake-openid",
            union_id="retake-unionid",
            session_key_hash=sha256(code.encode("utf-8")).hexdigest(),
        )


def test_retake_learning_owns_multi_year_loss_recovery_contract(
    db_session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    _seed_active_release(db_session_factory, now=now)
    client = _client(db_session_factory)
    login = client.post(
        "/api/v2/auth/wechat",
        json={"code": "retake-login", "deviceId": "retake-device"},
    )
    assert login.status_code == 200
    auth = login.json()["data"]
    headers = {"Authorization": f"Bearer {auth['accessToken']}"}
    user_id = auth["user"]["userId"]

    declaration = client.put(
        "/api/v2/onboarding/preparation-experience",
        headers={**headers, "Idempotency-Key": "retake-experience-0001"},
        json={
            "expectedExperienceVersion": 0,
            "expectedProgressVersion": 1,
            "firstSystematic": False,
            "externalLearning": True,
            "priorGuokaoExam": True,
            "retake": True,
            "priorExamYears": [2024],
        },
    )
    assert declaration.status_code == 200
    assert declaration.json()["data"]["experience"]["recommendedEntry"] == "retake_loss_recovery"

    official_evidence_id = _seed_score_evidence(
        db_session_factory,
        user_id=user_id,
        exam_year=2024,
        source_type="self_report",
        reported_context="official_exam",
        xingce_score=61,
        essay_score=54,
        now=now,
    )
    _seed_score_evidence(
        db_session_factory,
        user_id=user_id,
        exam_year=2025,
        source_type="external_mock",
        reported_context=None,
        xingce_score=72,
        essay_score=66,
        now=now,
    )

    workspace = client.get("/api/v2/retake", headers=headers)
    assert workspace.status_code == 200
    workspace_data = workspace.json()["data"]
    assert workspace_data["targetExamYear"] == 2026
    assert [option["evidenceId"] for option in workspace_data["eligibleScoreEvidence"]] == [
        official_evidence_id
    ]
    assert "不参与退款" in workspace_data["scoreUseBoundary"]

    created = client.post(
        "/api/v2/retake/programs",
        headers={**headers, "Idempotency-Key": "retake-program-0001"},
        json={"sourceScoreEvidenceId": official_evidence_id},
    )
    assert created.status_code == 200
    program = created.json()["data"]["program"]
    assert program["sourceExamYear"] == 2024
    assert program["targetExamYear"] == 2026
    assert program["refundUsesScore"] is False
    assert program["rewardUsesScore"] is False
    assert program["trainingUsesScore"] is True
    assert any("行测六模块" in focus for focus in program["lossStructure"]["trainingFocus"])

    replay = client.post(
        "/api/v2/retake/programs",
        headers={**headers, "Idempotency-Key": "retake-program-0001"},
        json={"sourceScoreEvidenceId": official_evidence_id},
    )
    assert replay.status_code == 200
    assert replay.json()["data"] == created.json()["data"]

    reused = client.post(
        "/api/v2/retake/programs",
        headers={**headers, "Idempotency-Key": "retake-program-0001"},
        json={"sourceScoreEvidenceId": str(uuid4())},
    )
    assert reused.status_code == 409
    assert reused.json()["code"] == "idempotency_key_reused"

    retired_commerce_owner = client.post(
        "/api/v2/commerce/retake-programs",
        headers={**headers, "Idempotency-Key": "old-commerce-owner"},
        json={"sourceScoreEvidenceId": official_evidence_id},
    )
    assert retired_commerce_owner.status_code == 404


def test_retake_workspace_requires_an_explicit_retake_declaration(
    db_session_factory: sessionmaker[Session],
) -> None:
    _seed_active_release(db_session_factory, now=datetime.now(UTC))
    client = _client(db_session_factory)
    auth = client.post(
        "/api/v2/auth/wechat",
        json={"code": "undeclared-login", "deviceId": "undeclared-device"},
    ).json()["data"]
    response = client.get(
        "/api/v2/retake",
        headers={"Authorization": f"Bearer {auth['accessToken']}"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "retake_experience_required"


def _client(factory: sessionmaker[Session]) -> TestClient:
    app = create_app()

    def override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    app.dependency_overrides[get_wechat_identity_exchange] = lambda: _WechatExchange()
    return TestClient(app)


def _seed_active_release(
    factory: sessionmaker[Session],
    *,
    now: datetime,
) -> None:
    with factory.begin() as session:
        seed_active_position_release(
            session,
            activated_at=now,
            database_now=now,
            rule_set_version="guokao-2026-retake",
            position_data_version="guokao-2026-retake-data",
        )


def _seed_score_evidence(
    factory: sessionmaker[Session],
    *,
    user_id: str,
    exam_year: int,
    source_type: str,
    reported_context: str | None,
    xingce_score: float,
    essay_score: float,
    now: datetime,
) -> str:
    evidence_id = str(uuid4())
    with factory.begin() as session:
        session.execute(
            text(
                """
                insert into learning.score_evidence (
                  evidence_id, command_id, user_id, exam_code, exam_year,
                  source_type, reported_context, xingce_score, shenlun_score,
                  total_score, timed_status, verification_status, occurred_at,
                  command_payload
                ) values (
                  cast(:evidence_id as uuid), :command_id,
                  cast(:user_id as uuid), 'guokao', :exam_year,
                  :source_type, :reported_context, :xingce_score, :essay_score,
                  :total_score, 'timed', 'user_confirmed', :occurred_at,
                  '{}'::jsonb
                )
                """
            ),
            {
                "evidence_id": evidence_id,
                "command_id": f"retake-evidence-{uuid4().hex}",
                "user_id": user_id,
                "exam_year": exam_year,
                "source_type": source_type,
                "reported_context": reported_context,
                "xingce_score": xingce_score,
                "essay_score": essay_score,
                "total_score": xingce_score + essay_score,
                "occurred_at": now,
            },
        )
    return evidence_id
