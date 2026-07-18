from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import json
from typing import Any, Mapping
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.auth.wechat import WechatPaymentSession
from app.domains.commerce.app_store import AppStoreSignedTransactionVerifier
from app.domains.commerce.repository import CommerceRepository
from app.domains.commerce.service import CommerceService
from app.domains.commerce.stage8_repository import (
    Stage8CommerceConflict,
    Stage8CommerceRepository,
)
from app.domains.commerce.stage8_schemas import (
    PolicyAcceptances,
    VirtualPaymentOrderCreateRequest,
    WechatRefundRequest,
)
from app.domains.commerce.wechat_virtual_payment import (
    DecodedWechatMessage,
    Stage8VirtualPaymentService,
    VirtualOrderResult,
    VirtualPaymentRequest,
    VirtualRefundSubmission,
    WechatMessageCrypto,
    WechatVirtualPaymentClient,
    WechatVirtualPaymentConfig,
    WechatVirtualPaymentProtocolError,
)
from app.domains.commerce.stage8_service import Stage8CommerceUnavailable


NOW = datetime.now(UTC) + timedelta(days=1)


class FakeIdentityExchange:
    def __init__(self, *, open_id: str, session_key: str = "fresh-session-key") -> None:
        self.open_id = open_id
        self.session_key = session_key

    def exchange_payment_session(self, *, code: str) -> WechatPaymentSession:
        assert code == "fresh-login-code"
        return WechatPaymentSession(
            app_id="stage8-test-app",
            open_id=self.open_id,
            session_key=self.session_key,
        )


