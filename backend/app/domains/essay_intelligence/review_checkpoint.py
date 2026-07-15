from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings


CHECKPOINT_CONTRACT_VERSION = "essay-review-checkpoint-v1"


class ReviewCheckpointConfigurationError(RuntimeError):
    pass


class ReviewCheckpointContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewCheckpoint:
    call_id: UUID
    payload: dict[str, Any] | None
    output_fingerprint: str
    provider_request_id_header: str | None
    provider_request_id_fingerprint: str | None


class ReviewCheckpointCipher:
    def __init__(
        self,
        *,
        active_key_id: str,
        keys: dict[str, bytes],
    ) -> None:
        if not active_key_id or active_key_id not in keys:
            raise ReviewCheckpointConfigurationError(
                "An active essay checkpoint encryption key is required"
            )
        if any(len(value) != 32 for value in keys.values()):
            raise ReviewCheckpointConfigurationError(
                "Essay checkpoint AES-GCM keys must each contain 32 bytes"
            )
        self._active_key_id = active_key_id
        self._keys = dict(keys)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReviewCheckpointCipher":
        try:
            document = json.loads(settings.ai_essay_checkpoint_keys_json)
            if not isinstance(document, dict) or not document:
                raise ValueError("keyring must be a non-empty object")
            keys = {
                str(key_id): b64decode(str(encoded), validate=True)
                for key_id, encoded in document.items()
            }
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ReviewCheckpointConfigurationError(
                "AI_ESSAY_CHECKPOINT_KEYS_JSON must be a base64 keyring object"
            ) from exc
        return cls(
            active_key_id=settings.ai_essay_checkpoint_active_key_id,
            keys=keys,
        )

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def encrypt(self, *, plaintext: bytes, associated_data: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keys[self._active_key_id]).encrypt(
            nonce,
            plaintext,
            associated_data,
        )
        return nonce, ciphertext

    def decrypt(
        self,
        *,
        key_id: str,
        nonce: bytes,
        ciphertext: bytes,
        associated_data: bytes,
    ) -> bytes:
        key = self._keys.get(key_id)
        if key is None:
            raise ReviewCheckpointContractError(
                "Essay checkpoint references an unavailable encryption key"
            )
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, associated_data)
        except InvalidTag as exc:
            raise ReviewCheckpointContractError(
                "Essay checkpoint authentication failed"
            ) from exc


