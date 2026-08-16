from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.payment import FakePaymentGateway


@pytest.fixture
def system(tmp_path):
    gateway = FakePaymentGateway()
    settings = Settings(
        database_path=str(tmp_path / "qbank.sqlite3"),
        catalog_mode="demo",
        api_key_secret="test-secret-with-more-than-thirty-two-characters",
        rate_limit_per_minute=60,
    )
    app = create_app(settings, gateway=gateway)
    return app, gateway, TestClient(app)


def paid_key(system, *, plan="month", request_id="order-request-0001"):
    app, gateway, client = system
    first = client.post("/v1/membership/invoke", json={"plan": plan, "requestId": request_id})
    assert first.status_code == 402
    assert "WeixinPay-Required" in first.headers
    out_trade_no = first.headers["X-Out-Trade-No"]
    gateway.mark_paid(out_trade_no)
    fulfilled = client.post(
        "/v1/membership/invoke",
        headers={"X-Out-Trade-No": out_trade_no},
        json={"plan": plan, "requestId": request_id},
    )
    assert fulfilled.status_code == 200
    return fulfilled.json()["apiKey"], out_trade_no


def test_happy_paid_mock_flow(system):
    _, _, client = system
    api_key, _ = paid_key(system)
    headers = {"Authorization": f"Bearer {api_key}"}
    created = client.post(
        "/v1/sessions",
        headers=headers,
        json={"requestId": "mock-request-0001", "kind": "mock", "exam": "guokao"},
    )
    assert created.status_code == 200
    session = created.json()
    assert session["totalQuestions"] == 130
    assert session["templateId"] == "guokao-demo-complete-v1"

    first_page = client.get(
        f"/v1/sessions/{session['sessionId']}/questions?offset=0&limit=20", headers=headers
    )
    assert first_page.status_code == 200
    assert len(first_page.json()["questions"]) == 20
    assert "answer" not in first_page.json()["questions"][0]
    assert "explanation" not in first_page.json()["questions"][0]

    answers = [
        {"questionId": f"demo-guokao-{index:03d}", "answer": "ABCD"[(index - 1) % 4]}
        for index in range(1, 131)
    ]
    submitted = client.post(
        f"/v1/sessions/{session['sessionId']}/submit",
        headers=headers,
        json={"requestId": "submit-request-01", "answers": answers},
    )
    assert submitted.status_code == 200
    assert submitted.json()["correctCount"] == 130
    assert submitted.json()["results"][0]["explanation"]


def test_failure_unpaid_and_unlicensed_fail_closed(system, tmp_path):
    _, gateway, client = system
    order = client.post(
        "/v1/membership/invoke", json={"plan": "week", "requestId": "unpaid-request-01"}
    )
    retry = client.post(
        "/v1/membership/invoke",
        headers={"X-Out-Trade-No": order.headers["X-Out-Trade-No"]},
        json={"plan": "week", "requestId": "unpaid-request-01"},
    )
    assert retry.status_code == 402
    assert retry.json()["code"] == "PAYMENT_PENDING"

    api_key, _ = paid_key(system, request_id="paid-for-prod-01")
    production_app = create_app(
        Settings(
            database_path=str(tmp_path / "production.sqlite3"),
            catalog_mode="licensed_production",
            api_key_secret="production-test-secret-over-thirty-two-chars",
        ),
        gateway=gateway,
    )
    production_service = production_app.state.service
    # Import the already paid identity solely for the catalog fail-closed assertion.
    production_service.store = system[0].state.service.store
    production_client = TestClient(production_app)
    blocked = production_client.post(
        "/v1/sessions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"requestId": "prod-catalog-001", "kind": "practice", "exam": "guokao", "questionCount": 10},
    )
    assert blocked.status_code == 503
    assert blocked.json()["code"] == "CATALOG_UNAVAILABLE"


def test_replay_fulfillment_is_idempotent(system):
    _, gateway, client = system
    first = client.post(
        "/v1/membership/invoke", json={"plan": "month", "requestId": "replay-order-001"}
    )
    out_trade_no = first.headers["X-Out-Trade-No"]
    gateway.mark_paid(out_trade_no)
    headers = {"X-Out-Trade-No": out_trade_no}
    one = client.post("/v1/membership/invoke", headers=headers, json={"plan": "month", "requestId": "replay-order-001"})
    two = client.post("/v1/membership/invoke", headers=headers, json={"plan": "month", "requestId": "replay-order-001"})
    assert one.json()["apiKey"] == two.json()["apiKey"]
    assert one.json()["expiresAt"] == two.json()["expiresAt"]
    assert two.json()["alreadyFulfilled"] is True

    auth = {"Authorization": f"Bearer {one.json()['apiKey']}"}
    payload = {"requestId": "session-replay-1", "kind": "practice", "exam": "sydw", "questionCount": 10}
    session_one = client.post("/v1/sessions", headers=auth, json=payload).json()
    session_two = client.post("/v1/sessions", headers=auth, json=payload).json()
    assert session_one["sessionId"] == session_two["sessionId"]


def test_stale_or_expired_access_is_rejected(system):
    app, _, client = system
    api_key, _ = paid_key(system, plan="week", request_id="expiring-order-1")
    with app.state.service.store.connect(immediate=True) as connection:
        connection.execute(
            "UPDATE entitlements SET expires_at=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )
    response = client.post(
        "/v1/sessions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"requestId": "expired-session-1", "kind": "practice", "exam": "guokao", "questionCount": 5},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ENTITLEMENT_EXPIRED"


def test_race_fulfillment_creates_one_entitlement(system):
    app, gateway, client = system
    first = client.post(
        "/v1/membership/invoke", json={"plan": "month", "requestId": "race-order-0001"}
    )
    out_trade_no = first.headers["X-Out-Trade-No"]
    gateway.mark_paid(out_trade_no)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: app.state.service.fulfill_order(out_trade_no), range(4)))
    assert len({item["apiKey"] for item in results}) == 1
    with app.state.service.store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM entitlements").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 1


def test_recovery_after_pending_payment(system):
    _, gateway, client = system
    first = client.post(
        "/v1/membership/invoke", json={"plan": "week", "requestId": "recover-order-01"}
    )
    out_trade_no = first.headers["X-Out-Trade-No"]
    pending = client.post(
        "/v1/membership/invoke",
        headers={"X-Out-Trade-No": out_trade_no},
        json={"plan": "week", "requestId": "recover-order-01"},
    )
    assert pending.status_code == 402
    gateway.mark_paid(out_trade_no)
    recovered = client.post(
        "/v1/membership/invoke",
        headers={"X-Out-Trade-No": out_trade_no},
        json={"plan": "week", "requestId": "recover-order-01"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["alreadyFulfilled"] is False
