from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.external_learning.models import (
    AccountPrivateRights,
    Audience,
    ContentComponent,
    ContentLicenseRecord,
    ContentTransform,
    ContentUse,
    DeliveryChannel,
    LicenseGrantCell,
    LicenseGrantMatrix,
    LicenseState,
    LicenseTerm,
    ReviewerReceipt,
    Territory,
    TransferableRights,
    canonical_grant_matrix_hash,
)
from app.domains.content_catalog.licensing.service import ContentLicenseService
from app.domains.external_ingestion.matching.authorization import SQLMatchAuthorization
from app.domains.external_ingestion.matching.models import IndexedContent


NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)
CONTENT_VERSION = "7" * 64
OWNER = UUID("00000000-0000-0000-0000-000000000101")
OTHER = UUID("00000000-0000-0000-0000-000000000202")


def _cell(
    component: ContentComponent,
    *,
    use: ContentUse,
    channel: DeliveryChannel,
    audience: Audience,
    transform: ContentTransform,
) -> LicenseGrantCell:
    return LicenseGrantCell(
        component=component,
        use=use,
        channel=channel,
        audience=audience,
        transform=transform,
        territory=Territory.MAINLAND_CHINA,
    )


def _matrix(cells: tuple[LicenseGrantCell, ...]) -> LicenseGrantMatrix:
    payload = {
        "matrixVersionRef": "matching-license-matrix@v1",
        "matrixHash": "0" * 64,
        "componentsCoverage": [item.value for item in ContentComponent],
        "usesCoverage": [item.value for item in ContentUse],
        "channelsCoverage": [item.value for item in DeliveryChannel],
        "audiencesCoverage": [item.value for item in Audience],
        "transformsCoverage": [item.value for item in ContentTransform],
        "territoriesCoverage": [item.value for item in Territory],
        "defaultDecision": "deny",
        "modelTrainingDecision": "deny",
        "allowedGrantCells": [
            cell.model_dump(by_alias=True, mode="json") for cell in cells
        ],
    }
    payload["matrixHash"] = canonical_grant_matrix_hash(payload)
    return LicenseGrantMatrix.model_validate(payload)


def _matching_components() -> tuple[ContentComponent, ...]:
    return (
        ContentComponent.QUESTION_PROMPT,
        ContentComponent.QUESTION_OPTIONS,
        ContentComponent.MATERIAL_TEXT,
        ContentComponent.MATERIAL_IMAGE,
    )


def _ai_components() -> tuple[ContentComponent, ...]:
    return (
        *_matching_components(),
        ContentComponent.REFERENCE_ANSWER,
        ContentComponent.SOLUTION_EXPLANATION,
    )


def _transferable_cells() -> tuple[LicenseGrantCell, ...]:
    cross = tuple(
        _cell(
            component,
            use=ContentUse.CROSS_USER_MATCH,
            channel=DeliveryChannel.INTERNAL_RETRIEVAL,
            audience=Audience.AUTHENTICATED_USERS,
            transform=ContentTransform.EMBEDDING,
        )
        for component in _matching_components()
    )
    reference = (
        _cell(
            ContentComponent.REFERENCE_ANSWER,
            use=ContentUse.IN_APP_PRACTICE,
            channel=DeliveryChannel.WECHAT_MINIPROGRAM,
            audience=Audience.ACCOUNT_OWNER,
            transform=ContentTransform.VERBATIM,
        ),
    )
    ai = tuple(
        _cell(
            component,
            use=ContentUse.AI_GROUNDING,
            channel=DeliveryChannel.AI_PROCESSOR,
            audience=Audience.ACCOUNT_OWNER,
            transform=ContentTransform.VERBATIM,
        )
        for component in _ai_components()
    )
    return (*cross, *reference, *ai)


