from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Callable, Protocol

from app.domains.external_ingestion.upload.models import (
    ObjectMetadata,
    ExpectedUploadPart,
    MultipartUploadHandle,
    ScanReceipt,
    ScanVerdict,
    ScannerRuntimeApproval,
    UploadPartAuthorization,
    UploadContractError,
    UploadedPart,
)


@dataclass(frozen=True)
class TosStorageSettings:
    bucket: str
    region: str
    endpoint: str
    quarantine_prefix: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.bucket, self.region, self.endpoint)):
            raise UploadContractError("TOS bucket, region and endpoint are required")
        if not self.endpoint.startswith("https://"):
            raise UploadContractError("TOS endpoint must use HTTPS")
        if self.endpoint.endswith("/"):
            raise UploadContractError("TOS endpoint must not end with a slash")
        if not self.quarantine_prefix.startswith("private/quarantine/"):
            raise UploadContractError("quarantine objects require an isolated prefix")
        if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket) is None:
            raise UploadContractError("TOS bucket name is invalid")
        if re.fullmatch(r"[a-z0-9-]{2,80}", self.region) is None:
            raise UploadContractError("TOS region is invalid")


@dataclass(frozen=True)
class TosPresignResult:
    url: str
    signed_headers: dict[str, str]
    expires_at: datetime
    binds_content_type: bool
    binds_content_length: bool
    binds_content_sha256: bool
    binds_server_side_encryption: bool


@dataclass(frozen=True)
class TosHeadResult:
    byte_size: int
    content_sha256: str
    mime_type: str
    private_acl: bool
    server_side_encrypted: bool


@dataclass(frozen=True)
class TosClientIdentity:
    region: str
    endpoint: str


