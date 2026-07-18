from __future__ import annotations

from base64 import b64decode, b64encode
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha1, sha256
import hmac
import json
import secrets
import struct
from threading import Lock
import time
from typing import Any, Protocol
from uuid import UUID

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx

from app.core.config import Settings
from app.domains.auth.wechat import WechatIdentityExchange
from app.domains.commerce.service import CommerceService
from app.domains.commerce.stage8_repository import (
    OfferRecord,
    OrderRecord,
    RefundRecord,
    Stage8CommerceConflict,
    Stage8CommerceNotFound,
    Stage8CommerceRepository,
)
from app.domains.commerce.stage8_schemas import (
    PolicyAcceptances,
    VirtualPaymentOrderCreateRequest,
    WechatOrderCreateResponse,
    WechatOrderView,
    WechatRefundRequest,
    WechatRefundResponse,
    WechatRefundView,
    WechatVirtualPaymentParameters,
)
from app.domains.commerce.stage8_service import Stage8PaymentPending
from app.domains.commerce.wechat_pay import (
    WechatPayConfigurationError,
    WechatPayProtocolError,
    WechatPayTransportUncertain,
)


class WechatVirtualPaymentConfigurationError(WechatPayConfigurationError):
    pass


class WechatVirtualPaymentProtocolError(WechatPayProtocolError):
    pass


class WechatVirtualPaymentTransportUncertain(WechatPayTransportUncertain):
    """An xpay mutation may have reached WeChat; callers must query before retry."""