class EssayReviewCheckpointRepository:
    def __init__(
        self,
        *,
        cipher: ReviewCheckpointCipher,
        ttl: timedelta,
    ) -> None:
        if ttl < timedelta(minutes=30) or ttl > timedelta(days=7):
            raise ReviewCheckpointConfigurationError(
                "Essay checkpoint TTL must be between 30 minutes and 7 days"
            )
        self._cipher = cipher
        self._ttl = ttl

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
    ) -> "EssayReviewCheckpointRepository":
        return cls(
            cipher=ReviewCheckpointCipher.from_settings(settings),
            ttl=timedelta(seconds=settings.ai_essay_checkpoint_ttl_seconds),
        )

    def save(
        self,
        session: Session,
        *,
        job_id: UUID,
        phase: str,
        call_id: UUID,
        payload: dict[str, Any] | None,
        output_fingerprint: str,
        provider_request_id_header: str | None = None,
        provider_request_id_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> None:
        if phase not in {"initial", "repair"}:
            raise ReviewCheckpointContractError("Unsupported review checkpoint phase")
        if len(output_fingerprint) != 64:
            raise ReviewCheckpointContractError(
                "Review checkpoint output fingerprint must be SHA-256"
            )
        if (provider_request_id_header is None) != (
            provider_request_id_fingerprint is None
        ):
            raise ReviewCheckpointContractError(
                "Provider request ID header and fingerprint must appear together"
            )
        if (
            provider_request_id_fingerprint is not None
            and len(provider_request_id_fingerprint) != 64
        ):
            raise ReviewCheckpointContractError(
                "Provider request ID fingerprint must be SHA-256"
            )
        recorded_at = now or datetime.now(UTC)
        document = {
            "contractVersion": CHECKPOINT_CONTRACT_VERSION,
            "payload": payload,
            "outputFingerprint": output_fingerprint,
            "providerRequestIdHeader": provider_request_id_header,
            "providerRequestIdFingerprint": provider_request_id_fingerprint,
        }
        plaintext = _canonical_json(document)
        payload_sha256 = sha256(plaintext).hexdigest()
        associated_data = _associated_data(
            job_id=job_id,
            phase=phase,
            call_id=call_id,
        )
        nonce, ciphertext = self._cipher.encrypt(
            plaintext=plaintext,
            associated_data=associated_data,
        )
        inserted = session.execute(
            text(
                """
                insert into ai_audit.essay_review_checkpoints (
                  checkpoint_id, job_id, phase, call_id, contract_version,
                  key_id, nonce, ciphertext, payload_sha256,
                  created_at, expires_at
                ) values (
                  cast(:checkpoint_id as uuid), cast(:job_id as uuid), :phase,
                  cast(:call_id as uuid), :contract_version, :key_id,
                  :nonce, :ciphertext, :payload_sha256, :created_at, :expires_at
                )
                on conflict (job_id, phase) do nothing
                returning checkpoint_id
                """
            ),
            {
                "checkpoint_id": str(uuid4()),
                "job_id": str(job_id),
                "phase": phase,
                "call_id": str(call_id),
                "contract_version": CHECKPOINT_CONTRACT_VERSION,
                "key_id": self._cipher.active_key_id,
                "nonce": nonce,
                "ciphertext": ciphertext,
                "payload_sha256": payload_sha256,
                "created_at": recorded_at,
                "expires_at": recorded_at + self._ttl,
            },
        ).scalar_one_or_none()
        if inserted is not None:
            return
        existing = session.execute(
            text(
                """
                select call_id, payload_sha256
                  from ai_audit.essay_review_checkpoints
                 where job_id = cast(:job_id as uuid) and phase = :phase
                """
            ),
            {"job_id": str(job_id), "phase": phase},
        ).mappings().one()
        if existing["call_id"] != call_id or existing["payload_sha256"] != payload_sha256:
            raise ReviewCheckpointContractError(
                "An immutable review checkpoint already exists for different output"
            )

    def load(
        self,
        session: Session,
        *,
        job_id: UUID,
        phase: str,
        now: datetime | None = None,
    ) -> ReviewCheckpoint | None:
        row = session.execute(
            text(
                """
                select call_id, contract_version, key_id, nonce, ciphertext,
                       payload_sha256, expires_at
                  from ai_audit.essay_review_checkpoints
                 where job_id = cast(:job_id as uuid) and phase = :phase
                """
            ),
            {"job_id": str(job_id), "phase": phase},
        ).mappings().one_or_none()
        if row is None:
            return None
        loaded_at = now or datetime.now(UTC)
        if row["expires_at"] <= loaded_at:
            session.execute(
                text(
                    "delete from ai_audit.essay_review_checkpoints "
                    "where job_id = cast(:job_id as uuid) and phase = :phase "
                    "and expires_at <= :now"
                ),
                {"job_id": str(job_id), "phase": phase, "now": loaded_at},
            )
            return None
        if row["contract_version"] != CHECKPOINT_CONTRACT_VERSION:
            raise ReviewCheckpointContractError(
                "Unsupported essay checkpoint contract version"
            )
        call_id = UUID(str(row["call_id"]))
        plaintext = self._cipher.decrypt(
            key_id=str(row["key_id"]),
            nonce=bytes(row["nonce"]),
            ciphertext=bytes(row["ciphertext"]),
            associated_data=_associated_data(
                job_id=job_id,
                phase=phase,
                call_id=call_id,
            ),
        )
        if sha256(plaintext).hexdigest() != row["payload_sha256"]:
            raise ReviewCheckpointContractError(
                "Essay checkpoint plaintext fingerprint mismatch"
            )
        try:
            document = json.loads(plaintext)
        except json.JSONDecodeError as exc:
            raise ReviewCheckpointContractError(
                "Essay checkpoint plaintext is not valid JSON"
            ) from exc
        if (
            not isinstance(document, dict)
            or document.get("contractVersion") != CHECKPOINT_CONTRACT_VERSION
            or not isinstance(document.get("outputFingerprint"), str)
            or (
                document.get("payload") is not None
                and not isinstance(document.get("payload"), dict)
            )
            or (
                document.get("providerRequestIdHeader") is None
                and document.get("providerRequestIdFingerprint") is not None
            )
            or (
                document.get("providerRequestIdHeader") is not None
                and (
                    not isinstance(document.get("providerRequestIdHeader"), str)
                    or not isinstance(
                        document.get("providerRequestIdFingerprint"),
                        str,
                    )
                    or len(document["providerRequestIdFingerprint"]) != 64
                )
            )
        ):
            raise ReviewCheckpointContractError(
                "Essay checkpoint plaintext violates its contract"
            )
        return ReviewCheckpoint(
            call_id=call_id,
            payload=document.get("payload"),
            output_fingerprint=document["outputFingerprint"],
            provider_request_id_header=document.get("providerRequestIdHeader"),
            provider_request_id_fingerprint=document.get(
                "providerRequestIdFingerprint"
            ),
        )

    def delete_for_job(self, session: Session, *, job_id: UUID) -> int:
        result = session.execute(
            text(
                "delete from ai_audit.essay_review_checkpoints "
                "where job_id = cast(:job_id as uuid)"
            ),
            {"job_id": str(job_id)},
        )
        return int(result.rowcount or 0)

    def purge_expired(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> int:
        result = session.execute(
            text(
                "delete from ai_audit.essay_review_checkpoints "
                "where expires_at <= :now"
            ),
            {"now": now or datetime.now(UTC)},
        )
        return int(result.rowcount or 0)


def _associated_data(*, job_id: UUID, phase: str, call_id: UUID) -> bytes:
    return (
        f"{CHECKPOINT_CONTRACT_VERSION}:{job_id}:{phase}:{call_id}"
    ).encode("utf-8")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