class FakeVirtualPaymentGateway:
    env = 0

    def __init__(self) -> None:
        self.results: dict[str, VirtualOrderResult] = {}
        self.prepared: list[dict[str, object]] = []
        self.goods_notifications: list[str] = []
        self.next_event: dict[str, Any] | None = None
        self.encoded_responses: list[dict[str, object]] = []

    def prepare_goods_payment(self, **kwargs: object) -> VirtualPaymentRequest:
        self.prepared.append(dict(kwargs))
        return VirtualPaymentRequest(
            sign_data=json.dumps(kwargs, default=str, separators=(",", ":")),
            pay_sig="a" * 64,
            signature="b" * 64,
        )

    def query_order(self, *, open_id: str, out_trade_no: str) -> VirtualOrderResult:
        del open_id
        return self.results[out_trade_no]

    def refund_order(self, **kwargs: object) -> VirtualRefundSubmission:
        return VirtualRefundSubmission(
            refund_order_id=str(kwargs["out_refund_no"]),
            refund_wx_order_id=f"wx-refund-{kwargs['out_refund_no']}",
            pay_order_id=str(kwargs["out_trade_no"]),
            pay_wx_order_id=f"wx-pay-{kwargs['out_trade_no']}",
        )

    def notify_goods_provided(
        self, *, out_trade_no: str, wx_order_id: str | None
    ) -> None:
        del wx_order_id
        self.goods_notifications.append(out_trade_no)

    def verify_endpoint(self, *, query: Mapping[str, str]) -> str:
        return query["echostr"]

    def decode_message(
        self, *, query: Mapping[str, str], body: bytes
    ) -> DecodedWechatMessage:
        del query, body
        assert self.next_event is not None
        plaintext = json.dumps(
            self.next_event, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        return DecodedWechatMessage(
            payload=dict(self.next_event),
            plaintext=plaintext,
            timestamp="1721190000",
            nonce="event-nonce",
        )

    def encode_message_response(
        self, *, plaintext: Mapping[str, object], timestamp: str, nonce: str
    ) -> dict[str, object]:
        del timestamp, nonce
        response = dict(plaintext)
        self.encoded_responses.append(response)
        return {"encoded": response}


def test_request_virtual_payment_signature_uses_fresh_matching_session() -> None:
    session_key = "one-time-session-key"
    identity = FakeIdentityExchange(open_id="openid-owner", session_key=session_key)
    http_client = httpx.Client(base_url="https://api.weixin.qq.com", trust_env=False)
    client = WechatVirtualPaymentClient(
        _settings(),
        config=_virtual_config(),
        client=http_client,
        identity_exchange=identity,  # type: ignore[arg-type]
    )

    payment = client.prepare_goods_payment(
        payment_login_code="fresh-login-code",
        expected_open_id="openid-owner",
        product_id="founder_experience_30d",
        amount_minor=6900,
        out_trade_no="A8SIGNATURE00001",
        attach="order:00000000-0000-0000-0000-000000000001",
    )

    expected_sign_data = (
        '{"offerId":"offer-test","buyQuantity":1,"env":0,'
        '"currencyType":"CNY","productId":"founder_experience_30d",'
        '"goodsPrice":6900,"outTradeNo":"A8SIGNATURE00001",'
        '"attach":"order:00000000-0000-0000-0000-000000000001"}'
    )
    assert payment.sign_data == expected_sign_data
    assert payment.pay_sig == _hmac(
        "app-key-test", "requestVirtualPayment&" + expected_sign_data
    )
    assert payment.signature == _hmac(session_key, expected_sign_data)

    identity.open_id = "openid-another-user"
    with pytest.raises(Stage8CommerceConflict):
        client.prepare_goods_payment(
            payment_login_code="fresh-login-code",
            expected_open_id="openid-owner",
            product_id="founder_experience_30d",
            amount_minor=6900,
            out_trade_no="A8SIGNATURE00002",
            attach="order:00000000-0000-0000-0000-000000000002",
        )
    http_client.close()


def test_free_cold_start_rejects_forged_virtual_payment_order(
    db_session: Session,
) -> None:
    user_id, _ = _seed_wechat_user(db_session)
    gateway = FakeVirtualPaymentGateway()
    free_settings = replace(_settings(), commercial_mode="free_cold_start")
    service = Stage8VirtualPaymentService(
        repository=Stage8CommerceRepository(db_session),
        entitlement_service=CommerceService(
            repository=CommerceRepository(db_session),
            app_store_verifier=AppStoreSignedTransactionVerifier(free_settings),
            commercial_mode="free_cold_start",
            usage_access_mode="unlimited",
            now=lambda: NOW,
        ),
        gateway=gateway,
        settings=free_settings,
        now=lambda: NOW,
    )
    with pytest.raises(Stage8CommerceUnavailable, match="free cold start"):
        service.create_order(
            user_id=user_id,
            request=VirtualPaymentOrderCreateRequest(
                command_key="forged-free-order-0001",
                offer_version_id=uuid4(),
                expected_amount_minor=6900,
                accepted_policies=PolicyAcceptances(
                    service_terms_version="forged-service",
                    refund_policy_version="forged-refund",
                    reward_policy_version="forged-reward",
                    marketing_policy_version="forged-marketing",
                ),
                payment_login_code="fresh-login-code",
                client_platform="android",
            ),
        )
    assert gateway.prepared == []


def test_ios_payment_reconciliation_grants_once_and_records_apple_route(
    db_session: Session,
) -> None:
    user_id, open_id = _seed_wechat_user(db_session)
    gateway = FakeVirtualPaymentGateway()
    service = _service(db_session, gateway=gateway)
    created = _create_order(
        db_session,
        service=service,
        user_id=user_id,
        client_platform="ios",
        command_key="ios-virtual-order-0001",
    )
    order = created.order
    assert order.payment_channel == "wechat_virtual_payment"
    assert order.client_platform == "ios"
    assert order.virtual_payment is not None
    gateway.results[order.out_trade_no] = _provider_result(
        out_trade_no=order.out_trade_no,
        status=2,
        order_type=7,
    )

    paid = service.reconcile_order(user_id=user_id, order_id=order.order_id)
    replayed = service.reconcile_order(user_id=user_id, order_id=order.order_id)

    assert paid.state == "paid"
    assert replayed.state == "paid"
    assert gateway.goods_notifications == [order.out_trade_no]
    assert service._entitlement_service.get_entitlement(user_id=user_id).tier == "pro"  # noqa: SLF001
    grant_count = db_session.execute(
        text(
            "select count(*) from commerce.entitlement_grants "
            "where source_ref = :source_ref"
        ),
        {"source_ref": f"wechat-order:{order.order_id}"},
    ).scalar_one()
    assert grant_count == 1
    provider_type = db_session.execute(
        text(
            "select provider_order_type from commerce.wechat_orders "
            "where order_id = cast(:order_id as uuid)"
        ),
        {"order_id": str(order.order_id)},
    ).scalar_one()
    assert provider_type == 7
    assert gateway.prepared[0]["expected_open_id"] == open_id


def test_provider_amount_mismatch_never_grants_entitlement(db_session: Session) -> None:
    user_id, _ = _seed_wechat_user(db_session)
    gateway = FakeVirtualPaymentGateway()
    service = _service(db_session, gateway=gateway)
    order = _create_order(
        db_session,
        service=service,
        user_id=user_id,
        client_platform="android",
        command_key="android-tamper-order-0001",
    ).order
    gateway.results[order.out_trade_no] = _provider_result(
        out_trade_no=order.out_trade_no,
        status=2,
        order_type=0,
        order_fee=6800,
    )

    with pytest.raises(WechatVirtualPaymentProtocolError):
        service.reconcile_order(user_id=user_id, order_id=order.order_id)
    assert service._entitlement_service.get_entitlement(user_id=user_id).tier == "free"  # noqa: SLF001


def test_signed_delivery_callback_is_replay_safe(db_session: Session) -> None:
    user_id, open_id = _seed_wechat_user(db_session)
    gateway = FakeVirtualPaymentGateway()
    service = _service(db_session, gateway=gateway)
    order = _create_order(
        db_session,
        service=service,
        user_id=user_id,
        client_platform="android",
        command_key="android-callback-order-0001",
    ).order
    gateway.next_event = {
        "Event": "xpay_goods_deliver_notify",
        "CreateTime": int(NOW.timestamp()),
        "OpenId": open_id,
        "OutTradeNo": order.out_trade_no,
        "Env": 0,
        "GoodsInfo": {
            "ProductId": "founder_experience_30d",
            "Quantity": 1,
            "OrigPrice": 6900,
            "ActualPrice": 6900,
            "Attach": f"order:{order.order_id}",
        },
        "WeChatPayInfo": {
            "MchOrderNo": "provider-merchant-order",
            "TransactionId": "wechat-transaction",
            "PaidTime": int(NOW.timestamp()),
        },
    }

    first = service.process_event(query={}, body=b"signed")
    second = service.process_event(query={}, body=b"signed")

    assert first == second
    assert gateway.encoded_responses == [
        {"ErrCode": 0, "ErrMsg": "success"},
        {"ErrCode": 0, "ErrMsg": "success"},
    ]
    grant_count = db_session.execute(
        text(
            "select count(*) from commerce.entitlement_grants "
            "where source_ref = :source_ref"
        ),
        {"source_ref": f"wechat-order:{order.order_id}"},
    ).scalar_one()
    assert grant_count == 1


def test_ios_refund_intent_inquiry_and_provider_completion(db_session: Session) -> None:
    user_id, open_id = _seed_wechat_user(db_session)
    gateway = FakeVirtualPaymentGateway()
    service = _service(db_session, gateway=gateway)
    order = _create_order(
        db_session,
        service=service,
        user_id=user_id,
        client_platform="ios",
        command_key="ios-refund-order-0001",
    ).order
    gateway.results[order.out_trade_no] = _provider_result(
        out_trade_no=order.out_trade_no,
        status=2,
        order_type=7,
    )
    service.reconcile_order(user_id=user_id, order_id=order.order_id)

    requested = service.request_refund(
        user_id=user_id,
        order_id=order.order_id,
        request=WechatRefundRequest(
            command_key="ios-refund-intent-0001", reason="not_suitable"
        ),
    )
    assert requested.provider_action == "app_store_required"
    assert requested.refund.state == "requested"
    assert requested.entitlement.tier == "pro"

    gateway.next_event = {
        "Event": "xpay_subscribe_ios_refund_query_notify",
        "CreateTime": int(NOW.timestamp()) + 1,
        "refund_time": str(int(NOW.timestamp()) + 1),
        "order_time": str(int(NOW.timestamp())),
        "channel_bill": "apple-bill-0001",
        "bundleid": "com.example.anan",
        "product_id": "founder_experience_30d",
        "p_count": "1",
        "refund_request_reason": "not suitable",
        "provide_status": "1",
        "pay_order_id": order.out_trade_no,
    }
    service.process_event(query={}, body=b"signed-inquiry")
    assert gateway.encoded_responses[-1]["result_code"] == 0

    gateway.next_event = {
        "Event": "xpay_refund_notify",
        "CreateTime": int(NOW.timestamp()) + 2,
        "OpenId": open_id,
        "WxRefundId": "apple-refund-0001",
        "MchRefundId": "apple-generated-refund-id",
        "WxOrderId": "wx-order-0001",
        "MchOrderId": order.out_trade_no,
        "RefundFee": 6900,
        "RetCode": 0,
        "RetMsg": "success",
    }
    service.process_event(query={}, body=b"signed-refund")

    refreshed = service.reconcile_order(user_id=user_id, order_id=order.order_id)
    assert refreshed.state == "refunded"
    assert service._entitlement_service.get_entitlement(user_id=user_id).tier == "free"  # noqa: SLF001


def test_android_refund_is_reconciled_from_xpay_query(db_session: Session) -> None:
    user_id, _ = _seed_wechat_user(db_session)
    gateway = FakeVirtualPaymentGateway()
    service = _service(db_session, gateway=gateway)
    order = _create_order(
        db_session,
        service=service,
        user_id=user_id,
        client_platform="android",
        command_key="android-refund-order-0001",
    ).order
    gateway.results[order.out_trade_no] = _provider_result(
        out_trade_no=order.out_trade_no,
        status=2,
        order_type=0,
    )
    service.reconcile_order(user_id=user_id, order_id=order.order_id)
    requested = service.request_refund(
        user_id=user_id,
        order_id=order.order_id,
        request=WechatRefundRequest(
            command_key="android-refund-intent-0001", reason="not_suitable"
        ),
    )
    assert requested.refund.state == "processing"

    gateway.results[order.out_trade_no] = _provider_result(
        out_trade_no=order.out_trade_no,
        status=5,
        order_type=0,
        left_fee=0,
    )
    result = service.reconcile_pending()

    assert result["refundsReconciled"] == 1
    refreshed = service.reconcile_order(user_id=user_id, order_id=order.order_id)
    assert refreshed.state == "refunded"
    assert service._entitlement_service.get_entitlement(user_id=user_id).tier == "free"  # noqa: SLF001


def test_safe_mode_crypto_round_trip_and_signature_rejection() -> None:
    crypto = WechatMessageCrypto(
        token="message-token",
        aes_key=bytes(range(32)),
        app_id="wx-test-app",
    )
    response = crypto.encode_response(
        plaintext=b'{"Event":"xpay_goods_deliver_notify"}',
        timestamp="1721190000",
        nonce="nonce-1",
    )
    query = {
        "encrypt_type": "aes",
        "timestamp": "1721190000",
        "nonce": "nonce-1",
        "msg_signature": str(response["MsgSignature"]),
    }
    decoded = crypto.decode_message(
        query=query,
        body=json.dumps({"Encrypt": response["Encrypt"]}).encode(),
    )
    assert decoded.payload == {"Event": "xpay_goods_deliver_notify"}

    with pytest.raises(WechatVirtualPaymentProtocolError):
        crypto.decode_message(
            query={**query, "msg_signature": "0" * 40},
            body=json.dumps({"Encrypt": response["Encrypt"]}).encode(),
        )


def _service(
    session: Session, *, gateway: FakeVirtualPaymentGateway
) -> Stage8VirtualPaymentService:
    settings = _settings()
    entitlement_service = CommerceService(
        repository=CommerceRepository(session),
        app_store_verifier=AppStoreSignedTransactionVerifier(settings),
        commercial_mode="paid",
        usage_access_mode="entitlement_quota",
        now=lambda: NOW,
    )
    return Stage8VirtualPaymentService(
        repository=Stage8CommerceRepository(session),
        entitlement_service=entitlement_service,
        gateway=gateway,
        settings=settings,
        now=lambda: NOW,
    )


def _create_order(
    session: Session,
    *,
    service: Stage8VirtualPaymentService,
    user_id: str,
    client_platform: str,
    command_key: str,
):
    offer = Stage8CommerceRepository(session).list_active_offers(now=NOW)[0]
    return service.create_order(
        user_id=user_id,
        request=VirtualPaymentOrderCreateRequest(
            command_key=command_key,
            offer_version_id=offer.offer_version_id,
            expected_amount_minor=offer.amount_minor,
            accepted_policies=PolicyAcceptances(
                service_terms_version=offer.service_terms_version,
                refund_policy_version=offer.refund_policy_version,
                reward_policy_version=offer.reward_policy_version,
                marketing_policy_version=offer.marketing_policy_version,
            ),
            payment_login_code="fresh-login-code",
            client_platform=client_platform,  # type: ignore[arg-type]
        ),
    )


def _provider_result(
    *,
    out_trade_no: str,
    status: int,
    order_type: int,
    order_fee: int = 6900,
    left_fee: int = 6900,
) -> VirtualOrderResult:
    return VirtualOrderResult(
        out_trade_no=out_trade_no,
        status=status,
        order_fee=order_fee,
        paid_fee=6900,
        left_fee=left_fee,
        paid_at=NOW,
        order_type=order_type,
        wx_order_id=f"wx-{out_trade_no}",
        channel_order_id=f"channel-{out_trade_no}",
        wxpay_order_id=f"wxpay-{out_trade_no}",
        provide_time=0,
    )


def _settings() -> Settings:
    return Settings(
        commercial_mode="paid",
        wechat_mini_app_id="stage8-test-app",
        wechat_mini_app_secret="stage8-test-secret",
        wechat_virtual_pay_offer_id="offer-test",
        wechat_virtual_pay_app_key="app-key-test",
        wechat_virtual_pay_env=0,
        wechat_virtual_pay_message_token="message-token",
        wechat_virtual_pay_message_encoding_aes_key=b64encode(bytes(range(32)))
        .decode()
        .rstrip("="),
        bundle_id="com.example.anan",
    )


def _virtual_config() -> WechatVirtualPaymentConfig:
    return WechatVirtualPaymentConfig.from_settings(_settings())


def _seed_wechat_user(session: Session) -> tuple[str, str]:
    user_id = str(uuid4())
    open_id = f"openid-{uuid4().hex}"
    session.execute(
        text(
            """
            insert into learner.users (user_id, display_name, email)
            values (cast(:user_id as uuid), '阿岸', :email)
            """
        ),
        {"user_id": user_id, "email": f"{user_id}@example.test"},
    )
    session.execute(
        text("insert into learner.profiles (user_id) values (cast(:user_id as uuid))"),
        {"user_id": user_id},
    )
    session.execute(
        text(
            """
            insert into learner.wechat_identities (
              identity_id, user_id, app_id, open_id, union_id,
              session_key_hash, last_login_at
            ) values (
              gen_random_uuid(), cast(:user_id as uuid), 'stage8-test-app',
              :open_id, :union_id, repeat('a', 64), :now
            )
            """
        ),
        {
            "user_id": user_id,
            "open_id": open_id,
            "union_id": f"union-{uuid4().hex}",
            "now": NOW,
        },
    )
    session.commit()
    return user_id, open_id


def _hmac(key: str, value: str) -> str:
    return hmac.new(key.encode(), value.encode(), sha256).hexdigest()
