from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import RecognitionKind
from app.domains.external_ingestion.matching.models import (
    AuthorizationDecision,
    IndexedContent,
    MatchingPolicy,
    MatchQuery,
)
from app.domains.external_ingestion.matching.repository import (
    SQLContentMatchRepository,
    SQLRecognitionAuthority,
)
from app.domains.external_ingestion.matching.service import MatchingService
from app.domains.external_ingestion.recognition.models import (
    BoundingBox,
    ProcessorApproval,
    ProcessorResult,
    TextSpan,
)
from app.domains.external_ingestion.recognition.processor import ProcessorRegistry
from app.domains.external_ingestion.recognition.repository import SQLRecognitionRepository
from app.domains.external_ingestion.recognition.service import RecognitionService


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


class SQLRoundTripProcessor:
    provider_release = "release-2026-07-01"

    def process(self, request):
        return ProcessorResult(
            providerRequestId="sql-round-trip-provider-request",
            textSpans=(
                TextSpan(
                    spanId="span-1",
                    text="下列关于行政许可的说法正确的是",
                    box=BoundingBox(pageNo=1, x=0.1, y=0.1, width=0.8, height=0.1),
                    confidence=0.99,
                ),
            ),
            visualFingerprints=("3" * 64,),
            confidence=0.99,
        )


class SQLRoundTripCatalog:
    def __init__(self, item):
        self.item = item

    def search(self, *, query, maximum_candidates):
        return (self.item,)

    def get(self, *, content_version_id):
        return self.item if content_version_id == self.item.content_version_id else None


class SQLRoundTripAuthorization:
    def authorize(self, *, owner_user_id, content, purpose):
        return AuthorizationDecision(
            allowed=True,
            reason="explicit_grant",
            snapshotRef="license-snapshot:sql-round-trip",
        )

    def snapshot_current(self, *, snapshot_ref):
        return True


