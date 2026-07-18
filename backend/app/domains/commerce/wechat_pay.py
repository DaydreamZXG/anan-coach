from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import secrets
import time
from collections.abc import Callable
from typing import Any, Mapping, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509 import load_pem_x509_certificate
import httpx

from app.core.config import Settings


class WechatPayConfigurationError(RuntimeError):
    pass


class WechatPayProtocolError(RuntimeError):
    pass


class WechatPayTransportUncertain(RuntimeError):
    """The provider may have accepted a mutating request; query before retrying."""


@dataclass(frozen=True)
class WechatPrepayResult:
    prepay_id: str
    time_stamp: str
    nonce_str: str
    package: str
    sign_type: str
    pay_sign: str


@dataclass(frozen=True)
class WechatOrderQueryResult:
    out_trade_no: str
    trade_state: str
    transaction_id: str | None
    success_time: datetime | None
    amount_minor: int
    currency: str
    payer_open_id: str | None


@dataclass(frozen=True)
class WechatRefundResult:
    refund_id: str
    out_refund_no: str
    status: str
    amount_minor: int
    currency: str
    success_time: datetime | None


@dataclass(frozen=True)
class WechatNotification:
    notification_id: str
    event_type: str
    resource_type: str
    platform_serial: str
    resource: dict[str, Any]


class WechatPayGateway(Protocol):
    def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        description: str,
        payer_open_id: str,
        amount_minor: int,
        currency: str,
        expires_at: datetime,
        attach: str,
    ) -> WechatPrepayResult: ...

    def sign_prepay(self, *, prepay_id: str) -> WechatPrepayResult: ...

    def query_order(self, *, out_trade_no: str) -> WechatOrderQueryResult: ...

    def close_order(self, *, out_trade_no: str) -> None: ...

    def create_refund(
        self,
        *,
        out_trade_no: str,
        out_refund_no: str,
        amount_minor: int,
        total_minor: int,
        currency: str,
        reason: str,
    ) -> WechatRefundResult: ...

    def query_refund(self, *, out_refund_no: str) -> WechatRefundResult: ...

    def verify_notification(
        self, *, headers: Mapping[str, str], body: bytes
    ) -> WechatNotification: ...


