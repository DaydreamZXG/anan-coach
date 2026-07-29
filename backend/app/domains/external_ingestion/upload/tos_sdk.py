from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from typing import Any, Callable

from app.domains.external_ingestion.upload.adapters import (
    TosClientIdentity,
    TosHeadResult,
    TosPresignResult,
)
from app.domains.external_ingestion.upload.models import (
    UploadContractError,
    UploadedPart,
)


class VolcengineTosSdkUnavailable(RuntimeError):
    pass


class VolcengineTosSdkFacade:
    """Audited facade for the official ``tos`` 2.x ``TosClientV2`` SDK.

    The facade signs every immutable upload property, enforces private ACL and
    SSE-TOS (AES256), and makes multipart init/complete/abort replay-safe using
    deterministic object keys plus provider state inspection.
    """

    def __init__(
        self,
        *,
        client: Any,
        presign_client: Any | None = None,
        tos_module: Any,
        region: str,
        endpoint: str,
        presign_endpoint: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        resolved_presign_endpoint = presign_endpoint or endpoint
        if (
            not region.strip()
            or not endpoint.startswith("https://")
            or not resolved_presign_endpoint.startswith("https://")
        ):
            raise UploadContractError("official TOS client identity is invalid")
        self._client = client
        self._presign_client = presign_client or client
        self._tos = tos_module
        self._identity = TosClientIdentity(
            region=region, endpoint=endpoint.rstrip("/")
        )
        self._presign_endpoint = resolved_presign_endpoint.rstrip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_credentials(
        cls,
        *,
        access_key: str,
        secret_key: str,
        endpoint: str,
        presign_endpoint: str | None = None,
        region: str,
        security_token: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> VolcengineTosSdkFacade:
        if not access_key.strip() or not secret_key.strip():
            raise UploadContractError("TOS access and secret keys are required")
        try:
            import tos  # type: ignore[import-not-found]
        except ImportError as error:
            raise VolcengineTosSdkUnavailable(
                "official package tos>=2.9,<3 is required"
            ) from error
        client = tos.TosClientV2(
            access_key,
            secret_key,
            endpoint,
            region,
            security_token=security_token,
        )
        resolved_presign_endpoint = presign_endpoint or endpoint
        presign_client = (
            client
            if resolved_presign_endpoint.rstrip("/") == endpoint.rstrip("/")
            else tos.TosClientV2(
                access_key,
                secret_key,
                resolved_presign_endpoint,
                region,
                security_token=security_token,
            )
        )
        return cls(
            client=client,
            presign_client=presign_client,
            tos_module=tos,
            region=region,
            endpoint=endpoint,
            presign_endpoint=resolved_presign_endpoint,
            clock=clock,
        )

    def identity(self) -> TosClientIdentity:
        return self._identity

    def presign_private_put(
        self,
        *,
        bucket: str,
        key: str,
        mime_type: str,
        byte_size: int,
        content_sha256: str,
        expires_at: datetime,
    ) -> TosPresignResult:
        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(byte_size),
            "x-tos-content-sha256": content_sha256,
            "x-tos-meta-content-sha256": content_sha256,
            "x-tos-acl": "private",
            "x-tos-server-side-encryption": "AES256",
        }
        return self._presign(
            bucket=bucket,
            key=key,
            query=None,
            headers=headers,
            expires_at=expires_at,
            binds_content_type=True,
            binds_server_side_encryption=True,
        )

    def initiate_multipart(
        self,
        *,
        bucket: str,
        key: str,
        mime_type: str,
        content_sha256: str,
        operation_key: str,
    ) -> str:
        existing = self._active_upload_ids(bucket=bucket, key=key)
        if len(existing) == 1:
            return existing[0]
        if len(existing) > 1:
            raise UploadContractError(
                "multiple active TOS multipart uploads share one deterministic key"
            )
        try:
            result = self._client.create_multipart_upload(
                bucket,
                key,
                content_type=mime_type,
                acl=self._tos.ACLType.ACL_Private,
                server_side_encryption="AES256",
                meta={
                    "content-sha256": content_sha256,
                    "multipart-operation-key": operation_key,
                },
                forbid_overwrite=True,
            )
            if not getattr(result, "upload_id", None):
                raise UploadContractError("TOS did not return a multipart upload ID")
            return str(result.upload_id)
        except Exception:
            recovered = self._active_upload_ids(bucket=bucket, key=key)
            if len(recovered) == 1:
                return recovered[0]
            raise

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
    ) -> TosPresignResult:
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(byte_size),
            "x-tos-content-sha256": content_sha256,
            "x-tos-server-side-encryption": "AES256",
        }
        return self._presign(
            bucket=bucket,
            key=key,
            query={"uploadId": provider_upload_id, "partNumber": str(part_number)},
            headers=headers,
            expires_at=expires_at,
            binds_content_type=True,
            binds_server_side_encryption=True,
        )

    def list_multipart_parts(
        self, *, bucket: str, key: str, provider_upload_id: str
    ) -> tuple[UploadedPart, ...]:
        result = self._client.list_parts(
            bucket, key, provider_upload_id, max_parts=1_000
        )
        if bool(getattr(result, "is_truncated", False)):
            raise UploadContractError("TOS multipart manifest exceeds 1000 parts")
        parts = tuple(
            UploadedPart(
                part_number=int(item.part_number),
                byte_size=int(item.size),
                etag=str(item.etag),
            )
            for item in result.parts
        )
        if tuple(item.part_number for item in parts) != tuple(
            sorted(item.part_number for item in parts)
        ):
            raise UploadContractError("TOS multipart manifest is not ordered")
        return parts

    def complete_multipart(
        self,
        *,
        bucket: str,
        key: str,
        provider_upload_id: str,
        parts: tuple[UploadedPart, ...],
        operation_key: str,
    ) -> None:
        if self._completed_for_operation(
            bucket=bucket, key=key, operation_key=operation_key
        ):
            return
        sdk_parts = [
            self._tos.models2.CompletePart(item.etag, item.part_number)
            for item in parts
        ]
        try:
            self._client.complete_multipart_upload(
                bucket,
                key,
                provider_upload_id,
                parts=sdk_parts,
                forbid_overwrite=True,
            )
        except Exception:
            if self._completed_for_operation(
                bucket=bucket, key=key, operation_key=operation_key
            ):
                return
            raise
        if not self._completed_for_operation(
            bucket=bucket, key=key, operation_key=operation_key
        ):
            raise UploadContractError(
                "TOS multipart completion lacks its operation receipt"
            )

    def abort_multipart(
        self,
        *,
        bucket: str,
        key: str,
        provider_upload_id: str,
        operation_key: str,
    ) -> None:
        try:
            self._client.abort_multipart_upload(bucket, key, provider_upload_id)
        except Exception as error:
            if self._is_not_found(error) and provider_upload_id not in (
                self._active_upload_ids(bucket=bucket, key=key)
            ):
                return
            raise

    def head(self, *, bucket: str, key: str) -> TosHeadResult:
        result = self._client.head_object(bucket, key)
        acl = self._client.get_object_acl(bucket, key)
        public_groups = {"AllUsers", "AuthenticatedUsers"}
        private_acl = all(
            _enum_value(getattr(grant.grantee, "canned", None))
            not in public_groups
            for grant in acl.grants
        )
        headers = _lower_headers(getattr(result, "header", {}))
        sha_value = str(getattr(result, "meta", {}).get("content-sha256", ""))
        encrypted = (
            headers.get("x-tos-server-side-encryption") == "AES256"
            or _enum_value(getattr(result, "sse_algorithm", None)) == "AES256"
        )
        return TosHeadResult(
            byte_size=int(result.content_length),
            content_sha256=sha_value,
            mime_type=str(result.content_type),
            private_acl=private_acl,
            server_side_encrypted=encrypted,
        )

    def delete(self, *, bucket: str, key: str, operation_key: str) -> None:
        self._client.delete_object(bucket, key)

    def quarantine(
        self,
        *,
        bucket: str,
        source_key: str,
        destination_key: str,
        operation_key: str,
    ) -> None:
        destination = self._raw_head_or_none(bucket=bucket, key=destination_key)
        if destination is not None:
            if destination.meta.get("quarantine-operation-key") != operation_key:
                raise UploadContractError(
                    "quarantine destination belongs to another operation"
                )
            self.delete(bucket=bucket, key=source_key, operation_key=operation_key)
            return
        source = self._raw_head_or_none(bucket=bucket, key=source_key)
        if source is None:
            raise UploadContractError(
                "neither quarantine source nor attributable destination exists"
            )
        metadata = dict(source.meta)
        metadata["quarantine-operation-key"] = operation_key
        self._client.copy_object(
            bucket,
            destination_key,
            bucket,
            source_key,
            content_type=source.content_type,
            acl=self._tos.ACLType.ACL_Private,
            server_side_encryption="AES256",
            metadata_directive=(
                self._tos.MetadataDirectiveType.Metadata_Directive_Replace
            ),
            meta=metadata,
            forbid_overwrite=True,
        )
        destination = self._raw_head_or_none(bucket=bucket, key=destination_key)
        if (
            destination is None
            or destination.meta.get("quarantine-operation-key") != operation_key
        ):
            raise UploadContractError("TOS quarantine copy is not attributable")
        self.delete(bucket=bucket, key=source_key, operation_key=operation_key)

    def _presign(
        self,
        *,
        bucket: str,
        key: str,
        query: dict[str, str] | None,
        headers: dict[str, str],
        expires_at: datetime,
        binds_content_type: bool,
        binds_server_side_encryption: bool,
    ) -> TosPresignResult:
        ttl = ceil((expires_at - self._clock()).total_seconds())
        if not 60 <= ttl <= 86_400:
            raise UploadContractError("TOS presign TTL must be between 60s and 24h")
        output = self._presign_client.pre_signed_url(
            http_method=self._tos.HttpMethodType.Http_Method_Put,
            bucket=bucket,
            key=key,
            expires=ttl,
            header=headers,
            query=query,
            is_signed_all_headers=True,
        )
        signed_headers = {
            str(name).lower(): str(value)
            for name, value in output.signed_header.items()
            if str(name).lower() != "host"
        }
        lower = {name.lower(): value for name, value in headers.items()}
        return TosPresignResult(
            url=str(output.signed_url),
            signed_headers=signed_headers,
            expires_at=expires_at,
            binds_content_type=(
                binds_content_type and "content-type" in lower
            ),
            binds_content_length="content-length" in lower,
            binds_content_sha256=(
                lower.get("x-tos-content-sha256") not in {None, "UNSIGNED-PAYLOAD"}
            ),
            binds_server_side_encryption=(
                binds_server_side_encryption
                and lower.get("x-tos-server-side-encryption") == "AES256"
            ),
        )

    def _active_upload_ids(self, *, bucket: str, key: str) -> tuple[str, ...]:
        result = self._client.list_multipart_uploads(
            bucket, prefix=key, max_uploads=1_000
        )
        if bool(getattr(result, "is_truncated", False)):
            raise UploadContractError(
                "cannot establish a unique multipart operation from truncated state"
            )
        return tuple(
            str(item.upload_id) for item in result.uploads if item.key == key
        )

    def _completed_for_operation(
        self, *, bucket: str, key: str, operation_key: str
    ) -> bool:
        result = self._raw_head_or_none(bucket=bucket, key=key)
        return (
            result is not None
            and result.meta.get("multipart-operation-key") == operation_key
        )

    def _raw_head_or_none(self, *, bucket: str, key: str) -> Any | None:
        try:
            return self._client.head_object(bucket, key)
        except Exception as error:
            if self._is_not_found(error):
                return None
            raise

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        return getattr(error, "status_code", None) == 404 or getattr(
            error, "code", None
        ) in {"NoSuchKey", "NoSuchUpload", "NotFound"}


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _lower_headers(headers: Any) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in headers.items()}
