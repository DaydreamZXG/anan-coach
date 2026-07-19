from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.processor_disclosure.schemas import (
    ProcessorDisclosure,
    ProcessorDisclosureList,
)


class ProcessorDisclosureRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def published(self, *, generated_at: datetime) -> ProcessorDisclosureList:
        rows = self._session.execute(
            text(
                """
                select processor_release_record_id, provider_release, display_name,
                       operator_name, real_user_status, transport_security,
                       purposes, data_categories, data_region, model_training_use,
                       retention_policy, retention_seconds, deletion_support,
                       evidence, verified_at, valid_until
                from ai_audit.processor_release_records
                order by display_name, published_at desc
                """
            )
        ).mappings()
        return ProcessorDisclosureList(
            processors=[
                ProcessorDisclosure(
                    processorReleaseId=str(row["processor_release_record_id"]),
                    providerRelease=str(row["provider_release"]),
                    displayName=str(row["display_name"]),
                    operatorName=(
                        str(row["operator_name"])
                        if row["operator_name"] is not None
                        else None
                    ),
                    realUserStatus=str(row["real_user_status"]),
                    transportSecurity=str(row["transport_security"]),
                    purposes=list(row["purposes"]),
                    dataCategories=list(row["data_categories"]),
                    dataRegion=str(row["data_region"]),
                    modelTrainingUse=str(row["model_training_use"]),
                    retentionPolicy=str(row["retention_policy"]),
                    retentionSeconds=row["retention_seconds"],
                    deletionSupport=str(row["deletion_support"]),
                    evidence=dict(row["evidence"]),
                    verifiedAt=row["verified_at"],
                    validUntil=row["valid_until"],
                )
                for row in rows
            ],
            generatedAt=generated_at,
        )