class WechatPayApiV3Client:
    """Strict direct-merchant WeChat Pay API v3 client.

    Every request and response is signed. Mutating transport failures are exposed
    as uncertain so callers reconcile by merchant order/refund number before any
    further state transition.
    """

    _base_url = "https://api.mch.weixin.qq.com"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        now_epoch: Callable[[], float] | None = None,
    ) -> None:
        required = {
            "WECHAT_PAY_MCH_ID": settings.wechat_pay_mch_id,
            "WECHAT_PAY_MERCHANT_SERIAL": settings.wechat_pay_merchant_serial,
            "WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH": (
                settings.wechat_pay_merchant_private_key_path
            ),
            "WECHAT_PAY_API_V3_KEY": settings.wechat_pay_api_v3_key,
            "WECHAT_PAY_PLATFORM_CERTIFICATES_JSON": (
                settings.wechat_pay_platform_certificates_json
            ),
            "WECHAT_PAY_NOTIFY_URL": settings.wechat_pay_notify_url,
            "WECHAT_PAY_REFUND_NOTIFY_URL": settings.wechat_pay_refund_notify_url,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise WechatPayConfigurationError(
                f"WeChat Pay configuration is incomplete: {', '.join(missing)}"
            )
        if len(settings.wechat_pay_api_v3_key.encode("utf-8")) != 32:
            raise WechatPayConfigurationError(
                "WECHAT_PAY_API_V3_KEY must contain exactly 32 UTF-8 bytes"
            )
        self._mch_id = settings.wechat_pay_mch_id
        self._merchant_serial = settings.wechat_pay_merchant_serial
        self._app_id = settings.wechat_mini_app_id
        self._notify_url = settings.wechat_pay_notify_url
        self._refund_notify_url = settings.wechat_pay_refund_notify_url
        self._api_v3_key = settings.wechat_pay_api_v3_key.encode("utf-8")
        self._private_key = _load_private_key(
            settings.wechat_pay_merchant_private_key_path
        )
        self._platform_keys = _load_platform_public_keys(
            settings.wechat_pay_platform_certificates_json
        )
        self._client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))
        self._now_epoch = now_epoch or time.time

    def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        description: str,
        payer_open_id: str,
        amount_minor: int,
        currency: str,
        expires_at: datetime,
        attach: str,
    ) -> WechatPrepayResult:
        payload = {
            "appid": self._app_id,
            "mchid": self._mch_id,
            "description": description,
            "out_trade_no": out_trade_no,
            "time_expire": _rfc3339(expires_at),
            "attach": attach,
            "notify_url": self._notify_url,
            "amount": {"total": amount_minor, "currency": currency},
            "payer": {"openid": payer_open_id},
        }
        response = self._request_json(
            "POST", "/v3/pay/transactions/jsapi", payload, mutating=True
        )
        prepay_id = _required_string(response, "prepay_id")
        return self.sign_prepay(prepay_id=prepay_id)

    def sign_prepay(self, *, prepay_id: str) -> WechatPrepayResult:
        if not prepay_id.strip():
            raise WechatPayProtocolError("prepay_id is required")
        timestamp = str(int(self._now_epoch()))
        nonce = secrets.token_urlsafe(18)
        package = f"prepay_id={prepay_id}"
        signature = self._sign(
            f"{self._app_id}\n{timestamp}\n{nonce}\n{package}\n".encode("utf-8")
        )
        return WechatPrepayResult(
            prepay_id=prepay_id,
            time_stamp=timestamp,
            nonce_str=nonce,
            package=package,
            sign_type="RSA",
            pay_sign=signature,
        )

    def query_order(self, *, out_trade_no: str) -> WechatOrderQueryResult:
        response = self._request_json(
            "GET",
            f"/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={self._mch_id}",
            None,
            mutating=False,
        )
        amount = _required_mapping(response, "amount")
        payer = response.get("payer")
        return WechatOrderQueryResult(
            out_trade_no=_required_string(response, "out_trade_no"),
            trade_state=_required_string(response, "trade_state"),
            transaction_id=_optional_string(response.get("transaction_id")),
            success_time=_optional_datetime(response.get("success_time")),
            amount_minor=_required_int(amount, "total"),
            currency=_required_string(amount, "currency"),
            payer_open_id=(
                _optional_string(payer.get("openid"))
                if isinstance(payer, dict)
                else None
            ),
        )

    def close_order(self, *, out_trade_no: str) -> None:
        self._request_json(
            "POST",
            f"/v3/pay/transactions/out-trade-no/{out_trade_no}/close",
            {"mchid": self._mch_id},
            mutating=True,
            allow_empty=True,
        )

    def create_refund(
        self,
        *,
        out_trade_no: str,
        out_refund_no: str,
        amount_minor: int,
        total_minor: int,
        currency: str,
        reason: str,
    ) -> WechatRefundResult:
        response = self._request_json(
            "POST",
            "/v3/refund/domestic/refunds",
            {
                "out_trade_no": out_trade_no,
                "out_refund_no": out_refund_no,
                "reason": reason,
                "notify_url": self._refund_notify_url,
                "amount": {
                    "refund": amount_minor,
                    "total": total_minor,
                    "currency": currency,
                },
            },
            mutating=True,
        )
        return _refund_result(response)

    def query_refund(self, *, out_refund_no: str) -> WechatRefundResult:
        response = self._request_json(
            "GET",
            f"/v3/refund/domestic/refunds/{out_refund_no}",
            None,
            mutating=False,
        )
        return _refund_result(response)

    def verify_notification(
        self, *, headers: Mapping[str, str], body: bytes
    ) -> WechatNotification:
        normalized = {key.lower(): value for key, value in headers.items()}
        serial = _required_header(normalized, "wechatpay-serial")
        timestamp = _required_header(normalized, "wechatpay-timestamp")
        nonce = _required_header(normalized, "wechatpay-nonce")
        signature = _required_header(normalized, "wechatpay-signature")
        self._verify_timestamp(timestamp)
        self._verify_signature(
            serial=serial,
            message=timestamp.encode("ascii")
            + b"\n"
            + nonce.encode("utf-8")
            + b"\n"
            + body
            + b"\n",
            signature_b64=signature,
        )
        try:
            envelope = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WechatPayProtocolError("notification body is not valid JSON") from exc
        if not isinstance(envelope, dict):
            raise WechatPayProtocolError("notification body must be an object")
        resource = _required_mapping(envelope, "resource")
        algorithm = _required_string(resource, "algorithm")
        if algorithm != "AEAD_AES_256_GCM":
            raise WechatPayProtocolError("unsupported notification encryption algorithm")
        try:
            clear = AESGCM(self._api_v3_key).decrypt(
                _required_string(resource, "nonce").encode("utf-8"),
                b64decode(_required_string(resource, "ciphertext"), validate=True),
                _required_text(resource, "associated_data").encode("utf-8"),
            )
            decoded = json.loads(clear)
        except Exception as exc:
            raise WechatPayProtocolError("notification resource decryption failed") from exc
        if not isinstance(decoded, dict):
            raise WechatPayProtocolError("notification resource must be an object")
        return WechatNotification(
            notification_id=_required_string(envelope, "id"),
            event_type=_required_string(envelope, "event_type"),
            resource_type=_required_string(envelope, "resource_type"),
            platform_serial=serial,
            resource=decoded,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        mutating: bool,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            if payload is not None
            else b""
        )
        timestamp = str(int(self._now_epoch()))
        nonce = secrets.token_urlsafe(18)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n".encode("utf-8") + body + b"\n"
        authorization = (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self._mch_id}",nonce_str="{nonce}",'
            f'signature="{self._sign(message)}",timestamp="{timestamp}",'
            f'serial_no="{self._merchant_serial}"'
        )
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                content=body or None,
                headers={
                    "Authorization": authorization,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "gongkao-dazi-stage8/1.0",
                },
            )
        except httpx.TransportError as exc:
            if mutating:
                raise WechatPayTransportUncertain(
                    "WeChat Pay mutating request outcome is uncertain"
                ) from exc
            raise WechatPayProtocolError("WeChat Pay query transport failed") from exc
        self._verify_response(response)
        if response.status_code not in range(200, 300):
            raise WechatPayProtocolError(
                f"WeChat Pay rejected request with HTTP {response.status_code}"
            )
        if not response.content:
            if allow_empty:
                return {}
            raise WechatPayProtocolError("WeChat Pay response body is empty")
        try:
            decoded = response.json()
        except ValueError as exc:
            raise WechatPayProtocolError("WeChat Pay response is not JSON") from exc
        if not isinstance(decoded, dict):
            raise WechatPayProtocolError("WeChat Pay response must be an object")
        return decoded

    def _verify_response(self, response: httpx.Response) -> None:
        serial = response.headers.get("Wechatpay-Serial")
        timestamp = response.headers.get("Wechatpay-Timestamp")
        nonce = response.headers.get("Wechatpay-Nonce")
        signature = response.headers.get("Wechatpay-Signature")
        if not all((serial, timestamp, nonce, signature)):
            raise WechatPayProtocolError("WeChat Pay response signature headers are missing")
        self._verify_timestamp(str(timestamp))
        self._verify_signature(
            serial=str(serial),
            message=str(timestamp).encode("ascii")
            + b"\n"
            + str(nonce).encode("utf-8")
            + b"\n"
            + response.content
            + b"\n",
            signature_b64=str(signature),
        )

    def _verify_signature(
        self, *, serial: str, message: bytes, signature_b64: str
    ) -> None:
        public_key = self._platform_keys.get(serial)
        if public_key is None:
            raise WechatPayProtocolError(
                "WeChat Pay signature uses an unconfigured platform certificate"
            )
        try:
            public_key.verify(
                b64decode(signature_b64, validate=True),
                message,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:
            raise WechatPayProtocolError("WeChat Pay signature verification failed") from exc

    def _sign(self, message: bytes) -> str:
        return b64encode(
            self._private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        ).decode("ascii")

    def _verify_timestamp(self, value: str) -> None:
        try:
            timestamp = int(value)
        except ValueError as exc:
            raise WechatPayProtocolError("WeChat Pay signature timestamp is invalid") from exc
        if abs(self._now_epoch() - timestamp) > 300:
            raise WechatPayProtocolError("WeChat Pay signed message is outside the replay window")


def _load_private_key(path_value: str) -> rsa.RSAPrivateKey:
    path = Path(path_value)
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:
        raise WechatPayConfigurationError(
            "WeChat Pay merchant private key cannot be loaded"
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise WechatPayConfigurationError(
            "WeChat Pay merchant private key must be RSA 2048 or stronger"
        )
    return key


def _load_platform_public_keys(value: str) -> dict[str, rsa.RSAPublicKey]:
    try:
        mapping = json.loads(value)
    except json.JSONDecodeError as exc:
        raise WechatPayConfigurationError(
            "WECHAT_PAY_PLATFORM_CERTIFICATES_JSON is not valid JSON"
        ) from exc
    if not isinstance(mapping, dict) or not mapping:
        raise WechatPayConfigurationError(
            "At least one WeChat Pay platform certificate is required"
        )
    result: dict[str, rsa.RSAPublicKey] = {}
    for serial, path_value in mapping.items():
        if not isinstance(serial, str) or not isinstance(path_value, str):
            raise WechatPayConfigurationError(
                "WeChat Pay platform certificate mapping must contain string values"
            )
        try:
            certificate = load_pem_x509_certificate(Path(path_value).read_bytes())
            public_key = certificate.public_key()
        except Exception as exc:
            raise WechatPayConfigurationError(
                f"WeChat Pay platform certificate cannot be loaded: {serial}"
            ) from exc
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise WechatPayConfigurationError(
                "WeChat Pay platform certificate must contain an RSA key"
            )
        expected_serial = format(certificate.serial_number, "X")
        if serial.upper().lstrip("0") != expected_serial.upper().lstrip("0"):
            raise WechatPayConfigurationError(
                f"WeChat Pay platform certificate serial mismatch: {serial}"
            )
        current = datetime.now(UTC)
        if not certificate.not_valid_before_utc <= current <= certificate.not_valid_after_utc:
            raise WechatPayConfigurationError(
                f"WeChat Pay platform certificate is not currently valid: {serial}"
            )
        result[serial] = public_key
    return result


def _refund_result(payload: dict[str, Any]) -> WechatRefundResult:
    amount = _required_mapping(payload, "amount")
    return WechatRefundResult(
        refund_id=_required_string(payload, "refund_id"),
        out_refund_no=_required_string(payload, "out_refund_no"),
        status=_required_string(payload, "status"),
        amount_minor=_required_int(amount, "refund"),
        currency=_required_string(amount, "currency"),
        success_time=_optional_datetime(payload.get("success_time")),
    )


def _required_mapping(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise WechatPayProtocolError(f"WeChat Pay field {key} must be an object")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WechatPayProtocolError(f"WeChat Pay field {key} is missing")
    return value


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise WechatPayProtocolError(f"WeChat Pay field {key} must be text")
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WechatPayProtocolError(f"WeChat Pay field {key} must be an integer")
    return value


def _required_header(headers: Mapping[str, str], key: str) -> str:
    value = headers.get(key)
    if not value:
        raise WechatPayProtocolError(f"WeChat Pay header {key} is missing")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise WechatPayProtocolError("WeChat Pay timestamp is invalid") from exc


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