def test_sql_repositories_preserve_immutable_recognition_and_match_lineage(
    db_session: Session,
) -> None:
    owner = uuid4()
    artifact = uuid4()
    db_session.execute(
        text("insert into learner.users (user_id, display_name) values (:id, '识别测试')"),
        {"id": str(owner)},
    )
    db_session.execute(
        text(
            """
            insert into learning.source_artifact_versions (
              artifact_version_id, artifact_id, owner_user_id, version_no,
              source_kind, state, object_ref, mime_type, content_sha256,
              byte_size, page_count, supersedes_artifact_version_id,
              contract_payload, created_at
            ) values (
              :artifact_version_id, :artifact_id, :owner_user_id, 1,
              'camera_image', 'available', :object_ref, 'image/jpeg', :sha,
              1024, null, null, '{}'::jsonb, :created_at
            )
            """
        ),
        {
            "artifact_version_id": str(artifact),
            "artifact_id": str(uuid4()),
            "owner_user_id": str(owner),
            "object_ref": f"tos://private/{owner}/{artifact}",
            "sha": "1" * 64,
            "created_at": NOW,
        },
    )
    recognition_repository = SQLRecognitionRepository(db_session)
    approval = ProcessorApproval(
        policyVersion="ocr-private@2026-07",
        route="ocr-primary",
        provider="vendor",
        providerRelease=SQLRoundTripProcessor.provider_release,
        modelRelease="sql-ocr-model@v1",
        promptVersion="recognition-processor-policy@v1",
        priceVersion="ocr-price@v1",
        billingUnit="image",
        unitPriceCny="0.01",
        approvedKinds=(RecognitionKind.PRINTED_OCR,),
        approvedMimeTypes=("image/jpeg",),
        dataRegion="mainland_china",
        dpaEvidenceRef="dpa:sql-test",
        securityReviewRef="security:sql-test",
        approvalReceiptRef="approval:sql-test",
        validFrom=NOW - timedelta(days=1),
        validUntil=NOW + timedelta(days=1),
    )
    recognition_service = RecognitionService(
        repository=recognition_repository,
        processors=ProcessorRegistry(
            (approval,), {"ocr-primary": SQLRoundTripProcessor()}
        ),
        clock=lambda: NOW,
    )
    candidate = recognition_service.recognize(
        owner_user_id=owner,
        artifact_version_id=artifact,
        recognition_kind=RecognitionKind.PRINTED_OCR,
        processor_route="ocr-primary",
        idempotency_key="sql-recognize",
    )
    confirmed = recognition_service.confirm(
        owner_user_id=owner,
        recognition_revision_id=candidate.recognition_revision_id,
        expected_revision_no=1,
        idempotency_key="sql-confirm-recognition",
    )

    assert confirmed.supersedes_recognition_revision_id == candidate.recognition_revision_id
    assert recognition_repository.revision(
        recognition_revision_id=confirmed.recognition_revision_id
    ) == confirmed
    cost = db_session.execute(
        text(
            "select scenario_key, operation, model, billing_unit, unit_count, "
            "total_cost_cny, status from ai_audit.external_ai_operation_ledger "
            "where operation_key = :operation_key"
        ),
        {"operation_key": f"recognition:{candidate.recognition_revision_id}"},
    ).mappings().one()
    assert cost["scenario_key"] == "recognition_printed_ocr"
    assert cost["operation"] == "recognition_process"
    assert cost["model"] == "sql-ocr-model@v1"
    assert cost["billing_unit"] == "image"
    assert cost["unit_count"] == 1
    assert cost["total_cost_cny"] == Decimal("0.01")
    assert cost["status"] == "succeeded"

    indexed = IndexedContent(
        contentVersionId="question-content-version-sql-1",
        track="xingce",
        promptText="下列关于行政许可的说法正确的是",
        distributionClass="licensed_transferable",
        crossUserMatchAllowed=True,
        referenceAnswer="答案 A",
        visualFingerprints=("3" * 64,),
    )
    matching_repository = SQLContentMatchRepository(db_session)
    matching = MatchingService(
        repository=matching_repository,
        catalog=SQLRoundTripCatalog(indexed),
        authorization=SQLRoundTripAuthorization(),
        recognition_authority=SQLRecognitionAuthority(db_session),
        policy=MatchingPolicy(
            policyVersion="mixed-match@2026-07",
            similarThreshold=0.75,
            ambiguityMargin=0.05,
            textWeight=0.8,
            visualWeight=0.2,
            essayMinPromptChars=20,
            essayRequireMaterial=True,
            maximumCandidates=5,
        ),
        clock=lambda: NOW,
    )
    match_candidate, evaluation = matching.evaluate(
        owner_user_id=owner,
        question_unit_id="a" * 64,
        recognition=confirmed,
        query=MatchQuery(
            track="xingce",
            promptText=indexed.prompt_text,
            visualFingerprints=indexed.visual_fingerprints,
        ),
        idempotency_key="sql-match",
    )
    match_confirmed = matching.confirm(
        owner_user_id=owner,
        content_match_version_id=match_candidate.content_match_version_id,
        expected_version_no=1,
        content_version_id=indexed.content_version_id,
        idempotency_key="sql-confirm-match",
    )
    db_session.commit()

    assert evaluation.ambiguity_reason == "single_exact"
    assert match_confirmed.supersedes_content_match_version_id == (
        match_candidate.content_match_version_id
    )
    assert matching_repository.version(
        content_match_version_id=match_confirmed.content_match_version_id
    ) == match_confirmed
    assert db_session.execute(
        text(
            "select count(*) from learning.recognition_revisions "
            "where owner_user_id = :owner"
        ),
        {"owner": str(owner)},
    ).scalar_one() == 2
    assert db_session.execute(
        text(
            "select count(*) from learning.content_match_versions "
            "where owner_user_id = :owner"
        ),
        {"owner": str(owner)},
    ).scalar_one() == 2
