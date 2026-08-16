from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
import json
import secrets
import time
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


PREORDER_URL = "https://payapp.weixin.qq.com/palmpayminiapp/clawagentpay/preorder"
PREORDER_PATH = "/palmpayminiapp/clawagentpay/preorder"


@dataclass(frozen=True)
class PaymentOrder:
    out_trade_no: str
    payment_code: str


class PaymentGateway(Protocol):
    def create_order(self, out_trade_no: str, amount_minor: int, description: str) -> PaymentOrder: ...

    def query_order(self, out_trade_no: str) -> str: ...


class FakePaymentGateway:
    """Explicit test/demo gateway. It never contacts WeChat or moves money."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}

    def create_order(self, out_trade_no: str, amount_minor: int, description: str) -> PaymentOrder:
        self.states.setdefault(out_trade_no, "NOTPAY")
        return PaymentOrder(out_trade_no, f"weixin://demo/{out_trade_no}")

    def query_order(self, out_trade_no: str) -> str:
        return self.states.get(out_trade_no, "NOTPAY")

    def mark_paid(self, out_trade_no: str) -> None:
        self.states[out_trade_no] = "SUCCESS"


class LivePaymentGatewayUnavailable:
    def create_order(self, out_trade_no: str, amount_minor: int, description: str) -> PaymentOrder:
        raise RuntimeError("Live WeChat gateway is not configured; refusing to create a real order.")

    def query_order(self, out_trade_no: str) -> str:
        raise RuntimeError("Live WeChat gateway is not configured; payment status is uncertain.")


class SkillHubPreorderSigner:
    def __init__(self, developer_id: str, public_key_id: str, private_key_pem: bytes):
        self.developer_id = developer_id
        self.public_key_id = public_key_id
        self.private_key = serialization.load_pem_private_key(private_key_pem, password=None)

    def build(
        self,
        *,
        payment_code: str,
        out_trade_no: str,
        amount_minor: int,
        plan: str,
        now: int | None = None,
        nonce: str | None = None,
    ) -> dict:
        timestamp = now or int(time.time())
        nonce_str = nonce or secrets.token_hex(16)
        layer2 = {
            "skill_info": {"skill_name": "岸岸题库云", "skill_id": "anan-qbank-cloud"},
            "pay_type": "SKILL_PAY",
            "pay_mode": "AUTH_AND_PAY",
            "payment_code": payment_code,
            "out_trade_no": out_trade_no,
            "pay_items": [{"name": f"岸岸题库云{plan}会员", "amount": amount_minor, "quantity": 1}],
            "expires_at": timestamp + 900,
        }
        payment_required = b64encode(
            json.dumps(layer2, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        signing_text = f"POST\n{PREORDER_PATH}\n{timestamp}\n{nonce_str}\n{payment_required}\n"
        signature = b64encode(
            self.private_key.sign(signing_text.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        return {
            "signature_type": "SKILLHUB-SHA256-RSA2048",
            "developer_platform": "SKILLHUB",
            "developer_id": self.developer_id,
            "pub_key_id": self.public_key_id,
            "nonce": nonce_str,
            "timestamp": timestamp,
            "signature": signature,
            "payment_required": payment_required,
        }


def demo_payment_required(order: PaymentOrder, amount_minor: int, plan: str) -> str:
    block = {
        "pay_type": "SKILL_PAY",
        "pay_mode": "AUTH_AND_PAY",
        "payment_code": order.payment_code,
        "out_trade_no": order.out_trade_no,
        "pay_items": [{"name": f"岸岸题库云{plan}会员", "amount": amount_minor, "quantity": 1}],
        "demo": True,
    }
    return b64encode(json.dumps(block, ensure_ascii=False, separators=(",", ":")).encode()).decode()