def _private_cells() -> tuple[LicenseGrantCell, ...]:
    matching = tuple(
        _cell(
            component,
            use=ContentUse.IN_APP_PRACTICE,
            channel=DeliveryChannel.INTERNAL_RETRIEVAL,
            audience=Audience.ACCOUNT_OWNER,
            transform=ContentTransform.EMBEDDING,
        )
        for component in _matching_components()
    )
    return (*matching, *_transferable_cells()[4:])


def _activate_license(
    session: Session,
    *,
    private_owner: UUID | None = None,
    cells: tuple[LicenseGrantCell, ...],
) -> ContentLicenseRecord:
    license_id = uuid4()
    term = LicenseTerm(
        termKind="fixed_term",
        effectiveAt=NOW - timedelta(days=1),
        expiresAt=NOW + timedelta(days=30),
        renewalPolicyVersionRef="matching-license-renewal@v1",
    )
    if private_owner is None:
        rights = TransferableRights(
            sourceId="catalog-question:q-auth-1",
            term=term,
            grantMatrix=_matrix(cells),
            distributionClass="licensed_transferable",
            rightsBasis="verified_agreement",
            agreementRef="agreement:catalog-2026",
            agreementHash="1" * 64,
        )
    else:
        rights = AccountPrivateRights(
            sourceId="private-upload:artifact-auth-1",
            term=term,
            grantMatrix=_matrix(cells),
            distributionClass="account_private",
            rightsBasis="user_private_purpose_limited",
            subjectScopeRef=f"account:{private_owner}",
            userAuthorizationReceiptRef="receipt:private-upload-auth-1",
            userAuthorizationReceiptHash="2" * 64,
        )
    draft = ContentLicenseRecord(
        contentLicenseId=license_id,
        contentVersionId=CONTENT_VERSION,
        revision=1,
        authorizationEpoch=0,
        state=LicenseState.DRAFT,
        createdByActorId="license-author",
        closedCoverageVersionRef="content-license-coverage@v1",
        closedCoverageHash="3" * 64,
        rights=rights,
        evidenceManifestRef="evidence:matching-license",
        evidenceManifestHash="4" * 64,
        recordCanonicalHash="0" * 64,
        createdAt=NOW,
        updatedAt=NOW,
    )
    service = ContentLicenseService(session=session, clock=lambda: NOW)
    saved = service.save_draft(
        actor_id="license-author",
        idempotency_key=f"save-{license_id}",
        expected_revision=0,
        record=draft,
    )
    review = service.submit_review(
        actor_id="license-author",
        idempotency_key=f"review-{license_id}",
        content_license_id=license_id,
        expected_revision=saved.revision,
    )
    return service.activate(
        actor_id="license-reviewer",
        idempotency_key=f"activate-{license_id}",
        content_license_id=license_id,
        expected_revision=review.revision,
        reviewer_receipt=ReviewerReceipt(
            reviewerId="license-reviewer",
            decision="approved",
            reviewedRecordHash=review.record_canonical_hash,
            evidenceManifestHash=review.evidence_manifest_hash,
            grantMatrixHash=review.rights.grant_matrix.matrix_hash,
            reviewedAt=NOW,
        ),
    )


def _content(*, private_owner: UUID | None = None) -> IndexedContent:
    return IndexedContent(
        contentVersionId=CONTENT_VERSION,
        track="xingce",
        promptText="题干",
        optionTexts=("选项 A",),
        materialTexts=("材料",),
        visualFingerprints=("5" * 64,),
        referenceAnswer="答案 A",
        solutionExplanation="解析",
        distributionClass=(
            "account_private" if private_owner is not None else "licensed_transferable"
        ),
        ownerUserId=private_owner,
        crossUserMatchAllowed=private_owner is None,
    )


