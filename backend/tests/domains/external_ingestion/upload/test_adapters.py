from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domains.external_ingestion.upload.adapters import (
    ApprovedMalwareScannerAdapter,
    ScannerApproval,
    ScannerProviderResult,
    TosClientIdentity,
    TosHeadResult,
    TosPresignResult,
    TosPrivateObjectStorageAdapter,
    TosStorageSettings,
)
from app.domains.external_ingestion.upload.models import UploadContractError


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
KEY = "private/external-learning/abc/2026/07/session/file.jpg"
REF = f"tos://bucket/{KEY}"


class TosFacade:
    def __init__(self) -> None:
        self.presign = TosPresignResult(
            url=f"https://tos.example.invalid/{KEY}?signature=opaque",
            signed_headers={
                "content-type": "image/jpeg",
                "content-length": "100",
                "x-tos-content-sha256": "a" * 64,
                "x-tos-acl": "private",
                "x-tos-server-side-encryption": "AES256",
            },
            expires_at=NOW + timedelta(minutes=10),
            binds_content_type=True,
            binds_content_length=True,
            binds_content_sha256=True,
            binds_server_side_encryption=True,
        )
        self.head_result = TosHeadResult(
            byte_size=100,
            content_sha256="a" * 64,
            mime_type="image/jpeg",
            private_acl=True,
            server_side_encrypted=True,
        )
        self.deleted: list[str] = []

    def identity(self) -> TosClientIdentity:
        return TosClientIdentity(
            region="cn-guangzhou",
            endpoint="https://tos-cn-guangzhou.volces.com",
        )

    def presign_private_put(self, **kwargs) -> TosPresignResult:
        return self.presign

    def head(self, **kwargs) -> TosHeadResult:
        return self.head_result

    def delete(self, *, bucket: str, key: str, operation_key: str) -> None:
        self.deleted.append(key)

    def quarantine(self, **kwargs) -> None:
        return None


def storage(client: TosFacade) -> TosPrivateObjectStorageAdapter:
    return TosPrivateObjectStorageAdapter(
        settings=TosStorageSettings(
            bucket="bucket",
            region="cn-guangzhou",
            endpoint="https://tos-cn-guangzhou.volces.com",
            quarantine_prefix="private/quarantine/external-learning",
        ),
        client=client,
    )


def test_tos_adapter_requires_all_security_properties_to_be_signed() -> None:
    client = TosFacade()
    client.presign = replace(client.presign, binds_content_sha256=False)

    with pytest.raises(UploadContractError):
        storage(client).authorize_single_put(
            object_key=KEY,
            mime_type="image/jpeg",
            byte_size=100,
            content_sha256="a" * 64,
            expires_at=NOW + timedelta(minutes=10),
        )


def test_tos_adapter_reports_public_or_unencrypted_metadata_for_service_rejection() -> None:
    client = TosFacade()
    client.head_result = replace(client.head_result, private_acl=False)
    assert storage(client).head_private(object_ref=REF).access == "public"

    client.head_result = replace(
        client.head_result, private_acl=True, server_side_encrypted=False
    )
    assert storage(client).head_private(object_ref=REF).server_side_encrypted is False


class ScannerClient:
    def __init__(self, result: ScannerProviderResult) -> None:
        self.result = result

    def scan_private_object(self, **kwargs) -> ScannerProviderResult:
        return self.result


def approval() -> ScannerApproval:
    return ScannerApproval(
        provider_route="approved-mainland-malware-scanner",
        scanner_policy_version="malware-policy@v3",
        data_processing_agreement_version="dpa@2026-06",
        mainland_processing_only=True,
    )


def scanner_result() -> ScannerProviderResult:
    return ScannerProviderResult(
        scan_id="scan-1",
        verdict="clean",
        detected_mime_type="image/jpeg",
        content_sha256="a" * 64,
        definition_version="defs-1",
        definitions_updated_at=NOW - timedelta(hours=2),
        scanned_at=NOW,
        detected_page_count=None,
    )


def test_scanner_adapter_rejects_stale_definitions_and_unknown_verdicts() -> None:
    stale = replace(
        scanner_result(), definitions_updated_at=NOW - timedelta(hours=49)
    )
    with pytest.raises(UploadContractError):
        ApprovedMalwareScannerAdapter(
            approval=approval(), client=ScannerClient(stale), clock=lambda: NOW
        ).scan(object_ref=REF, expected_sha256="a" * 64, operation_key="b" * 64)

    unknown = replace(scanner_result(), verdict="probably-clean")
    with pytest.raises(UploadContractError):
        ApprovedMalwareScannerAdapter(
            approval=approval(), client=ScannerClient(unknown), clock=lambda: NOW
        ).scan(object_ref=REF, expected_sha256="a" * 64, operation_key="b" * 64)


def test_scanner_approval_is_fail_closed_for_unapproved_data_boundary() -> None:
    with pytest.raises(UploadContractError):
        ScannerApproval(
            provider_route="scanner",
            scanner_policy_version="policy",
            data_processing_agreement_version="dpa",
            mainland_processing_only=False,
        )
