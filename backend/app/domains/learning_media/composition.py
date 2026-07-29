from __future__ import annotations

from app.core.config import Settings
from app.domains.external_ingestion.composition import HttpMalwareScannerClient
from app.domains.external_ingestion.upload.adapters import (
    ApprovedMalwareScannerAdapter,
    ScannerApproval,
    TosPrivateObjectStorageAdapter,
    TosStorageSettings,
)
from app.domains.external_ingestion.upload.tos_sdk import VolcengineTosSdkFacade


def build_learning_media_storage(settings: Settings) -> TosPrivateObjectStorageAdapter:
    return TosPrivateObjectStorageAdapter(
        settings=TosStorageSettings(
            bucket=settings.tos_bucket,
            region=settings.tos_region,
            endpoint=settings.tos_endpoint,
            quarantine_prefix=settings.tos_quarantine_prefix,
        ),
        client=VolcengineTosSdkFacade.from_credentials(
            access_key=settings.tos_access_key,
            secret_key=settings.tos_secret_key,
            endpoint=settings.tos_endpoint,
            presign_endpoint=settings.tos_presign_endpoint,
            region=settings.tos_region,
        ),
    )


def build_learning_media_scanner(settings: Settings) -> ApprovedMalwareScannerAdapter:
    return ApprovedMalwareScannerAdapter(
        approval=ScannerApproval(
            provider_route=settings.upload_scanner_route,
            scanner_policy_version=settings.upload_scanner_policy_version,
            data_processing_agreement_version=settings.upload_scanner_dpa_version,
            mainland_processing_only=settings.upload_scanner_mainland_only,
            maximum_definition_age=settings.upload_scanner_maximum_definition_age,
        ),
        client=HttpMalwareScannerClient(settings),
    )
