from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.domains.auth.deps import get_current_user
from app.domains.auth.repository import AuthenticatedUser
from app.domains.community_method_cards.deps import get_community_method_card_service
from app.domains.community_method_cards.schemas import (
    CandidateStatus,
    DelayedVerificationReceipt,
    DraftReceipt,
)
from app.main import create_app


class FakeCommunityMethodCardService:
    def __init__(self) -> None:
        self.draft_calls: list[tuple[str, object]] = []
        self.delayed_calls: list[tuple[str, object]] = []

    def create_draft(self, *, author_user_id: str, command: object) -> DraftReceipt:
        self.draft_calls.append((author_user_id, command))
        return DraftReceipt(
            cardId=str(uuid4()),
            versionId=str(uuid4()),
            candidateStatus=CandidateStatus.READY,
        )

    def record_delayed_verification(
        self,
        *,
        learner_user_id: str,
        command: object,
    ) -> DelayedVerificationReceipt:
        self.delayed_calls.append((learner_user_id, command))
        return DelayedVerificationReceipt(
            verificationId=str(uuid4()),
            recorded=True,
        )


def test_community_routes_require_authentication() -> None:
    client = TestClient(create_app())

    assert client.post(
        "/api/v2/community-method-cards/discovery",
        json={"track": "xingce", "applicability": ["资料分析"]},
    ).status_code == 401
    assert client.get("/api/v2/community-rewards/ledger").status_code == 401


def test_create_draft_uses_authenticated_author_and_public_envelope() -> None:
    service = FakeCommunityMethodCardService()
    user_id = str(uuid4())
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        learner_key=user_id,
        display_name=None,
        email=None,
    )
    app.dependency_overrides[get_community_method_card_service] = lambda: service

    response = TestClient(app).post(
        "/api/v2/community-method-cards/drafts",
        json={
            "sourceType": "text",
            "sourceText": "先看增长量，再对比基期量。",
            "track": "xingce",
            "applicability": ["资料分析"],
            "idempotencyKey": "draft-route-1",
        },
        headers={"X-Request-ID": "community-route-test"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "community-route-test"
    assert response.json()["requestId"] == "community-route-test"
    assert response.json()["data"]["candidateStatus"] == "ready"
    assert service.draft_calls[0][0] == user_id


def test_version_bound_sources_and_trial_outcomes_cannot_be_forged_by_client() -> None:
    service = FakeCommunityMethodCardService()
    user_id = str(uuid4())
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        learner_key=user_id,
        display_name=None,
        email=None,
    )
    app.dependency_overrides[get_community_method_card_service] = lambda: service
    client = TestClient(app)

    voice = client.post(
        "/api/v2/community-method-cards/drafts",
        json={
            "sourceType": "voice_transcript",
            "sourceText": "我先排除过度绝对的选项。",
            "track": "xingce",
            "applicability": ["言语理解"],
            "sourceArtifactId": str(uuid4()),
            "idempotencyKey": "voice-route-1",
        },
    )
    assert voice.status_code == 422

    trial_id = uuid4()
    forged = client.post(
        f"/api/v2/community-method-cards/trials/{trial_id}/delayed-verifications",
        json={
            "trialId": str(trial_id),
            "questionAttemptId": "attempt-2",
            "questionVersionId": "question-version-2",
            "correctAfter": True,
            "durationAfterMs": 18000,
            "deviceClusterHash": "client-controlled",
            "evidenceRefs": ["client-controlled"],
        },
    )
    assert forged.status_code == 422
    assert service.delayed_calls == []


def test_community_openapi_exposes_stage35_contract_without_social_fields() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "/api/v2/community-method-cards/drafts" in paths
    assert "/api/v2/community-method-cards/versions/{card_version_id}" in paths
    assert "/api/v2/community-method-cards/trials/{trial_id}/delayed-verifications" in paths
    assert "/api/v2/community-rewards/ledger" in paths

    serialized = str(schema).casefold()
    for forbidden in (
        "publiccomment",
        "commenttext",
        "replytext",
        "directmessage",
        "externalurl",
    ):
        assert forbidden not in serialized
