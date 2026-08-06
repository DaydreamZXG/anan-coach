from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domains.external_ingestion.upload.adapters import (
    ApprovedMalwareScannerAdapter,
    ScannerApproval,
    ScannerProviderResult,
    TosPrivateObjectStorageAdapter,
    TosStorageSettings,
)
from app.domains.external_ingestion.upload.models import (
    UploadContractError,
)
from app.domains.external_ingestion.upload.repository import PostgresUploadRepository
from app.domains.external_ingestion.upload.reservations import (
    PostgresUploadReservationAdapter,
    UploadQuotaPolicy,
)
from app.domains.external_ingestion.upload.service import UploadService
from app.domains.external_ingestion.upload.tos_sdk import VolcengineTosSdkFacade
from app.internal_services.private_pdf import PrivatePdfPageCounter


class HttpMalwareScannerClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = _approved_https_url(
            settings.upload_scanner_base_url,
            allowlist=settings.upload_scanner_https_host_allowlist,
            name="UPLOAD_SCANNER_BASE_URL",
        )
        if not settings.upload_scanner_api_key:
            raise UploadContractError("UPLOAD_SCANNER_API_KEY is required")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", settings.upload_scanner_release_id) is None:
            raise UploadContractError("UPLOAD_SCANNER_RELEASE_ID is invalid")
        self._settings = settings
        self._transport = transport

    def scan_private_object(
        self,
        *,
        object_ref: str,
        expected_sha256: str,
        operation_key: str,
        provider_route: str,
    ) -> ScannerProviderResult:
        try:
            with httpx.Client(timeout=45.0, transport=self._transport) as client:
                response = client.post(
                    f"{self._base_url}/v1/private-object-scans",
                    headers={
                        "Authorization": f"Bearer {self._settings.upload_scanner_api_key}",
                        "Idempotency-Key": operation_key,
                        "X-Scanner-Release": self._settings.upload_scanner_release_id,
                    },
                    json={
                        "contractVersion": "private-object-scan@v1",
                        "objectRef": object_ref,
                        "expectedSha256": expected_sha256,
                        "providerRoute": provider_route,
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise UploadContractError("approved malware scanner is unavailable") from exc
        if response.status_code != 200:
            raise UploadContractError(
                f"approved malware scanner rejected the request ({response.status_code})"
            )
        try:
            payload: dict[str, Any] = response.json()
            if payload.get("contractVersion") != "private-object-scan-result@v1":
                raise ValueError("wrong scanner result contract")
            return ScannerProviderResult(
                scan_id=str(payload["scanId"]),
                verdict=str(payload["verdict"]),
                detected_mime_type=str(payload["detectedMimeType"]),
                definition_version=str(payload["definitionVersion"]),
                definitions_updated_at=_aware_datetime(payload["definitionsUpdatedAt"]),
                scanned_at=_aware_datetime(payload["scannedAt"]),
                content_sha256=str(payload["contentSha256"]),
                detected_page_count=(
                    int(payload["detectedPageCount"])
                    if payload.get("detectedPageCount") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UploadContractError("malware scanner returned an invalid contract") from exc


def build_upload_service(*, session: Session, settings: Settings) -> UploadService:
    storage = build_private_object_storage(settings)
    scanner = None
    scanner_runtime_approval = None
    if settings.upload_malware_scan_enabled:
        scanner_client = HttpMalwareScannerClient(settings)
        scanner_approval = ScannerApproval(
            provider_route=settings.upload_scanner_route,
            scanner_policy_version=settings.upload_scanner_policy_version,
            data_processing_agreement_version=settings.upload_scanner_dpa_version,
            mainland_processing_only=settings.upload_scanner_mainland_only,
            maximum_definition_age=settings.upload_scanner_maximum_definition_age,
        )
        scanner = ApprovedMalwareScannerAdapter(
            approval=scanner_approval,
            client=scanner_client,
        )
        scanner_runtime_approval = scanner_approval.runtime_approval()
    return UploadService(
        repository=PostgresUploadRepository(session=session),
        object_storage=storage,
        scanner=scanner,
        scanner_approval=scanner_runtime_approval,
        reservations=PostgresUploadReservationAdapter(
            session=session,
            policy=UploadQuotaPolicy(
                policy_version=settings.upload_quota_policy_version,
                maximum_pending_bytes_per_user=(
                    settings.upload_maximum_pending_bytes_per_user
                ),
                maximum_stored_bytes_per_user=(
                    settings.upload_maximum_stored_bytes_per_user
                ),
            ),
        ),
        pdf_page_counter=PrivatePdfPageCounter(settings),
    )


def build_private_object_storage(settings: Settings) -> TosPrivateObjectStorageAdapter:
    storage_settings = TosStorageSettings(
        bucket=settings.tos_bucket,
        region=settings.tos_region,
        endpoint=settings.tos_endpoint,
        quarantine_prefix=settings.tos_quarantine_prefix,
    )
    return TosPrivateObjectStorageAdapter(
        settings=storage_settings,
        client=VolcengineTosSdkFacade.from_credentials(
            access_key=settings.tos_access_key,
            secret_key=settings.tos_secret_key,
            endpoint=settings.tos_endpoint,
            presign_endpoint=settings.tos_presign_endpoint,
            region=settings.tos_region,
        ),
    )


def _approved_https_url(value: str, *, allowlist: str, name: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").casefold()
        _ = parsed.port
    except ValueError as exc:
        raise UploadContractError(f"{name} is invalid") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UploadContractError(f"{name} requires a credential-free HTTPS URL")
    allowed = {item.strip().casefold() for item in allowlist.split(",") if item.strip()}
    if hostname not in allowed:
        raise UploadContractError(f"{name} host is not approved")
    return value.rstrip("/")


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("scanner timestamps require timezone")
    return parsed.astimezone(UTC)
