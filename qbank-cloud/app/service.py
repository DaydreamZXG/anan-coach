from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import hmac
import json
import secrets
import threading
import time

from .catalog import CatalogUnavailable, QuestionCatalog
from .payment import PaymentGateway
from .store import Store, utc_now


PLANS = {
    "week": {"amount_minor": 990, "days": 7},
    "month": {"amount_minor": 1990, "days": 30},
}


class ServiceError(RuntimeError):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code = code
        self.status = status


class SlidingWindowLimiter:
    def __init__(self, limit: int):
        self.limit = limit
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def take(self, key_id: str, now: float | None = None) -> None:
        current = now if now is not None else time.monotonic()
        with self._lock:
            events = self._events[key_id]
            while events and current - events[0] >= 60:
                events.popleft()
            if len(events) >= self.limit:
                raise ServiceError("RATE_LIMITED", 429, "API key exceeds the 60-second request window")
            events.append(current)


class QBankService:
    def __init__(
        self,
        *,
        store: Store,
        catalog: QuestionCatalog,
        gateway: PaymentGateway,
        api_key_secret: str,
        rate_limit: int = 60,
    ):
        self.store = store
        self.catalog = catalog
        self.gateway = gateway
        self.secret = api_key_secret.encode()
        self.limiter = SlidingWindowLimiter(rate_limit)

    @staticmethod
    def _fingerprint(value: object) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return sha256(raw).hexdigest()

    def start_order(self, plan: str, request_id: str) -> dict:
        if plan not in PLANS:
            raise ServiceError("INVALID_PLAN", 422, "plan must be week or month")
        fingerprint = self._fingerprint({"plan": plan, "requestId": request_id})
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM orders WHERE request_id = ?", (request_id,)
            ).fetchone()
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409, "requestId was used with another payload")
            return dict(existing)
        out_trade_no = f"QB{int(time.time()):010d}{secrets.token_hex(5)}"[:32]
        amount = PLANS[plan]["amount_minor"]
        payment = self.gateway.create_order(out_trade_no, amount, f"岸岸题库云{plan}会员")
        try:
            with self.store.connect(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (out_trade_no, request_id, fingerprint, plan, amount, "NOTPAY", payment.payment_code, utc_now()),
                )
        except Exception:
            with self.store.connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM orders WHERE request_id = ?", (request_id,)
                ).fetchone()
            if not existing or existing["request_fingerprint"] != fingerprint:
                raise
            return dict(existing)
        with self.store.connect() as connection:
            return dict(connection.execute("SELECT * FROM orders WHERE out_trade_no = ?", (out_trade_no,)).fetchone())

    def _derive_key(self, out_trade_no: str) -> str:
        digest = hmac.new(self.secret, f"api-key:{out_trade_no}".encode(), sha256).digest()
        return "qbk_" + base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def fulfill_order(self, out_trade_no: str) -> dict:
        with self.store.connect() as connection:
            order = connection.execute("SELECT * FROM orders WHERE out_trade_no = ?", (out_trade_no,)).fetchone()
        if not order:
            raise ServiceError("ORDER_NOT_FOUND", 404, "membership order not found")
        provider_state = self.gateway.query_order(out_trade_no)
        if provider_state != "SUCCESS":
            raise ServiceError("PAYMENT_PENDING", 402, f"provider state is {provider_state}")
        api_key = self._derive_key(out_trade_no)
        key_hash = sha256(api_key.encode()).hexdigest()
        already_fulfilled = False
        with self.store.connect(immediate=True) as connection:
            entitlement = connection.execute(
                "SELECT e.*, k.key_id FROM entitlements e JOIN api_keys k USING(entitlement_id) WHERE e.out_trade_no = ?",
                (out_trade_no,),
            ).fetchone()
            if entitlement:
                already_fulfilled = True
            else:
                start = datetime.now(timezone.utc)
                expires = start + timedelta(days=PLANS[order["plan"]]["days"])
                entitlement_id = "ent_" + secrets.token_hex(12)
                key_id = "key_" + secrets.token_hex(10)
                connection.execute(
                    "INSERT INTO entitlements VALUES (?, ?, ?, ?, ?, 'available')",
                    (entitlement_id, out_trade_no, order["plan"], start.isoformat(), expires.isoformat()),
                )
                connection.execute(
                    "INSERT INTO api_keys VALUES (?, ?, ?, ?, NULL)",
                    (key_id, entitlement_id, api_key[:12], key_hash),
                )
                connection.execute(
                    "UPDATE orders SET provider_state='SUCCESS', fulfilled_at=? WHERE out_trade_no=?",
                    (start.isoformat(), out_trade_no),
                )
                entitlement = connection.execute(
                    "SELECT e.*, k.key_id FROM entitlements e JOIN api_keys k USING(entitlement_id) WHERE e.out_trade_no = ?",
                    (out_trade_no,),
                ).fetchone()
        return {
            "code": "SUCCESS",
            "apiKey": api_key,
            "expiresAt": entitlement["expires_at"],
            "alreadyFulfilled": already_fulfilled,
        }

    def authenticate(self, api_key: str) -> dict:
        key_hash = sha256(api_key.encode()).hexdigest()
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT k.key_id, k.revoked_at, e.expires_at, e.state FROM api_keys k JOIN entitlements e USING(entitlement_id) WHERE k.key_hash = ?",
                (key_hash,),
            ).fetchone()
        if not row or row["revoked_at"] or row["state"] != "available":
            raise ServiceError("UNAUTHORIZED", 401, "invalid or revoked API key")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            raise ServiceError("ENTITLEMENT_EXPIRED", 403, "membership has expired")
        self.limiter.take(row["key_id"])
        return dict(row)

    def create_session(self, key_id: str, payload: dict) -> dict:
        kind, exam = payload["kind"], payload["exam"]
        request_id = payload["requestId"]
        fingerprint = self._fingerprint(payload)
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM sessions WHERE key_id=? AND request_id=?", (key_id, request_id)
            ).fetchone()
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise ServiceError("IDEMPOTENCY_CONFLICT", 409, "requestId was used with another payload")
            return self._session_summary(existing)
        try:
            if kind == "practice":
                questions = self.catalog.practice(exam, payload.get("questionCount") or 10)
                template_id = None
            elif kind == "mock":
                template_id, questions = self.catalog.mock(exam, payload.get("templateId"))
            else:
                raise ServiceError("INVALID_SHAPE", 422, "kind must be practice or mock")
        except ValueError as error:
            raise ServiceError("INVALID_SHAPE", 422, str(error)) from error
        except CatalogUnavailable as error:
            raise ServiceError("CATALOG_UNAVAILABLE", 503, str(error)) from error
        session_id = "ses_" + secrets.token_hex(12)
        with self.store.connect(immediate=True) as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
                (
                    session_id, key_id, request_id, fingerprint, kind, exam, template_id,
                    self.catalog.mode, json.dumps([q.stored() for q in questions], ensure_ascii=False), utc_now(),
                ),
            )
            row = connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return self._session_summary(row)

    @staticmethod
    def _session_summary(row) -> dict:
        return {
            "sessionId": row["session_id"],
            "kind": row["kind"],
            "exam": row["exam"],
            "templateId": row["template_id"],
            "totalQuestions": len(json.loads(row["questions_json"])),
            "catalogMode": row["catalog_mode"],
        }

    def page(self, key_id: str, session_id: str, offset: int, limit: int) -> dict:
        if limit < 1 or limit > 20 or offset < 0:
            raise ServiceError("INVALID_SHAPE", 422, "offset must be nonnegative and limit must be 1..20")
        row = self._owned_session(key_id, session_id)
        questions = json.loads(row["questions_json"])
        page = [
            {"questionId": q["question_id"], "stem": q["stem"], "options": q["options"], "module": q["module"]}
            for q in questions[offset : offset + limit]
        ]
        return {"sessionId": session_id, "offset": offset, "totalQuestions": len(questions), "questions": page}

    def submit(self, key_id: str, session_id: str, request_id: str, answers: list[dict]) -> dict:
        fingerprint = self._fingerprint({"requestId": request_id, "answers": answers})
        with self.store.connect(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=? AND key_id=?", (session_id, key_id)
            ).fetchone()
            if not row:
                raise ServiceError("SESSION_NOT_FOUND", 404, "session not found for this key")
            if row["result_json"]:
                if row["submitted_request_id"] == request_id and row["submitted_fingerprint"] == fingerprint:
                    return json.loads(row["result_json"])
                raise ServiceError("ALREADY_SUBMITTED", 409, "session already submitted")
            submitted = {item["questionId"]: item["answer"] for item in answers}
            questions = json.loads(row["questions_json"])
            results = []
            for question in questions:
                correct = submitted.get(question["question_id"]) == question["answer"]
                results.append({
                    "questionId": question["question_id"],
                    "correct": correct,
                    "answer": question["answer"],
                    "explanation": question["explanation"],
                })
            result = {
                "sessionId": session_id,
                "correctCount": sum(item["correct"] for item in results),
                "totalQuestions": len(results),
                "results": results,
            }
            connection.execute(
                "UPDATE sessions SET submitted_request_id=?, submitted_fingerprint=?, result_json=? WHERE session_id=?",
                (request_id, fingerprint, json.dumps(result, ensure_ascii=False), session_id),
            )
            return result

    def _owned_session(self, key_id: str, session_id: str):
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=? AND key_id=?", (session_id, key_id)
            ).fetchone()
        if not row:
            raise ServiceError("SESSION_NOT_FOUND", 404, "session not found for this key")
        return row