def test_transferable_license_separately_authorizes_cross_match_reference_and_ai(
    db_session: Session,
) -> None:
    active = _activate_license(db_session, cells=_transferable_cells())
    authorization = SQLMatchAuthorization(db_session, clock=lambda: NOW)
    content = _content()

    cross = authorization.authorize(
        owner_user_id=OWNER, content=content, purpose="cross_user_match"
    )
    reference = authorization.authorize(
        owner_user_id=OWNER, content=content, purpose="reference_answer"
    )
    ai = authorization.authorize(
        owner_user_id=OWNER, content=content, purpose="ai_grounding"
    )

    assert cross.allowed and authorization.snapshot_current(
        snapshot_ref=cross.snapshot_ref or ""
    )
    assert reference.allowed and authorization.snapshot_current(
        snapshot_ref=reference.snapshot_ref or ""
    )
    assert ai.allowed and authorization.snapshot_current(
        snapshot_ref=ai.snapshot_ref or ""
    )
    assert (cross.snapshot_ref or "").count(",") == 3
    assert (reference.snapshot_ref or "").count(",") == 0
    assert (ai.snapshot_ref or "").count(",") == 5
    authorized_tuples = {
        (
            str(row["use"]),
            str(row["channel"]),
            str(row["audience"]),
            str(row["transform"]),
        )
        for row in db_session.execute(
            text(
                """
                select request_payload->>'use' as use,
                       request_payload->>'channel' as channel,
                       request_payload->>'audience' as audience,
                       request_payload->>'transform' as transform
                from product.content_license_authorization_snapshots
                where allowed is true
                """
            )
        ).mappings()
    }
    assert authorized_tuples == {
        (
            "cross_user_match",
            "internal_retrieval",
            "authenticated_users",
            "embedding",
        ),
        (
            "in_app_practice",
            "wechat_miniprogram",
            "account_owner",
            "verbatim",
        ),
        ("ai_grounding", "ai_processor", "account_owner", "verbatim"),
    }

    ContentLicenseService(session=db_session, clock=lambda: NOW).suspend(
        actor_id="license-operator",
        idempotency_key=f"suspend-{active.content_license_id}",
        content_license_id=active.content_license_id,
        expected_revision=active.revision,
        reason_code="rights_hold",
    )
    assert authorization.snapshot_current(snapshot_ref=cross.snapshot_ref or "") is False


def test_private_license_is_strictly_bound_to_account_and_never_cross_matched(
    db_session: Session,
) -> None:
    _activate_license(
        db_session, private_owner=OWNER, cells=_private_cells()
    )
    authorization = SQLMatchAuthorization(db_session, clock=lambda: NOW)
    content = _content(private_owner=OWNER)

    own = authorization.authorize(
        owner_user_id=OWNER, content=content, purpose="owner_private_match"
    )
    wrong_owner = authorization.authorize(
        owner_user_id=OTHER, content=content, purpose="owner_private_match"
    )
    cross = authorization.authorize(
        owner_user_id=OWNER, content=content, purpose="cross_user_match"
    )

    assert own.allowed is True
    assert wrong_owner.allowed is False
    assert wrong_owner.reason == "no_applicable_active_license"
    assert cross.allowed is False


def test_missing_component_grant_defaults_entire_authorization_set_to_deny(
    db_session: Session,
) -> None:
    incomplete = tuple(
        cell
        for cell in _transferable_cells()
        if not (
            cell.component == ContentComponent.MATERIAL_IMAGE
            and cell.use == ContentUse.CROSS_USER_MATCH
        )
    )
    _activate_license(db_session, cells=incomplete)
    authorization = SQLMatchAuthorization(db_session, clock=lambda: NOW)

    decision = authorization.authorize(
        owner_user_id=OWNER, content=_content(), purpose="cross_user_match"
    )

    assert decision.allowed is False
    assert "default_deny" in decision.reason
    assert decision.snapshot_ref is None


def test_malformed_snapshot_manifest_fails_closed(db_session: Session) -> None:
    authorization = SQLMatchAuthorization(db_session, clock=lambda: NOW)

    assert authorization.snapshot_current(snapshot_ref="not-a-snapshot-set") is False
    assert authorization.snapshot_current(
        snapshot_ref="license-snapshot-set@v1:not-a-uuid"
    ) is False
