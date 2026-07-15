from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import json
from pathlib import Path
from typing import Any

import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.x509.oid import SignatureAlgorithmOID

from app.core.config import Settings


class AppStoreVerificationConfigurationError(RuntimeError):
    pass


class AppStoreTransactionVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedAppStoreTransaction:
    product_id: str
    transaction_id: str
    original_transaction_id: str
    web_order_line_item_id: str | None
    environment: str
    purchase_date: datetime
    expires_date: datetime | None
    revocation_date: datetime | None
    app_account_token: str | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class VerifiedAppStoreNotification:
    notification_uuid: str
    notification_type: str
    subtype: str | None
    environment: str
    signed_transaction_info: str | None
    raw_payload: dict[str, Any]


class AppStoreSignedTransactionVerifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def verify(self, signed_transaction_info: str) -> VerifiedAppStoreTransaction:
        payload = self._verify_jws_payload(signed_transaction_info)
        return self._verified_transaction_from_payload(payload)

    def verify_notification(self, signed_payload: str) -> VerifiedAppStoreNotification:
        payload = self._verify_jws_payload(signed_payload)
        notification_uuid = str(payload.get("notificationUUID") or "")
        notification_type = str(payload.get("notificationType") or "")
        subtype = _optional_string(payload.get("subtype"))
        data = payload.get("data")
        data_payload = data if isinstance(data, dict) else {}
        bundle_id = str(data_payload.get("bundleId") or "")
        if bundle_id != self._settings.bundle_id:
            raise AppStoreTransactionVerificationError("App Store notification bundleId does not match")
        environment = str(data_payload.get("environment") or "")
        signed_transaction_info = _optional_string(data_payload.get("signedTransactionInfo"))
        if not notification_uuid or not notification_type:
            raise AppStoreTransactionVerificationError("App Store notification identifiers are incomplete")
        return VerifiedAppStoreNotification(
            notification_uuid=notification_uuid,
            notification_type=notification_type,
            subtype=subtype,
            environment=environment,
            signed_transaction_info=signed_transaction_info,
            raw_payload=payload,
        )

    def _verify_jws_payload(self, signed_payload: str) -> dict[str, Any]:
        root_certificates = self._load_root_certificates()
        header = jwt.get_unverified_header(signed_payload)
        if header.get("alg") != "ES256":
            raise AppStoreTransactionVerificationError("Unsupported App Store JWS algorithm")
        chain = _certificate_chain_from_header(header)
        if not chain:
            raise AppStoreTransactionVerificationError("App Store JWS certificate chain missing")
        _verify_certificate_chain(chain=chain, roots=root_certificates)
        payload = jwt.decode(
            signed_payload,
            key=chain[0].public_key(),
            algorithms=["ES256"],
            options={
                "verify_aud": False,
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
        if not isinstance(payload, dict):
            raise AppStoreTransactionVerificationError("App Store JWS payload is invalid")
        return payload

    def _load_root_certificates(self) -> list[x509.Certificate]:
        paths = [
            item.strip()
            for item in self._settings.app_store_root_certificate_paths.split(",")
            if item.strip()
        ]
        if not paths:
            raise AppStoreVerificationConfigurationError(
                "APP_STORE_ROOT_CERTIFICATE_PATHS is required for App Store transaction verification"
            )
        roots: list[x509.Certificate] = []
        for path in paths:
            cert_path = Path(path).expanduser()
            if not cert_path.exists():
                raise AppStoreVerificationConfigurationError(
                    f"App Store root certificate not found: {cert_path}"
                )
            data = cert_path.read_bytes()
            try:
                roots.append(x509.load_pem_x509_certificate(data))
            except ValueError:
                roots.append(x509.load_der_x509_certificate(data))
        return roots

    def _verified_transaction_from_payload(
        self,
        payload: dict[str, Any],
    ) -> VerifiedAppStoreTransaction:
        bundle_id = str(payload.get("bundleId") or "")
        if bundle_id != self._settings.bundle_id:
            raise AppStoreTransactionVerificationError("App Store bundleId does not match")

        product_id = str(payload.get("productId") or "")
        transaction_id = str(payload.get("transactionId") or "")
        original_transaction_id = str(payload.get("originalTransactionId") or "")
        environment = str(payload.get("environment") or "")
        purchase_date = _transaction_datetime(payload.get("purchaseDate"), field="purchaseDate")
        expires_date = _optional_transaction_datetime(payload.get("expiresDate"))
        revocation_date = _optional_transaction_datetime(payload.get("revocationDate"))
        app_account_token = _optional_string(payload.get("appAccountToken"))

        if not product_id or not transaction_id or not original_transaction_id or not environment:
            raise AppStoreTransactionVerificationError("App Store transaction identifiers are incomplete")
        return VerifiedAppStoreTransaction(
            product_id=product_id,
            transaction_id=transaction_id,
            original_transaction_id=original_transaction_id,
            web_order_line_item_id=_optional_string(payload.get("webOrderLineItemId")),
            environment=environment,
            purchase_date=purchase_date,
            expires_date=expires_date,
            revocation_date=revocation_date,
            app_account_token=app_account_token,
            raw_payload=payload,
        )


def _certificate_chain_from_header(header: dict[str, Any]) -> list[x509.Certificate]:
    raw_chain = header.get("x5c")
    if not isinstance(raw_chain, list):
        return []
    certificates: list[x509.Certificate] = []
    for raw_cert in raw_chain:
        if not isinstance(raw_cert, str):
            raise AppStoreTransactionVerificationError("App Store JWS certificate is invalid")
        try:
            cert_bytes = base64.b64decode(raw_cert)
            certificates.append(x509.load_der_x509_certificate(cert_bytes))
        except ValueError as exc:
            raise AppStoreTransactionVerificationError(
                "App Store JWS certificate cannot be decoded"
            ) from exc
    return certificates


def _verify_certificate_chain(
    *,
    chain: list[x509.Certificate],
    roots: list[x509.Certificate],
) -> None:
    now = datetime.now(UTC)
    for certificate in chain:
        not_valid_before = certificate.not_valid_before_utc
        not_valid_after = certificate.not_valid_after_utc
        if not_valid_before > now or not_valid_after < now:
            raise AppStoreTransactionVerificationError("App Store JWS certificate is not currently valid")

    for index, certificate in enumerate(chain[:-1]):
        issuer = chain[index + 1]
        if certificate.issuer != issuer.subject:
            raise AppStoreTransactionVerificationError("App Store JWS certificate issuer mismatch")
        _verify_child_signature(child=certificate, issuer=issuer)

    chain_anchor = chain[-1]
    if any(root.fingerprint(SHA256()) == chain_anchor.fingerprint(SHA256()) for root in roots):
        return

    signing_root = next(
        (root for root in roots if chain_anchor.issuer == root.subject),
        None,
    )
    if signing_root is None:
        raise AppStoreTransactionVerificationError("App Store JWS chain is not anchored to configured Apple roots")
    _verify_child_signature(child=chain_anchor, issuer=signing_root)


def _verify_child_signature(*, child: x509.Certificate, issuer: x509.Certificate) -> None:
    public_key = issuer.public_key()
    try:
        if child.signature_algorithm_oid == SignatureAlgorithmOID.ECDSA_WITH_SHA256:
            public_key.verify(
                child.signature,
                child.tbs_certificate_bytes,
                ECDSA(SHA256()),
            )
        else:
            public_key.verify(
                child.signature,
                child.tbs_certificate_bytes,
                padding.PKCS1v15(),
                child.signature_hash_algorithm,
            )
    except InvalidSignature as exc:
        raise AppStoreTransactionVerificationError("App Store JWS certificate signature invalid") from exc


def _transaction_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    if isinstance(value, str) and value.isdigit():
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    raise AppStoreTransactionVerificationError(f"App Store transaction {field} is invalid")


def _optional_transaction_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _transaction_datetime(value, field="date")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


class StaticAppStoreTransactionVerifier:
    def __init__(
        self,
        transaction: VerifiedAppStoreTransaction,
        notification: VerifiedAppStoreNotification | None = None,
    ) -> None:
        self._transaction = transaction
        self._notification = notification

    def verify(self, signed_transaction_info: str) -> VerifiedAppStoreTransaction:
        return self._transaction

    def verify_notification(self, signed_payload: str) -> VerifiedAppStoreNotification:
        if self._notification is None:
            raise AppStoreTransactionVerificationError("Synthetic notification is not configured")
        return self._notification
