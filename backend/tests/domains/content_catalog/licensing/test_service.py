from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

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
    LicenseUseRequest,
    ReviewerReceipt,
    Territory,
    canonical_grant_matrix_hash,
)
from app.domains.content_catalog.licensing.service import (
    ContentLicenseIdempotencyConflict,
    ContentLicenseService,
    ContentLicenseVersionConflict,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _matrix(*cells: LicenseGrantCell) -> LicenseGrantMatrix:
    payload: dict[str, object] = {
        "matrixVersionRef": "license-grant-matrix@v1",
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


def _allowed_cell() -> LicenseGrantCell:
    return LicenseGrantCell(
        component=ContentComponent.QUESTION_PROMPT,
        use=ContentUse.IN_APP_PRACTICE,
        channel=DeliveryChannel.WECHAT_MINIPROGRAM,
        audience=Audience.ACCOUNT_OWNER,
        transform=ContentTransform.VERBATIM,
        territory=Territory.MAINLAND_CHINA,
    )


def _draft(
    *,
    content_license_id: UUID | None = None,
    content_version_id: str = "question-version-1",
    evidence_hash: str = "3" * 64,
    supersedes_content_license_id: UUID | None = None,
) -> ContentLicenseRecord:
    return ContentLicenseRecord(
        contentLicenseId=content_license_id or uuid4(),
        contentVersionId=content_version_id,
        revision=1,
        authorizationEpoch=0,
        state=LicenseState.DRAFT,
        createdByActorId="author-1",
        closedCoverageVersionRef="content-license-coverage@v1",
        closedCoverageHash="2" * 64,
        rights=AccountPrivateRights(
            sourceId="private-upload:artifact-1",
            term=LicenseTerm(
                termKind="revocable_open_term",
                effectiveAt=NOW - timedelta(minutes=1),
                expiresAt="not_applicable",
                renewalPolicyVersionRef="private-source-retention@v1",
            ),
            grantMatrix=_matrix(_allowed_cell()),
            distributionClass="account_private",
            rightsBasis="user_private_purpose_limited",
            subjectScopeRef="account:user-1",
            userAuthorizationReceiptRef="receipt:user-upload-1",
            userAuthorizationReceiptHash="1" * 64,
        ),
        evidenceManifestRef="evidence:manifest:1",
        evidenceManifestHash=evidence_hash,
        recordCanonicalHash="0" * 64,
        supersedesContentLicenseId=supersedes_content_license_id,
        createdAt=NOW,
        updatedAt=NOW,
    )


def _receipt(
    record: ContentLicenseRecord,
    *,
    reviewer_id: str,
    decision: str = "approved",
) -> ReviewerReceipt:
    return ReviewerReceipt(
        reviewerId=reviewer_id,
        decision=decision,
        reviewedRecordHash=record.record_canonical_hash,
        evidenceManifestHash=record.evidence_manifest_hash,
        grantMatrixHash=record.rights.grant_matrix.matrix_hash,
        reviewedAt=NOW,
    )


def _activate(
    session: Session,
    *,
    content_license_id: UUID | None = None,
    reviewer_id: str = "reviewer-1",
    supersedes_content_license_id: UUID | None = None,
) -> ContentLicenseRecord:
    service = ContentLicenseService(session=session, clock=MutableClock())
    draft = _draft(
        content_license_id=content_license_id,
        supersedes_content_license_id=supersedes_content_license_id,
    )
    saved = service.save_draft(
        actor_id="author-1",
        idempotency_key=f"draft-save-{draft.content_license_id}",
        expected_revision=0,
        record=draft,
    )
    review = service.submit_review(
        actor_id="author-1",
        idempotency_key=f"review-submit-{draft.content_license_id}",
        content_license_id=saved.content_license_id,
        expected_revision=1,
    )
    return service.activate(
        actor_id=reviewer_id,
        idempotency_key=f"activate-{reviewer_id}-{draft.content_license_id}",
        content_license_id=review.content_license_id,
        expected_revision=2,
        reviewer_receipt=_receipt(review, reviewer_id=reviewer_id),
    )


def _request(
    license_id: UUID,
    *,
    expected_epoch: int | None = 1,
    use: ContentUse = ContentUse.IN_APP_PRACTICE,
    channel: DeliveryChannel = DeliveryChannel.WECHAT_MINIPROGRAM,
    audience: Audience = Audience.ACCOUNT_OWNER,
) -> LicenseUseRequest:
    return LicenseUseRequest(
        contentLicenseId=license_id,
        contentVersionId="question-version-1",
        expectedAuthorizationEpoch=expected_epoch,
        component=ContentComponent.QUESTION_PROMPT,
        use=use,
        channel=channel,
        audience=audience,
        transform=ContentTransform.VERBATIM,
        territory=Territory.MAINLAND_CHINA,
        requestedAt=NOW,
    )


def test_explicit_grant_allows_exact_tuple_and_every_other_tuple_defaults_to_deny(
    db_session: Session,
) -> None:
    active = _activate(db_session)
    service = ContentLicenseService(session=db_session, clock=MutableClock())

    allowed = service.evaluate(_request(active.content_license_id))
    denied = service.evaluate(
        _request(
            active.content_license_id,
            use=ContentUse.SHARE_FORWARD,
            channel=DeliveryChannel.IMAGE_EXPORT,
            audience=Audience.AUTHORIZED_RECIPIENT,
        )
    )
    db_session.commit()

    assert active.state == LicenseState.ACTIVE
    assert active.authorization_epoch == 1
    assert allowed.allowed is True
    assert allowed.reason == "explicit_grant"
    assert denied.allowed is False
    assert denied.reason == "default_deny"
    assert db_session.execute(
        text(
            "select count(*) from product.content_license_authorization_snapshots"
        )
    ).scalar_one() == 2
    assert db_session.execute(
        text(
            "select count(*) from platform.outbox_events "
            "where aggregate_type = 'content_license'"
        )
    ).scalar_one() == 4


def test_command_replay_is_idempotent_and_changed_input_is_rejected(
    db_session: Session,
) -> None:
    service = ContentLicenseService(session=db_session, clock=MutableClock())
    draft = _draft()
    saved = service.save_draft(
        actor_id="author-1",
        idempotency_key="draft-stable-key",
        expected_revision=0,
        record=draft,
    )
    replay = service.save_draft(
        actor_id="author-1",
        idempotency_key="draft-stable-key",
        expected_revision=0,
        record=draft,
    )
    assert replay == saved
    assert db_session.execute(
        text(
            "select count(*) from product.content_license_record_versions "
            "where content_license_id = :license_id"
        ),
        {"license_id": str(draft.content_license_id)},
    ).scalar_one() == 1

    changed = _draft(
        content_license_id=draft.content_license_id,
        evidence_hash="4" * 64,
    )
    with pytest.raises(ContentLicenseIdempotencyConflict):
        service.save_draft(
            actor_id="author-1",
            idempotency_key="draft-stable-key",
            expected_revision=0,
            record=changed,
        )


def test_suspension_invalidates_old_epoch_and_history_is_append_only(
    db_session: Session,
) -> None:
    active = _activate(db_session)
    service = ContentLicenseService(session=db_session, clock=MutableClock())
    allowed = service.evaluate(_request(active.content_license_id))
    assert service.snapshot_status(snapshot_id=allowed.snapshot_id).valid is True
    suspended = service.suspend(
        actor_id="reviewer-1",
        idempotency_key="suspend-license-1",
        content_license_id=active.content_license_id,
        expected_revision=3,
        reason_code="rights_review_required",
    )
    stale = service.evaluate(
        _request(active.content_license_id, expected_epoch=1)
    )
    suspended_status = service.snapshot_status(snapshot_id=allowed.snapshot_id)
    reinstated = service.reinstate(
        actor_id="reviewer-2",
        idempotency_key="reinstate-license-1",
        content_license_id=active.content_license_id,
        expected_revision=4,
        reviewer_receipt=_receipt(suspended, reviewer_id="reviewer-2"),
    )
    reinstated_status = service.snapshot_status(snapshot_id=allowed.snapshot_id)
    db_session.commit()

    assert suspended.authorization_epoch == 2
    assert suspended_status.valid is False
    assert suspended_status.reason == "license_not_active"
    assert reinstated.authorization_epoch == 3
    assert reinstated_status.valid is False
    assert reinstated_status.reason == "authorization_epoch_changed"
    assert stale.allowed is False
    assert stale.reason == "license_not_active"
    with pytest.raises(DBAPIError):
        db_session.execute(
            text(
                "update product.content_license_record_versions "
                "set state = 'active' where content_license_id = :license_id"
            ),
            {"license_id": str(active.content_license_id)},
        )
    db_session.rollback()


def test_replacement_license_gets_a_content_global_epoch_and_can_supersede_old_head(
    db_session: Session,
) -> None:
    original = _activate(db_session, reviewer_id="reviewer-original")
    service = ContentLicenseService(session=db_session, clock=MutableClock())
    original_snapshot = service.evaluate(_request(original.content_license_id))

    replacement = _activate(
        db_session,
        reviewer_id="reviewer-replacement",
        supersedes_content_license_id=original.content_license_id,
    )
    assert replacement.authorization_epoch == 2
    superseded = service.supersede(
        actor_id="reviewer-original",
        idempotency_key="supersede-original-license",
        content_license_id=original.content_license_id,
        expected_revision=3,
        replacement_content_license_id=replacement.content_license_id,
    )
    old_status = service.snapshot_status(snapshot_id=original_snapshot.snapshot_id)
    db_session.commit()

    assert superseded.state == LicenseState.SUPERSEDED
    assert superseded.authorization_epoch == 3
    assert old_status.valid is False
    assert old_status.reason == "license_not_active"


def test_concurrent_activation_commits_exactly_one_authorization_epoch(
    db_session_factory: sessionmaker[Session],
) -> None:
    with db_session_factory() as setup:
        service = ContentLicenseService(session=setup, clock=MutableClock())
        draft = _draft()
        service.save_draft(
            actor_id="author-1",
            idempotency_key="race-draft-key",
            expected_revision=0,
            record=draft,
        )
        review = service.submit_review(
            actor_id="author-1",
            idempotency_key="race-review-key",
            content_license_id=draft.content_license_id,
            expected_revision=1,
        )
        setup.commit()

    barrier = Barrier(2)

    def activate(reviewer_id: str) -> str:
        with db_session_factory() as session:
            barrier.wait()
            service = ContentLicenseService(session=session, clock=MutableClock())
            try:
                service.activate(
                    actor_id=reviewer_id,
                    idempotency_key=f"race-activate-{reviewer_id}",
                    content_license_id=review.content_license_id,
                    expected_revision=2,
                    reviewer_receipt=_receipt(review, reviewer_id=reviewer_id),
                )
                session.commit()
                return "activated"
            except ContentLicenseVersionConflict:
                session.rollback()
                return "version_conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(activate, ("reviewer-a", "reviewer-b")))

    assert sorted(outcomes) == ["activated", "version_conflict"]
    with db_session_factory() as verify:
        rows = verify.execute(
            text(
                "select revision, authorization_epoch, state "
                "from product.content_license_heads "
                "where content_license_id = :license_id"
            ),
            {"license_id": str(review.content_license_id)},
        ).mappings().one()
        assert dict(rows) == {
            "revision": 3,
            "authorization_epoch": 1,
            "state": "active",
        }
        assert verify.execute(
            text(
                "select count(*) from product.content_license_commands "
                "where content_license_id = :license_id and command_type = 'activate'"
            ),
            {"license_id": str(review.content_license_id)},
        ).scalar_one() == 1