@dataclass(frozen=True)
class WechatVirtualPaymentConfig:
    offer_id: str
    app_key: str
    env: int
    message_token: str
    message_aes_key: bytes

    @classmethod
    def from_settings(cls, settings: Settings) -> "WechatVirtualPaymentConfig":
        values = {
            "WECHAT_VIRTUAL_PAY_OFFER_ID": settings.wechat_virtual_pay_offer_id.strip(),
            "WECHAT_VIRTUAL_PAY_APP_KEY": settings.wechat_virtual_pay_app_key.strip(),
            "WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN": settings.wechat_virtual_pay_message_token.strip(),
            "WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY": (
                settings.wechat_virtual_pay_message_encoding_aes_key.strip()
            ),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise WechatVirtualPaymentConfigurationError(
                "WeChat virtual payment configuration is incomplete: "
                + ", ".join(missing)
            )
        env = settings.wechat_virtual_pay_env
        if env not in {0, 1}:
            raise WechatVirtualPaymentConfigurationError(
                "WECHAT_VIRTUAL_PAY_ENV must be 0 or 1"
            )
        encoding_key = values["WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY"]
        if len(encoding_key) != 43:
            raise WechatVirtualPaymentConfigurationError(
                "WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY must contain 43 characters"
            )
        try:
            aes_key = b64decode(encoding_key + "=", validate=True)
        except ValueError as exc:
            raise WechatVirtualPaymentConfigurationError(
                "WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY is not valid base64"
            ) from exc
        if len(aes_key) != 32:
            raise WechatVirtualPaymentConfigurationError(
                "WECHAT_VIRTUAL_PAY_MESSAGE_ENCODING_AES_KEY must decode to 32 bytes"
            )
        return cls(
            offer_id=values["WECHAT_VIRTUAL_PAY_OFFER_ID"],
            app_key=values["WECHAT_VIRTUAL_PAY_APP_KEY"],
            env=env,
            message_token=values["WECHAT_VIRTUAL_PAY_MESSAGE_TOKEN"],
            message_aes_key=aes_key,
        )


@dataclass(frozen=True)
class VirtualPaymentRequest:
    sign_data: str
    pay_sig: str
    signature: str
    mode: str = "short_series_goods"


@dataclass(frozen=True)
class VirtualOrderResult:
    out_trade_no: str
    status: int
    order_fee: int
    paid_fee: int
    left_fee: int
    paid_at: datetime | None
    order_type: int
    wx_order_id: str | None
    channel_order_id: str | None
    wxpay_order_id: str | None
    provide_time: int


@dataclass(frozen=True)
class VirtualRefundSubmission:
    refund_order_id: str
    refund_wx_order_id: str | None
    pay_order_id: str
    pay_wx_order_id: str | None


@dataclass(frozen=True)
class DecodedWechatMessage:
    payload: dict[str, Any]
    plaintext: bytes
    timestamp: str
    nonce: str


class WechatVirtualPaymentGateway(Protocol):
    @property
    def env(self) -> int: ...

    def prepare_goods_payment(
        self,
        *,
        payment_login_code: str,
        expected_open_id: str,
        product_id: str,
        amount_minor: int,
        out_trade_no: str,
        attach: str,
    ) -> VirtualPaymentRequest: ...

    def query_order(self, *, open_id: str, out_trade_no: str) -> VirtualOrderResult: ...

    def refund_order(
        self,
        *,
        open_id: str,
        out_trade_no: str,
        out_refund_no: str,
        left_fee: int,
        refund_fee: int,
        reason: str,
    ) -> VirtualRefundSubmission: ...

    def notify_goods_provided(
        self, *, out_trade_no: str, wx_order_id: str | None
    ) -> None: ...

    def verify_endpoint(
        self,
        *,
        query: Mapping[str, str],
    ) -> str: ...

    def decode_message(
        self, *, query: Mapping[str, str], body: bytes
    ) -> DecodedWechatMessage: ...

    def encode_message_response(
        self, *, plaintext: Mapping[str, object], timestamp: str, nonce: str
    ) -> dict[str, object]: ...


class StableAccessTokenProvider:
    def __init__(
        self,
        *,
        settings: Settings,
        client: httpx.Client,
        now_epoch: Callable[[], float] | None = None,
    ) -> None:
        if not settings.wechat_mini_app_id.strip() or not settings.wechat_mini_app_secret.strip():
            raise WechatVirtualPaymentConfigurationError(
                "WECHAT_MINI_APP_ID and WECHAT_MINI_APP_SECRET are required"
            )
        self._app_id = settings.wechat_mini_app_id.strip()
        self._app_secret = settings.wechat_mini_app_secret.strip()
        self._client = client
        self._now_epoch = now_epoch or time.time
        self._token = ""
        self._expires_at = 0.0
        self._lock = Lock()

    def get(self) -> str:
        now = self._now_epoch()
        if self._token and now < self._expires_at:
            return self._token
        with self._lock:
            now = self._now_epoch()
            if self._token and now < self._expires_at:
                return self._token
            try:
                response = self._client.post(
                    "/cgi-bin/stable_token",
                    json={
                        "grant_type": "client_credential",
                        "appid": self._app_id,
                        "secret": self._app_secret,
                        "force_refresh": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise WechatVirtualPaymentProtocolError(
                    "WeChat stable access token request failed"
                ) from exc
            token = _required_string(payload, "access_token")
            expires_in = _required_int(payload, "expires_in")
            if expires_in <= 300:
                raise WechatVirtualPaymentProtocolError(
                    "WeChat stable access token lifetime is unexpectedly short"
                )
            self._token = token
            self._expires_at = now + expires_in - 300
            return token


class WechatVirtualPaymentClient:
    def __init__(
        self,
        settings: Settings,
        *,
        config: WechatVirtualPaymentConfig | None = None,
        client: httpx.Client | None = None,
        identity_exchange: WechatIdentityExchange | None = None,
        now_epoch: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._config = config or WechatVirtualPaymentConfig.from_settings(settings)
        self._client = client or httpx.Client(
            base_url=settings.wechat_api_base_url,
            timeout=httpx.Timeout(8.0, connect=4.0),
        )
        self._identity_exchange = identity_exchange or WechatIdentityExchange(settings)
        self._tokens = StableAccessTokenProvider(
            settings=settings,
            client=self._client,
            now_epoch=now_epoch,
        )
        self._crypto = WechatMessageCrypto(
            token=self._config.message_token,
            aes_key=self._config.message_aes_key,
            app_id=settings.wechat_mini_app_id,
        )

    @property
    def env(self) -> int:
        return self._config.env

    def prepare_goods_payment(
        self,
        *,
        payment_login_code: str,
        expected_open_id: str,
        product_id: str,
        amount_minor: int,
        out_trade_no: str,
        attach: str,
    ) -> VirtualPaymentRequest:
        payment_session = self._identity_exchange.exchange_payment_session(
            code=payment_login_code
        )
        if payment_session.app_id != self._settings.wechat_mini_app_id:
            raise WechatVirtualPaymentProtocolError(
                "payment login belongs to a different Mini Program"
            )
        if not hmac.compare_digest(payment_session.open_id, expected_open_id):
            raise Stage8CommerceConflict(
                "payment login identity does not match the authenticated account"
            )
        sign_data = _canonical_json(
            {
                "offerId": self._config.offer_id,
                "buyQuantity": 1,
                "env": self._config.env,
                "currencyType": "CNY",
                "productId": product_id,
                "goodsPrice": amount_minor,
                "outTradeNo": out_trade_no,
                "attach": attach,
            }
        )
        return VirtualPaymentRequest(
            sign_data=sign_data,
            pay_sig=_hmac_sha256(
                self._config.app_key, "requestVirtualPayment&" + sign_data
            ),
            signature=_hmac_sha256(payment_session.session_key, sign_data),
        )

    def query_order(self, *, open_id: str, out_trade_no: str) -> VirtualOrderResult:
        payload = self._xpay_request(
            uri="/xpay/query_order",
            payload={
                "openid": open_id,
                "env": self._config.env,
                "order_id": out_trade_no,
            },
            mutating=False,
        )
        order = _required_mapping(payload, "order")
        returned_order_id = _required_string(order, "order_id")
        if returned_order_id != out_trade_no:
            raise WechatVirtualPaymentProtocolError(
                "WeChat virtual-payment order number mismatch"
            )
        paid_time = _required_int(order, "paid_time")
        return VirtualOrderResult(
            out_trade_no=returned_order_id,
            status=_required_int(order, "status"),
            order_fee=_required_int(order, "order_fee"),
            paid_fee=_required_int(order, "paid_fee"),
            left_fee=_required_int(order, "left_fee"),
            paid_at=datetime.fromtimestamp(paid_time, UTC) if paid_time > 0 else None,
            order_type=_required_int(order, "order_type"),
            wx_order_id=_optional_string(order.get("wx_order_id")),
            channel_order_id=_optional_string(order.get("channel_order_id")),
            wxpay_order_id=_optional_string(order.get("wxpay_order_id")),
            provide_time=_required_int(order, "provide_time"),
        )

    def refund_order(
        self,
        *,
        open_id: str,
        out_trade_no: str,
        out_refund_no: str,
        left_fee: int,
        refund_fee: int,
        reason: str,
    ) -> VirtualRefundSubmission:
        payload = self._xpay_request(
            uri="/xpay/refund_order",
            payload={
                "openid": open_id,
                "order_id": out_trade_no,
                "refund_order_id": out_refund_no,
                "left_fee": left_fee,
                "refund_fee": refund_fee,
                "biz_meta": _canonical_json({"reason": reason}),
                "refund_reason": "3",
                "req_from": "2",
                "env": self._config.env,
            },
            mutating=True,
        )
        pay_order_id = _required_string(payload, "pay_order_id")
        if pay_order_id != out_trade_no:
            raise WechatVirtualPaymentProtocolError(
                "WeChat refund response belongs to another payment order"
            )
        return VirtualRefundSubmission(
            refund_order_id=_required_string(payload, "refund_order_id"),
            refund_wx_order_id=_optional_string(payload.get("refund_wx_order_id")),
            pay_order_id=pay_order_id,
            pay_wx_order_id=_optional_string(payload.get("pay_wx_order_id")),
        )

    def notify_goods_provided(
        self, *, out_trade_no: str, wx_order_id: str | None
    ) -> None:
        body: dict[str, object] = {
            "order_id": out_trade_no,
            "env": self._config.env,
        }
        if wx_order_id:
            body["wx_order_id"] = wx_order_id
        self._xpay_request(
            uri="/xpay/notify_provide_goods",
            payload=body,
            mutating=True,
            pay_signature=False,
        )

    def verify_endpoint(self, *, query: Mapping[str, str]) -> str:
        return self._crypto.verify_endpoint(query=query)

    def decode_message(
        self, *, query: Mapping[str, str], body: bytes
    ) -> DecodedWechatMessage:
        return self._crypto.decode_message(query=query, body=body)

    def encode_message_response(
        self, *, plaintext: Mapping[str, object], timestamp: str, nonce: str
    ) -> dict[str, object]:
        return self._crypto.encode_response(
            plaintext=_canonical_json(dict(plaintext)).encode("utf-8"),
            timestamp=timestamp,
            nonce=nonce,
        )

    def _xpay_request(
        self,
        *,
        uri: str,
        payload: Mapping[str, object],
        mutating: bool,
        pay_signature: bool = True,
    ) -> dict[str, Any]:
        body = _canonical_json(dict(payload))
        query: dict[str, str] = {"access_token": self._tokens.get()}
        if pay_signature:
            query["pay_sig"] = _hmac_sha256(
                self._config.app_key,
                uri + "&" + body,
            )
        try:
            response = self._client.post(
                uri,
                params=query,
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json() if response.content else {}
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            error = (
                WechatVirtualPaymentTransportUncertain
                if mutating
                else WechatVirtualPaymentProtocolError
            )
            raise error(f"WeChat xpay transport failed for {uri}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatVirtualPaymentProtocolError(
                f"WeChat xpay returned an invalid response for {uri}"
            ) from exc
        if not isinstance(result, dict):
            raise WechatVirtualPaymentProtocolError(
                f"WeChat xpay returned non-object JSON for {uri}"
            )
        error_code = result.get("errcode", 0)
        if not isinstance(error_code, int) or isinstance(error_code, bool):
            raise WechatVirtualPaymentProtocolError(
                f"WeChat xpay returned invalid errcode for {uri}"
            )
        if error_code != 0:
            error_message = _optional_string(result.get("errmsg")) or "unknown"
            raise WechatVirtualPaymentProtocolError(
                f"WeChat xpay rejected {uri}: {error_code} {error_message}"
            )
        return result


class WechatMessageCrypto:
    """Strict WeChat safe-mode JSON message verification and encryption."""

    def __init__(self, *, token: str, aes_key: bytes, app_id: str) -> None:
        self._token = token
        self._aes_key = aes_key
        self._app_id = app_id.encode("utf-8")

    def verify_endpoint(self, *, query: Mapping[str, str]) -> str:
        timestamp = _query_value(query, "timestamp")
        nonce = _query_value(query, "nonce")
        echo = _query_value(query, "echostr")
        msg_signature = _query_value(query, "msg_signature")
        self._verify_signature(
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=echo,
        )
        return self._decrypt(echo).decode("utf-8")

    def decode_message(
        self, *, query: Mapping[str, str], body: bytes
    ) -> DecodedWechatMessage:
        if query.get("encrypt_type") != "aes":
            raise WechatVirtualPaymentProtocolError(
                "WeChat virtual-payment events require safe-mode encryption"
            )
        timestamp = _query_value(query, "timestamp")
        nonce = _query_value(query, "nonce")
        msg_signature = _query_value(query, "msg_signature")
        try:
            envelope = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise WechatVirtualPaymentProtocolError(
                "WeChat message envelope is invalid JSON"
            ) from exc
        if not isinstance(envelope, dict):
            raise WechatVirtualPaymentProtocolError(
                "WeChat message envelope must be an object"
            )
        encrypted = _required_string(envelope, "Encrypt")
        self._verify_signature(
            signature=msg_signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        )
        plaintext = self._decrypt(encrypted)
        try:
            payload = json.loads(plaintext)
        except (UnicodeDecodeError, ValueError) as exc:
            raise WechatVirtualPaymentProtocolError(
                "Decrypted WeChat message is invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise WechatVirtualPaymentProtocolError(
                "Decrypted WeChat message must be an object"
            )
        return DecodedWechatMessage(
            payload=payload,
            plaintext=plaintext,
            timestamp=timestamp,
            nonce=nonce,
        )

    def encode_response(
        self, *, plaintext: bytes, timestamp: str, nonce: str
    ) -> dict[str, object]:
        encrypted = self._encrypt(plaintext)
        signature = _wechat_sha1(self._token, timestamp, nonce, encrypted)
        return {
            "Encrypt": encrypted,
            "MsgSignature": signature,
            "TimeStamp": int(timestamp),
            "Nonce": nonce,
        }

    def _verify_signature(
        self, *, signature: str, timestamp: str, nonce: str, encrypted: str
    ) -> None:
        expected = _wechat_sha1(self._token, timestamp, nonce, encrypted)
        if not hmac.compare_digest(signature, expected):
            raise WechatVirtualPaymentProtocolError(
                "WeChat message signature verification failed"
            )

    def _decrypt(self, value: str) -> bytes:
        try:
            ciphertext = b64decode(value, validate=True)
            decryptor = Cipher(
                algorithms.AES(self._aes_key),
                modes.CBC(self._aes_key[:16]),
            ).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(256).unpadder()
            full = unpadder.update(padded) + unpadder.finalize()
        except (ValueError, TypeError) as exc:
            raise WechatVirtualPaymentProtocolError(
                "WeChat message decryption failed"
            ) from exc
        if len(full) < 20:
            raise WechatVirtualPaymentProtocolError("WeChat message is truncated")
        message_length = struct.unpack("!I", full[16:20])[0]
        end = 20 + message_length
        if end > len(full):
            raise WechatVirtualPaymentProtocolError(
                "WeChat message length is invalid"
            )
        message = full[20:end]
        app_id = full[end:]
        if not hmac.compare_digest(app_id, self._app_id):
            raise WechatVirtualPaymentProtocolError(
                "WeChat message app id does not match runtime"
            )
        return message

    def _encrypt(self, plaintext: bytes) -> str:
        full = (
            secrets.token_bytes(16)
            + struct.pack("!I", len(plaintext))
            + plaintext
            + self._app_id
        )
        padder = padding.PKCS7(256).padder()
        padded = padder.update(full) + padder.finalize()
        encryptor = Cipher(
            algorithms.AES(self._aes_key),
            modes.CBC(self._aes_key[:16]),
        ).encryptor()
        return b64encode(encryptor.update(padded) + encryptor.finalize()).decode(
            "ascii"
        )


class Stage8VirtualPaymentService:
    def __init__(
        self,
        *,
        repository: Stage8CommerceRepository,
        entitlement_service: CommerceService,
        gateway: WechatVirtualPaymentGateway,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._entitlement_service = entitlement_service
        self._gateway = gateway
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))

    def create_order(
        self, *, user_id: str, request: VirtualPaymentOrderCreateRequest
    ) -> WechatOrderCreateResponse:
        if request.client_platform == "ios" and self._gateway.env != 0:
            raise Stage8CommerceConflict(
                "iOS virtual payment is unavailable in the WeChat sandbox"
            )
        now = self._now()
        offer = self._repository.offer_by_id(request.offer_version_id, now=now)
        if offer.channel != "wechat_virtual_payment":
            raise Stage8CommerceConflict(
                "the selected offer is not published for WeChat virtual payment"
            )
        if request.expected_amount_minor != offer.amount_minor:
            raise Stage8CommerceConflict(
                "offer amount changed; refresh catalog before payment"
            )
        _validate_policy_acceptances(offer, request.accepted_policies)
        policies = self._repository.policies(_offer_policy_versions(offer))
        terms_snapshot: dict[str, object] = {
            "offerVersionId": str(offer.offer_version_id),
            "offerCode": offer.offer_code,
            "versionNo": offer.version_no,
            "productId": offer.product_id,
            "channel": "wechat_virtual_payment",
            "clientPlatform": request.client_platform,
            "currency": offer.currency,
            "amountMinor": offer.amount_minor,
            "serviceDays": offer.service_days,
            "automaticRenewal": False,
            "iosPaymentProcessor": "Apple" if request.client_platform == "ios" else None,
            "policies": [
                {
                    "policyVersion": item.policy_version,
                    "policyKind": item.policy_kind,
                    "title": item.title,
                    "content": item.content,
                    "contentSha256": item.content_sha256,
                }
                for item in policies
            ],
            "acceptedAt": now.isoformat(),
        }
        request_fingerprint = _fingerprint(
            {
                "offerVersionId": str(request.offer_version_id),
                "expectedAmountMinor": request.expected_amount_minor,
                "acceptedPolicies": request.accepted_policies.model_dump(by_alias=True),
                "clientPlatform": request.client_platform,
            }
        )
        payer_open_id = self._repository.wechat_open_id(
            user_id=user_id,
            app_id=self._settings.wechat_mini_app_id,
        )
        order = self._repository.create_order_and_reserve_slot(
            user_id=user_id,
            command_key=request.command_key,
            request_fingerprint=request_fingerprint,
            offer=offer,
            payer_open_id=payer_open_id,
            terms_snapshot=terms_snapshot,
            expires_at=now
            + timedelta(seconds=self._settings.wechat_pay_order_expiry_seconds),
            payment_channel="wechat_virtual_payment",
            client_platform=request.client_platform,
        )
        if order.payment_channel != "wechat_virtual_payment":
            raise Stage8CommerceConflict(
                "idempotency key belongs to an incompatible payment channel"
            )
        if order.client_platform != request.client_platform:
            raise Stage8CommerceConflict(
                "idempotency key belongs to another client platform"
            )
        if order.state in {
            "paid",
            "partially_refunded",
            "refund_processing",
            "refunded",
            "closed",
        }:
            return WechatOrderCreateResponse(order=_order_view(order))
        payment = self._gateway.prepare_goods_payment(
            payment_login_code=request.payment_login_code,
            expected_open_id=payer_open_id,
            product_id=order.product_id,
            amount_minor=order.amount_minor,
            out_trade_no=order.out_trade_no,
            attach=f"order:{order.order_id}",
        )
        self._repository.mark_order_awaiting_virtual_payment(
            order_id=order.order_id,
            payload_sha256=sha256(payment.sign_data.encode("utf-8")).hexdigest(),
        )
        refreshed = self._repository.order_by_id(
            user_id=user_id, order_id=order.order_id
        )
        return WechatOrderCreateResponse(
            order=_order_view(refreshed, virtual_payment=payment)
        )

    def reconcile_order(self, *, user_id: str, order_id: UUID) -> WechatOrderView:
        order = self._repository.order_by_id(user_id=user_id, order_id=order_id)
        return _order_view(self._reconcile_order(order))

    def request_refund(
        self,
        *,
        user_id: str,
        order_id: UUID,
        request: WechatRefundRequest,
    ) -> WechatRefundResponse:
        order = self._repository.order_by_id(user_id=user_id, order_id=order_id)
        eligible, remaining = self._refund_eligibility(order)
        if not eligible:
            raise Stage8CommerceConflict("order is not eligible for the refund policy")
        refund = self._repository.create_refund(
            order=order,
            command_key=request.command_key,
            request_fingerprint=_fingerprint(
                {
                    "orderId": str(order.order_id),
                    "amountMinor": remaining,
                    "policyVersion": "founder-refund@v2",
                }
            ),
            refund_kind="not_suitable",
            reason="用户在购买后 30 个自然日内认为服务不适合；与成绩无关",
            amount_minor=remaining,
            policy_version="founder-refund@v2",
            now=self._now(),
            transition_order=order.client_platform != "ios",
        )
        if order.client_platform == "ios":
            self._repository.mark_refund_provider_state(
                refund=refund,
                state="requested",
                provider_refund_id=None,
                provider_status="APP_STORE_ACTION_REQUIRED",
                processed_at=None,
            )
            return WechatRefundResponse(
                refund=_refund_view(
                    self._repository.refund_by_number(
                        out_refund_no=refund.out_refund_no
                    )
                ),
                entitlement=self._entitlement_service.get_entitlement(
                    user_id=user_id
                ),
                provider_action="app_store_required",
                provider_action_url="https://reportaproblem.apple.com/",
                provider_action_message=(
                    "iOS 订单需由你在 App Store 提交退款申请；"
                    "苹果问询时，我们会对 30 天内的申请建议退款，最终结果由 Apple 决定。"
                ),
            )
        try:
            submission = self._gateway.refund_order(
                open_id=order.payer_open_id,
                out_trade_no=order.out_trade_no,
                out_refund_no=refund.out_refund_no,
                left_fee=order.amount_minor - order.refunded_minor,
                refund_fee=refund.amount_minor,
                reason="not_suitable",
            )
        except WechatVirtualPaymentTransportUncertain as exc:
            self._repository.mark_refund_provider_state(
                refund=refund,
                state="provider_uncertain",
                provider_refund_id=None,
                provider_status="UNKNOWN",
                processed_at=None,
            )
            raise Stage8PaymentPending(
                "virtual-payment refund outcome requires reconciliation"
            ) from exc
        self._repository.mark_refund_provider_state(
            refund=refund,
            state="processing",
            provider_refund_id=submission.refund_wx_order_id,
            provider_status="PROCESSING",
            processed_at=None,
        )
        return WechatRefundResponse(
            refund=_refund_view(
                self._repository.refund_by_number(out_refund_no=refund.out_refund_no)
            ),
            entitlement=self._entitlement_service.get_entitlement(user_id=user_id),
        )

    def verify_event_endpoint(self, *, query: Mapping[str, str]) -> str:
        return self._gateway.verify_endpoint(query=query)

    def process_event(
        self, *, query: Mapping[str, str], body: bytes
    ) -> dict[str, object]:
        decoded = self._gateway.decode_message(query=query, body=body)
        payload = decoded.payload
        event = _required_string(payload, "Event")
        if event not in {
            "xpay_goods_deliver_notify",
            "xpay_refund_notify",
            "xpay_subscribe_ios_refund_query_notify",
        }:
            raise WechatVirtualPaymentProtocolError(
                "unexpected WeChat virtual-payment event"
            )
        notification_id = _event_id(event=event, payload=payload)
        inserted = self._repository.record_notification(
            notification_id=notification_id,
            event_type=event,
            platform_serial=str(payload.get("CreateTime", "0")),
            body_sha256=sha256(decoded.plaintext).hexdigest(),
            resource_type="wechat-safe-mode-event",
            resource=payload,
            received_at=self._now(),
        )
        if not inserted and self._repository.notification_processed(
            notification_id=notification_id
        ):
            response = self._repository.notification_response(
                notification_id=notification_id
            )
            return self._gateway.encode_message_response(
                plaintext=response,
                timestamp=decoded.timestamp,
                nonce=decoded.nonce,
            )
        try:
            response = self._apply_event(event=event, payload=payload)
            self._repository.mark_notification_processed(
                notification_id=notification_id,
                response=response,
            )
        except Exception as exc:
            self._repository.rollback()
            self._repository.mark_notification_processed(
                notification_id=notification_id,
                error_code=type(exc).__name__,
            )
            raise
        return self._gateway.encode_message_response(
            plaintext=response,
            timestamp=decoded.timestamp,
            nonce=decoded.nonce,
        )

    def reconcile_pending(self) -> dict[str, int]:
        reconciled = 0
        closed = 0
        refunds_reconciled = 0
        visited: set[UUID] = set()
        for order in self._repository.pending_orders(now=self._now()):
            if order.payment_channel != "wechat_virtual_payment":
                continue
            result = self._reconcile_order(order, include_refunds=True)
            visited.add(order.order_id)
            if result.paid_at is not None:
                reconciled += 1
            elif result.state == "closed":
                closed += 1
        for order in self._repository.virtual_refund_orders():
            if order.order_id in visited:
                continue
            before = order.refunded_minor
            result = self._reconcile_order(order, include_refunds=True)
            if result.refunded_minor > before:
                refunds_reconciled += 1
        return {
            "ordersReconciled": reconciled,
            "ordersClosed": closed,
            "refundsReconciled": refunds_reconciled,
        }

    def _reconcile_order(
        self, order: OrderRecord, *, include_refunds: bool = True
    ) -> OrderRecord:
        if order.payment_channel != "wechat_virtual_payment":
            raise Stage8CommerceConflict("order does not use WeChat virtual payment")
        if order.state in {"refunded", "closed"}:
            return order
        if (
            not include_refunds
            and order.state in {"paid", "partially_refunded", "refund_processing"}
        ):
            return order
        provider = self._gateway.query_order(
            open_id=order.payer_open_id,
            out_trade_no=order.out_trade_no,
        )
        self._validate_provider_order(order=order, provider=provider)
        if provider.status in {2, 3, 4}:
            if provider.paid_at is None:
                raise WechatVirtualPaymentProtocolError(
                    "paid virtual order is missing paid_time"
                )
            self._repository.apply_paid_order(
                order=order,
                transaction_id=f"xpay-order:{order.out_trade_no}",
                paid_at=provider.paid_at,
                provider_trade_state=f"XPAY_STATUS_{provider.status}",
                service_days=int(order.terms_snapshot["serviceDays"]),
                provider_order_type=provider.order_type,
                provider_wx_order_id=provider.wx_order_id,
                provider_channel_order_id=provider.channel_order_id,
                provider_delivery_state=(
                    "provided" if provider.status == 4 else "pending_ack"
                ),
            )
            if (
                provider.status in {2, 3}
                and order.provider_delivery_state != "provided"
            ):
                self._gateway.notify_goods_provided(
                    out_trade_no=order.out_trade_no,
                    wx_order_id=provider.wx_order_id,
                )
                self._repository.mark_virtual_delivery_state(
                    order_id=order.order_id,
                    provider_delivery_state="provided",
                )
        elif provider.status in {5, 8}:
            self._apply_provider_refund_from_query(order=order, provider=provider)
        elif provider.status == 7:
            refund = self._not_suitable_refund(order)
            if refund is not None and refund.state != "abnormal":
                self._repository.mark_refund_provider_state(
                    refund=refund,
                    state="abnormal",
                    provider_refund_id=None,
                    provider_status="XPAY_REFUND_FAILED",
                    processed_at=None,
                )
        elif provider.status == 6:
            self._repository.mark_order_closed(
                order_id=order.order_id,
                at=self._now(),
            )
        elif provider.status not in {0, 1}:
            raise WechatVirtualPaymentProtocolError(
                "WeChat returned an unknown virtual-payment order status"
            )
        return self._repository.order_by_trade_no(out_trade_no=order.out_trade_no)

    def _apply_provider_refund_from_query(
        self, *, order: OrderRecord, provider: VirtualOrderResult
    ) -> None:
        if provider.paid_at is None:
            raise WechatVirtualPaymentProtocolError(
                "refunded virtual order is missing paid_time"
            )
        if order.paid_at is None:
            self._repository.apply_paid_order(
                order=order,
                transaction_id=f"xpay-order:{order.out_trade_no}",
                paid_at=provider.paid_at,
                provider_trade_state=f"XPAY_STATUS_{provider.status}",
                service_days=int(order.terms_snapshot["serviceDays"]),
                provider_order_type=provider.order_type,
                provider_wx_order_id=provider.wx_order_id,
                provider_channel_order_id=provider.channel_order_id,
                provider_delivery_state="provided",
            )
            order = self._repository.order_by_trade_no(
                out_trade_no=order.out_trade_no
            )
        refunded_minor = provider.paid_fee - provider.left_fee
        if refunded_minor <= 0 or refunded_minor > order.amount_minor:
            raise WechatVirtualPaymentProtocolError(
                "provider refund balance is inconsistent with the local order"
            )
        refund = self._not_suitable_refund(order)
        if refund is None:
            refund = self._repository.create_refund(
                order=order,
                command_key=f"provider-query-refund-{order.out_trade_no}",
                request_fingerprint=_fingerprint(
                    {
                        "orderId": str(order.order_id),
                        "refundFee": refunded_minor,
                        "providerStatus": provider.status,
                    }
                ),
                refund_kind="not_suitable",
                reason="provider query confirmed virtual-payment refund",
                amount_minor=refunded_minor,
                policy_version="founder-refund@v2",
                now=self._now(),
            )
        elif refund.amount_minor != refunded_minor:
            raise WechatVirtualPaymentProtocolError(
                "provider refund amount does not match the local refund intent"
            )
        if refund.state != "succeeded":
            self._repository.mark_refund_provider_state(
                refund=refund,
                state="succeeded",
                provider_refund_id=None,
                provider_status=f"XPAY_STATUS_{provider.status}",
                processed_at=self._now(),
            )

    def _not_suitable_refund(self, order: OrderRecord) -> RefundRecord | None:
        return next(
            (
                item
                for item in self._repository.list_refunds(order_id=order.order_id)
                if item.refund_kind == "not_suitable"
            ),
            None,
        )

    def _validate_provider_order(
        self, *, order: OrderRecord, provider: VirtualOrderResult
    ) -> None:
        if provider.out_trade_no != order.out_trade_no:
            raise WechatVirtualPaymentProtocolError("provider order number mismatch")
        if provider.order_fee != order.amount_minor:
            raise WechatVirtualPaymentProtocolError("provider order amount mismatch")
        if (
            provider.status in {2, 3, 4, 5, 8}
            and provider.paid_fee != order.amount_minor
        ):
            raise WechatVirtualPaymentProtocolError("provider paid amount mismatch")
        if provider.left_fee < 0 or provider.left_fee > provider.paid_fee:
            raise WechatVirtualPaymentProtocolError(
                "provider refundable balance is invalid"
            )
        if order.client_platform == "ios":
            allowed_types = {7, 8} if provider.status in {5, 8} else {7}
            if provider.order_type not in allowed_types:
                raise WechatVirtualPaymentProtocolError(
                    "iOS order was not routed through Apple payment"
                )
        if order.client_platform != "ios" and provider.order_type in {7, 8}:
            raise WechatVirtualPaymentProtocolError(
                "non-iOS order unexpectedly used Apple payment"
            )

    def _apply_event(
        self, *, event: str, payload: Mapping[str, Any]
    ) -> dict[str, object]:
        if event == "xpay_goods_deliver_notify":
            order = self._repository.order_by_trade_no(
                out_trade_no=_required_string(payload, "OutTradeNo")
            )
            if _required_int(payload, "Env") != self._gateway.env:
                raise WechatVirtualPaymentProtocolError(
                    "delivery event environment does not match runtime"
                )
            open_id = _required_string(payload, "OpenId")
            if not hmac.compare_digest(open_id, order.payer_open_id):
                raise WechatVirtualPaymentProtocolError(
                    "delivery event payer does not match order"
                )
            goods = _required_mapping(payload, "GoodsInfo")
            if _required_string(goods, "ProductId") != order.product_id:
                raise WechatVirtualPaymentProtocolError(
                    "delivery event product does not match order"
                )
            if _required_int(goods, "Quantity") != 1:
                raise WechatVirtualPaymentProtocolError(
                    "delivery event quantity does not match order"
                )
            if _required_int(goods, "ActualPrice") != order.amount_minor:
                raise WechatVirtualPaymentProtocolError(
                    "delivery event amount does not match order"
                )
            if _required_string(goods, "Attach") != f"order:{order.order_id}":
                raise WechatVirtualPaymentProtocolError(
                    "delivery event attachment does not match order"
                )
            provider_info = payload.get("WeChatPayInfo")
            paid_time = (
                _required_int(provider_info, "PaidTime")
                if isinstance(provider_info, dict)
                and provider_info.get("PaidTime") is not None
                else int(payload.get("CreateTime", 0))
            )
            if paid_time <= 0:
                raise WechatVirtualPaymentProtocolError(
                    "delivery event is missing payment time"
                )
            self._repository.apply_paid_order(
                order=order,
                transaction_id=f"xpay-order:{order.out_trade_no}",
                paid_at=datetime.fromtimestamp(paid_time, UTC),
                provider_trade_state="XPAY_DELIVERY_PUSH",
                service_days=int(order.terms_snapshot["serviceDays"]),
                provider_order_type=7 if order.client_platform == "ios" else 0,
                provider_channel_order_id=(
                    _optional_string(provider_info.get("MchOrderNo"))
                    if isinstance(provider_info, dict)
                    else None
                ),
                provider_delivery_state="provided",
            )
            return {"ErrCode": 0, "ErrMsg": "success"}
        if event == "xpay_refund_notify":
            order = self._repository.order_by_trade_no(
                out_trade_no=_required_string(payload, "MchOrderId")
            )
            if not hmac.compare_digest(
                _required_string(payload, "OpenId"), order.payer_open_id
            ):
                raise WechatVirtualPaymentProtocolError(
                    "refund event payer does not match order"
                )
            refund_fee = _required_int(payload, "RefundFee")
            refund = next(
                (
                    item
                    for item in self._repository.list_refunds(order_id=order.order_id)
                    if item.refund_kind == "not_suitable"
                ),
                None,
            )
            provider_refund_id = _required_string(payload, "WxRefundId")
            if refund is not None and refund.state == "succeeded":
                if (
                    refund.amount_minor != refund_fee
                    or refund.provider_refund_id != provider_refund_id
                ):
                    raise WechatVirtualPaymentProtocolError(
                        "completed refund was replayed with different provider facts"
                    )
                return {"ErrCode": 0, "ErrMsg": "success"}
            if refund_fee <= 0 or refund_fee > order.amount_minor - order.refunded_minor:
                raise WechatVirtualPaymentProtocolError(
                    "refund event amount exceeds the locally refundable balance"
                )
            if refund is None:
                refund = self._repository.create_refund(
                    order=order,
                    command_key=f"provider-refund-{_event_id(event=event, payload=payload)[:32]}",
                    request_fingerprint=_fingerprint(
                        {
                            "orderId": str(order.order_id),
                            "refundFee": refund_fee,
                            "providerEvent": "xpay_refund_notify",
                        }
                    ),
                    refund_kind="not_suitable",
                    reason="provider refund event",
                    amount_minor=refund_fee,
                    policy_version="founder-refund@v2",
                    now=self._now(),
                )
            elif refund.amount_minor != refund_fee:
                raise WechatVirtualPaymentProtocolError(
                    "refund event amount does not match the local refund intent"
                )
            if (
                order.client_platform != "ios"
                and _required_string(payload, "MchRefundId")
                != refund.out_refund_no
            ):
                raise WechatVirtualPaymentProtocolError(
                    "refund event number does not match the local refund request"
                )
            succeeded = _required_int(payload, "RetCode") == 0
            self._repository.mark_refund_provider_state(
                refund=refund,
                state="succeeded" if succeeded else "abnormal",
                provider_refund_id=provider_refund_id,
                provider_status="SUCCESS" if succeeded else "ABNORMAL",
                processed_at=self._now() if succeeded else None,
            )
            return {"ErrCode": 0, "ErrMsg": "success"}
        return self._event_response(event=event, payload=payload)

    def _event_response(
        self, *, event: str, payload: Mapping[str, Any]
    ) -> dict[str, object]:
        if event != "xpay_subscribe_ios_refund_query_notify":
            return {"ErrCode": 0, "ErrMsg": "success"}
        pay_order_id = _required_string(payload, "pay_order_id")
        try:
            order = self._repository.order_by_trade_no(out_trade_no=pay_order_id)
        except Stage8CommerceNotFound:
            return {
                "result_code": 1,
                "result_info": "unknown_order",
                "evidence": "The refund inquiry cannot be bound to a verified local order.",
            }
        if (
            order.payment_channel != "wechat_virtual_payment"
            or order.client_platform != "ios"
            or _required_string(payload, "product_id") != order.product_id
            or _required_string(payload, "p_count") != "1"
        ):
            return {
                "result_code": 1,
                "result_info": "order_evidence_mismatch",
                "evidence": (
                    "The inquiry platform, product, or quantity does not match "
                    "the immutable local order snapshot."
                ),
            }
        eligible, _ = self._refund_eligibility(order)
        return {
            "result_code": 0 if eligible else 1,
            "result_info": (
                "recommend_refund_under_founder_refund_v2"
                if eligible
                else "outside_published_refund_window"
            ),
            "evidence": (
                f"order={order.out_trade_no};policy=founder-refund@v2;"
                f"paid_at={order.paid_at.isoformat() if order.paid_at else 'missing'}"
            ),
        }

    def _refund_eligibility(self, order: OrderRecord) -> tuple[bool, int]:
        remaining = max(order.amount_minor - order.refunded_minor, 0)
        eligible = (
            order.paid_at is not None
            and self._now() <= order.paid_at + timedelta(days=30)
            and remaining > 0
            and order.state in {"paid", "partially_refunded", "refund_processing"}
        )
        return eligible, remaining


def _order_view(
    order: OrderRecord,
    *,
    virtual_payment: VirtualPaymentRequest | None = None,
) -> WechatOrderView:
    return WechatOrderView(
        order_id=order.order_id,
        out_trade_no=order.out_trade_no,
        offer_version_id=order.offer_version_id,
        title=order.offer_title,
        currency="CNY",
        amount_minor=order.amount_minor,
        refunded_minor=order.refunded_minor,
        state=order.state,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        created_at=order.created_at,
        terms_snapshot_sha256=order.terms_snapshot_sha256,
        payment=None,
        payment_channel="wechat_virtual_payment",
        client_platform=order.client_platform,
        virtual_payment=(
            WechatVirtualPaymentParameters(
                sign_data=virtual_payment.sign_data,
                pay_sig=virtual_payment.pay_sig,
                signature=virtual_payment.signature,
                mode="short_series_goods",
            )
            if virtual_payment is not None
            else None
        ),
    )


def _refund_view(refund: RefundRecord) -> WechatRefundView:
    return WechatRefundView(
        refund_id=refund.refund_id,
        order_id=refund.order_id,
        out_refund_no=refund.out_refund_no,
        refund_kind=refund.refund_kind,
        amount_minor=refund.amount_minor,
        currency="CNY",
        state=refund.state,
        policy_version=refund.policy_version,
        requested_at=refund.requested_at,
        processed_at=refund.processed_at,
    )


def _validate_policy_acceptances(
    offer: OfferRecord, accepted: PolicyAcceptances
) -> None:
    expected = {
        "service_terms_version": offer.service_terms_version,
        "refund_policy_version": offer.refund_policy_version,
        "reward_policy_version": offer.reward_policy_version,
        "marketing_policy_version": offer.marketing_policy_version,
    }
    if any(getattr(accepted, key) != value for key, value in expected.items()):
        raise Stage8CommerceConflict(
            "commercial policy version changed; accept current policies"
        )


def _offer_policy_versions(offer: OfferRecord) -> tuple[str, ...]:
    return (
        offer.service_terms_version,
        offer.refund_policy_version,
        offer.reward_policy_version,
        offer.marketing_policy_version,
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def _fingerprint(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _hmac_sha256(key: str, message: str) -> str:
    return hmac.new(
        key.encode("utf-8"),
        message.encode("utf-8"),
        sha256,
    ).hexdigest()


def _wechat_sha1(*values: str) -> str:
    return sha1("".join(sorted(values)).encode("utf-8")).hexdigest()


def _event_id(*, event: str, payload: Mapping[str, Any]) -> str:
    identity = {
        "event": event,
        "outTradeNo": payload.get("OutTradeNo"),
        "mchOrderId": payload.get("MchOrderId"),
        "payOrderId": payload.get("pay_order_id"),
        "wxRefundId": payload.get("WxRefundId"),
        "channelBill": payload.get("channel_bill"),
        "createTime": payload.get("CreateTime"),
    }
    return "xpay_" + _fingerprint(identity)


def _query_value(query: Mapping[str, str], key: str) -> str:
    value = query.get(key)
    if not isinstance(value, str) or not value:
        raise WechatVirtualPaymentProtocolError(
            f"WeChat message query field {key} is missing"
        )
    return value


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise WechatVirtualPaymentProtocolError(
            f"WeChat virtual-payment field {key} must be an object"
        )
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise WechatVirtualPaymentProtocolError(
            f"WeChat virtual-payment field {key} is missing"
        )
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WechatVirtualPaymentProtocolError(
            f"WeChat virtual-payment field {key} must be an integer"
        )
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
