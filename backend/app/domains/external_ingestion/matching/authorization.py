from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

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
    LicenseUseRequest,
    Territory,
    TransferableRights,
    canonical_record_hash,
)
from app.domains.content_catalog.licensing.service import (
    ContentLicenseNotFound,
    ContentLicenseService,
)
from app.domains.external_ingestion.matching.models import (
    AuthorizationDecision,
    AuthorizationPurpose,
    IndexedContent,
)


_SNAPSHOT_SET_PREFIX = "license-snapshot-set@v1:"


class SQLMatchAuthorization:
    """Map every matching use to the closed content-license tuple universe."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or (lambda: datetime.now(UTC))
        self._licenses = ContentLicenseService(session=session, clock=self._clock)

    def authorize(
        self,
        *,
        owner_user_id: UUID,
        content: IndexedContent,
        purpose: AuthorizationPurpose,
    ) -> AuthorizationDecision:
        records = self._active_records(content_version_id=content.content_version_id)
        applicable = [
            record
            for record in records
            if _record_applies(
                record=record,
                content=content,
                owner_user_id=owner_user_id,
                purpose=purpose,
            )
        ]
        if not applicable:
            return AuthorizationDecision(
                allowed=False, reason="no_applicable_active_license"
            )
        requests = _license_requests(
            content=content,
            purpose=purpose,
            requested_at=self._clock(),
        )
        denied_reasons: list[str] = []
        for record in applicable:
            snapshots: list[UUID] = []
            denied = False
            for request_values in requests:
                decision = self._licenses.evaluate(
                    LicenseUseRequest(
                        contentLicenseId=record.content_license_id,
                        contentVersionId=record.content_version_id,
                        expectedAuthorizationEpoch=record.authorization_epoch,
                        requestedAt=request_values.requested_at,
                        component=request_values.component,
                        use=request_values.use,
                        channel=request_values.channel,
                        audience=request_values.audience,
                        transform=request_values.transform,
                        territory=request_values.territory,
                    )
                )
                if not decision.allowed:
                    denied_reasons.append(decision.reason)
                    denied = True
                    break
                snapshots.append(decision.snapshot_id)
            if not denied:
                return AuthorizationDecision(
                    allowed=True,
                    reason="explicit_grant_set",
                    snapshotRef=_encode_snapshot_set(tuple(snapshots)),
                )
        reason = (
            "license_default_deny:" + ",".join(sorted(set(denied_reasons)))
            if denied_reasons
            else "license_default_deny"
        )
        return AuthorizationDecision(allowed=False, reason=reason)

    def snapshot_current(self, *, snapshot_ref: str) -> bool:
        snapshot_ids = _decode_snapshot_set(snapshot_ref)
        if not snapshot_ids:
            return False
        try:
            return all(
                self._licenses.snapshot_status(snapshot_id=snapshot_id).valid
                for snapshot_id in snapshot_ids
            )
        except ContentLicenseNotFound:
            return False

    def _active_records(self, *, content_version_id: str) -> tuple[ContentLicenseRecord, ...]:
        rows = self._session.execute(
            text(
                """
                select version.record_payload
                from product.content_license_heads head
                join product.content_license_record_versions version
                  on version.record_version_id = head.record_version_id
                where head.content_version_id = :content_version_id
                  and head.state = 'active'
                order by head.updated_at desc, head.content_license_id
                """
            ),
            {"content_version_id": content_version_id},
        ).scalars()
        records: list[ContentLicenseRecord] = []
        for payload in rows:
            record = ContentLicenseRecord.model_validate(payload)
            if canonical_record_hash(record) != record.record_canonical_hash:
                raise RuntimeError("active content-license record hash is invalid")
            records.append(record)
        return tuple(records)


def _record_applies(
    *,
    record: ContentLicenseRecord,
    content: IndexedContent,
    owner_user_id: UUID,
    purpose: AuthorizationPurpose,
) -> bool:
    rights = record.rights
    if content.distribution_class == "account_private":
        if not isinstance(rights, AccountPrivateRights):
            return False
        if rights.subject_scope_ref != f"account:{owner_user_id}":
            return False
    elif not isinstance(rights, TransferableRights):
        return False
    if purpose == "owner_private_match":
        return isinstance(rights, AccountPrivateRights)
    if purpose == "cross_user_match":
        return isinstance(rights, TransferableRights)
    return True


def _license_requests(
    *,
    content: IndexedContent,
    purpose: AuthorizationPurpose,
    requested_at: datetime,
) -> tuple[LicenseUseRequest, ...]:
    # License id/version/epoch are bound immediately before evaluation; these
    # placeholder identity values are deliberately overwritten by the adapter.
    placeholder = {
        "contentLicenseId": UUID(int=0),
        "contentVersionId": content.content_version_id,
        "requestedAt": requested_at,
    }
    if purpose in {"owner_private_match", "cross_user_match"}:
        components = [ContentComponent.QUESTION_PROMPT]
        if content.option_texts:
            components.append(ContentComponent.QUESTION_OPTIONS)
        if content.material_texts:
            components.append(ContentComponent.MATERIAL_TEXT)
        if content.visual_fingerprints:
            components.append(ContentComponent.MATERIAL_IMAGE)
        if purpose == "owner_private_match":
            use = ContentUse.IN_APP_PRACTICE
            audience = Audience.ACCOUNT_OWNER
        else:
            use = ContentUse.CROSS_USER_MATCH
            audience = Audience.AUTHENTICATED_USERS
        return tuple(
            LicenseUseRequest(
                **placeholder,
                component=component,
                use=use,
                channel=DeliveryChannel.INTERNAL_RETRIEVAL,
                audience=audience,
                transform=ContentTransform.EMBEDDING,
                territory=Territory.MAINLAND_CHINA,
            )
            for component in components
        )
    if purpose == "reference_answer":
        return (
            LicenseUseRequest(
                **placeholder,
                component=ContentComponent.REFERENCE_ANSWER,
                use=ContentUse.IN_APP_PRACTICE,
                channel=DeliveryChannel.WECHAT_MINIPROGRAM,
                audience=Audience.ACCOUNT_OWNER,
                transform=ContentTransform.VERBATIM,
                territory=Territory.MAINLAND_CHINA,
            ),
        )
    components = [ContentComponent.QUESTION_PROMPT]
    if content.option_texts:
        components.append(ContentComponent.QUESTION_OPTIONS)
    if content.material_texts:
        components.append(ContentComponent.MATERIAL_TEXT)
    if content.visual_fingerprints:
        components.append(ContentComponent.MATERIAL_IMAGE)
    if content.reference_answer:
        components.append(ContentComponent.REFERENCE_ANSWER)
    if content.solution_explanation:
        components.append(ContentComponent.SOLUTION_EXPLANATION)
    return tuple(
        LicenseUseRequest(
            **placeholder,
            component=component,
            use=ContentUse.AI_GROUNDING,
            channel=DeliveryChannel.AI_PROCESSOR,
            audience=Audience.ACCOUNT_OWNER,
            transform=ContentTransform.VERBATIM,
            territory=Territory.MAINLAND_CHINA,
        )
        for component in components
    )


def _encode_snapshot_set(snapshot_ids: tuple[UUID, ...]) -> str:
    if not snapshot_ids:
        raise RuntimeError("an allowed authorization requires at least one snapshot")
    encoded = _SNAPSHOT_SET_PREFIX + ",".join(str(item) for item in snapshot_ids)
    if len(encoded) > 500:
        raise RuntimeError("authorization snapshot manifest exceeds contract size")
    return encoded


def _decode_snapshot_set(value: str) -> tuple[UUID, ...]:
    if not value.startswith(_SNAPSHOT_SET_PREFIX):
        return ()
    parts = value.removeprefix(_SNAPSHOT_SET_PREFIX).split(",")
    if not parts or any(not part for part in parts):
        return ()
    try:
        parsed = tuple(UUID(part) for part in parts)
    except ValueError:
        return ()
    return parsed if len(parsed) == len(set(parsed)) else ()