class TosSdkFacade(Protocol):
    """Narrow facade implemented by the approved Volcengine TOS SDK wrapper."""

    def identity(self) -> TosClientIdentity: ...

    def presign_private_put(
        self,
        *,
        bucket: str,
        key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> TosPresignResult: ...

    def head(self, *, bucket: str, key: str) -> TosHeadResult: ...

    def initiate_multipart(
        self,
        *,
        bucket: str,
        key: str,
        mime_type: str,
        content_sha256: str,
        operation_key: str,
    ) -> str: ...

    def presign_multipart_part(
        self,
        *,
        bucket: str,
        key: str,
        provider_upload_id: str,
        part_number: int,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> TosPresignResult: ...

    def list_multipart_parts(
        self, *, bucket: str, key: str, provider_upload_id: str
    ) -> tuple[UploadedPart, ...]: ...

    def complete_multipart(
        self,
        *,
        bucket: str,
        key: str,
        provider_upload_id: str,
        parts: tuple[UploadedPart, ...],
        operation_key: str,
    ) -> None: ...

    def abort_multipart(
        self,
        *,
        bucket: str,
        key: str,
        provider_upload_id: str,
        operation_key: str,
    ) -> None: ...

    def delete(self, *, bucket: str, key: str, operation_key: str) -> None: ...

    def quarantine(
        self,
        *,
        bucket: str,
        source_key: str,
        destination_key: str,
        operation_key: str,
    ) -> None: ...


class TosPrivateObjectStorageAdapter:
    """Strict production adapter; it has no filesystem or public-ACL fallback."""

    def __init__(self, *, settings: TosStorageSettings, client: TosSdkFacade) -> None:
        self._settings = settings
        self._client = client
        identity = client.identity()
        if (
            identity.region != settings.region
            or identity.endpoint.rstrip("/") != settings.endpoint
        ):
            raise UploadContractError(
                "TOS client identity differs from the approved region or endpoint"
            )

    def authorize_single_put(
        self,
        *,
        object_key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> UploadPartAuthorization:
        self._validate_private_key(object_key)
        result = self._client.presign_private_put(
            bucket=self._settings.bucket,
            key=object_key,
            mime_type=mime_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            expires_at=expires_at,
        )
        if not result.url.startswith("https://"):
            raise UploadContractError("TOS presigned upload must use HTTPS")
        if not all(
            (
                result.binds_content_type,
                result.binds_content_length,
                result.binds_content_sha256,
                result.binds_server_side_encryption,
            )
        ):
            raise UploadContractError(
                "TOS upload authorization must bind type, size, hash and encryption"
            )
        if not result.signed_headers:
            raise UploadContractError("TOS upload authorization has no signed headers")
        self._require_signed_headers(
            result,
            {
                "content-type": mime_type,
                "content-length": str(byte_size),
                "x-tos-content-sha256": content_sha256,
                "x-tos-acl": "private",
                "x-tos-server-side-encryption": "AES256",
            },
        )
        return UploadPartAuthorization(
            object_ref=self._object_ref(object_key),
            part_number=1,
            byte_size=byte_size,
            content_sha256=content_sha256,
            url=result.url,
            required_headers=dict(result.signed_headers),
            expires_at=result.expires_at,
        )

    def initiate_multipart(
        self,
        *,
        object_key: str,
        mime_type: str,
        content_sha256: str,
        operation_key: str,
    ) -> MultipartUploadHandle:
        self._validate_private_key(object_key)
        provider_upload_id = self._client.initiate_multipart(
            bucket=self._settings.bucket,
            key=object_key,
            mime_type=mime_type,
            content_sha256=content_sha256,
            operation_key=_operation_key(operation_key),
        )
        return MultipartUploadHandle(
            object_ref=self._object_ref(object_key),
            provider_upload_id=provider_upload_id,
        )

    def authorize_multipart_part(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        part: ExpectedUploadPart,
        expires_at: datetime,
    ) -> UploadPartAuthorization:
        key = self._key(object_ref)
        result = self._client.presign_multipart_part(
            bucket=self._settings.bucket,
            key=key,
            provider_upload_id=provider_upload_id,
            part_number=part.part_number,
            byte_size=part.byte_size,
            content_sha256=part.content_sha256,
            expires_at=expires_at,
        )
        self._validate_presign(result)
        self._require_signed_headers(
            result,
            {
                "content-length": str(part.byte_size),
                "x-tos-content-sha256": part.content_sha256,
            },
        )
        return UploadPartAuthorization(
            object_ref=object_ref,
            part_number=part.part_number,
            byte_size=part.byte_size,
            content_sha256=part.content_sha256,
            url=result.url,
            required_headers=dict(result.signed_headers),
            expires_at=result.expires_at,
        )

    def uploaded_parts(
        self, *, object_ref: str, provider_upload_id: str
    ) -> tuple[UploadedPart, ...]:
        return self._client.list_multipart_parts(
            bucket=self._settings.bucket,
            key=self._key(object_ref),
            provider_upload_id=provider_upload_id,
        )

    def complete_multipart(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        parts: tuple[UploadedPart, ...],
        operation_key: str,
    ) -> None:
        self._client.complete_multipart(
            bucket=self._settings.bucket,
            key=self._key(object_ref),
            provider_upload_id=provider_upload_id,
            parts=parts,
            operation_key=_operation_key(operation_key),
        )

    def abort_multipart(
        self,
        *,
        object_ref: str,
        provider_upload_id: str,
        operation_key: str,
    ) -> None:
        self._client.abort_multipart(
            bucket=self._settings.bucket,
            key=self._key(object_ref),
            provider_upload_id=provider_upload_id,
            operation_key=_operation_key(operation_key),
        )

    def head_private(self, *, object_ref: str) -> ObjectMetadata:
        key = self._key(object_ref)
        result = self._client.head(bucket=self._settings.bucket, key=key)
        return ObjectMetadata(
            object_ref=object_ref,
            byte_size=result.byte_size,
            content_sha256=result.content_sha256,
            mime_type=result.mime_type,
            access="private" if result.private_acl else "public",
            server_side_encrypted=result.server_side_encrypted,
        )

    def delete_private(self, *, object_ref: str, operation_key: str) -> None:
        self._client.delete(
            bucket=self._settings.bucket,
            key=self._key(object_ref),
            operation_key=_operation_key(operation_key),
        )

    def quarantine_private(self, *, object_ref: str, operation_key: str) -> str:
        source_key = self._key(object_ref)
        destination_key = (
            f"{self._settings.quarantine_prefix.rstrip('/')}/"
            f"{_operation_key(operation_key)}/{source_key.rsplit('/', 1)[-1]}"
        )
        self._client.quarantine(
            bucket=self._settings.bucket,
            source_key=source_key,
            destination_key=destination_key,
            operation_key=operation_key,
        )
        return self._object_ref(destination_key)

    def _key(self, object_ref: str) -> str:
        prefix = f"tos://{self._settings.bucket}/"
        if not object_ref.startswith(prefix):
            raise UploadContractError("object reference belongs to another TOS bucket")
        key = object_ref[len(prefix) :]
        self._validate_private_key(key)
        return key

    @staticmethod
    def _validate_private_key(key: str) -> None:
        if (
            not key.startswith("private/")
            or key.startswith("/")
            or any(part in {"", ".", ".."} for part in key.split("/"))
            or len(key) > 1_024
        ):
            raise UploadContractError("TOS key is not a safe private object key")

    def _object_ref(self, key: str) -> str:
        return f"tos://{self._settings.bucket}/{key}"

    @staticmethod
    def _validate_presign(result: TosPresignResult) -> None:
        if not result.url.startswith("https://"):
            raise UploadContractError("TOS presigned upload must use HTTPS")
        if not all(
            (
                result.binds_content_type,
                result.binds_content_length,
                result.binds_content_sha256,
            )
        ):
            raise UploadContractError(
                "TOS multipart authorization must bind type, size and hash"
            )
        if not result.signed_headers:
            raise UploadContractError("TOS upload authorization has no signed headers")

    @staticmethod
    def _require_signed_headers(
        result: TosPresignResult, expected: dict[str, str]
    ) -> None:
        actual = {
            name.casefold(): value for name, value in result.signed_headers.items()
        }
        if any(actual.get(name) != value for name, value in expected.items()):
            raise UploadContractError(
                "TOS presign response omitted an immutable security header"
            )


@dataclass(frozen=True)
class ScannerApproval:
    provider_route: str
    scanner_policy_version: str
    data_processing_agreement_version: str
    mainland_processing_only: bool
    maximum_definition_age: timedelta = timedelta(hours=48)

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.provider_route,
                self.scanner_policy_version,
                self.data_processing_agreement_version,
            )
        ):
            raise UploadContractError("scanner approval evidence is incomplete")
        if not self.mainland_processing_only:
            raise UploadContractError("scanner route is outside the approved data boundary")
        if self.maximum_definition_age <= timedelta(0):
            raise UploadContractError("scanner definition-age policy must be positive")

    def runtime_approval(self) -> ScannerRuntimeApproval:
        return ScannerRuntimeApproval(
            provider_route=self.provider_route,
            scanner_policy_version=self.scanner_policy_version,
            data_processing_agreement_version=self.data_processing_agreement_version,
        )


@dataclass(frozen=True)
class ScannerProviderResult:
    scan_id: str
    verdict: str
    detected_mime_type: str
    content_sha256: str
    definition_version: str
    definitions_updated_at: datetime
    scanned_at: datetime
    detected_page_count: int | None = None


class ScannerClient(Protocol):
    def scan_private_object(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        operation_key: str,
        provider_route: str,
    ) -> ScannerProviderResult: ...


class ApprovedMalwareScannerAdapter:
    """Only an explicitly approved scanner route can produce a valid receipt."""

    def __init__(
        self,
        *,
        approval: ScannerApproval,
        client: ScannerClient,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._approval = approval
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    def scan(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        operation_key: str,
    ) -> ScanReceipt:
        result = self._client.scan_private_object(
            object_ref=object_ref,
            expected_sha256=expected_sha256,
            operation_key=_operation_key(operation_key),
            provider_route=self._approval.provider_route,
        )
        if result.scanned_at > self._clock() + timedelta(minutes=5):
            raise UploadContractError("scanner receipt time is in the future")
        if (
            result.scanned_at - result.definitions_updated_at
            > self._approval.maximum_definition_age
        ):
            raise UploadContractError("scanner definitions exceed the approved age")
        try:
            verdict = ScanVerdict(result.verdict)
        except ValueError as error:
            raise UploadContractError("scanner returned an unknown verdict") from error
        return ScanReceipt(
            scan_id=result.scan_id,
            verdict=verdict,
            detected_mime_type=result.detected_mime_type,
            content_sha256=result.content_sha256,
            scanner_policy_version=self._approval.scanner_policy_version,
            provider_route=self._approval.provider_route,
            data_processing_agreement_version=(
                self._approval.data_processing_agreement_version
            ),
            definition_version=result.definition_version,
            scanned_at=result.scanned_at,
            detected_page_count=result.detected_page_count,
        )


def _operation_key(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise UploadContractError("external operation key must be a SHA-256 value")
    return value
