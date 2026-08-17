from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


class ExternalProcessingAuthorizationError(RuntimeError):
    pass


_PROHIBITED_KEYS = {
    "userid",
    "owneruserid",
    "learneruserid",
    "openid",
    "unionid",
    "appleidentity",
    "nickname",
    "displayname",
    "avatar",
    "avatarurl",
    "email",
    "phone",
    "phonenumber",
    "mobilenumber",
    "objectref",
    "accesskey",
    "secretkey",
    "authorization",
}


def endpoint_fingerprint(value: str) -> str:
    """Fingerprint one credential-free, normalized processor endpoint."""

    raw = value.strip().rstrip("/")
    if not raw:
        raise ValueError("processor endpoint is empty")
    parsed = urlsplit(raw)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("processor endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("processor endpoint must not contain query or fragment data")
    return sha256(raw.encode("utf-8")).hexdigest()


def validate_minimized_external_payload(payload: Any) -> None:
    """Reject account identifiers, storage handles and credentials before egress."""

    violations: set[str] = set()

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                child = f"{path}.{key}" if path else str(key)
                if normalized in _PROHIBITED_KEYS:
                    violations.add(child)
                visit(item, child)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload, "")
    if violations:
        raise ValueError(
            "external payload contains prohibited account or storage fields: "
            + ", ".join(sorted(violations))
        )


class ExternalProcessingRepository:
    def cause_routes_require_subject_consent(
        self,
        session: Session,
        *,
        provider_releases: tuple[str, ...],
        at: datetime | None = None,
    ) -> bool:
        releases = tuple(value.strip() for value in provider_releases if value.strip())
        if not releases:
            return False
        checked_at = at or datetime.now(UTC)
        return bool(
            session.execute(
                text(
                    """
                    select exists (
                      select 1
                      from ai_audit.processor_release_records
                      where provider_release = any(cast(:provider_releases as text[]))
                        and real_user_status = 'approved'
                        and authorization_mode = 'user_consent'
                        and purposes ? 'cause_candidate_generate'
                        and valid_from <= :checked_at
                        and (valid_until is null or valid_until > :checked_at)
                    )
                    """
                ),
                {
                    "provider_releases": list(releases),
                    "checked_at": checked_at,
                },
            ).scalar_one()
        )

    def require_current_cause_consent(
        self,
        session: Session,
        *,
        user_id: str,
        at: datetime | None = None,
    ) -> str:
        checked_at = at or datetime.now(UTC)
        event = (
            session.execute(
                text(
                    """
                    select event.consent_event_id, event.action
                    from privacy.policy_versions policy
                    left join lateral (
                      select consent_event_id, action
                      from privacy.consent_events
                      where user_id = cast(:user_id as uuid)
                        and policy_version = policy.policy_version
                        and created_at <= :checked_at
                      order by created_at desc, consent_event_id desc
                      limit 1
                    ) event on true
                    where policy.state = 'active'
                      and policy.content->>'policyKind' = 'xingce_cause_external_ai'
                    """
                ),
                {"user_id": user_id, "checked_at": checked_at},
            )
            .mappings()
            .one_or_none()
        )
        if event is None or str(event["action"]) != "accepted":
            raise ExternalProcessingAuthorizationError(
                "external AI consent is required before cause analysis"
            )
        return str(event["consent_event_id"])

    def _active_release(
        self,
        session: Session,
        *,
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        purpose: str,
        data_categories: tuple[str, ...],
        at: datetime,
    ) -> Any | None:
        return (
            session.execute(
                text(
                    """
                    select processor_release_record_id, authorization_mode,
                           consent_policy_version
                    from ai_audit.processor_release_records
                    where provider_release = :provider_release
                      and endpoint_fingerprint = :endpoint_fingerprint
                      and real_user_status = 'approved'
                      and valid_from <= :checked_at
                      and (valid_until is null or valid_until > :checked_at)
                      and purposes ? :purpose
                      and not exists (
                        select 1
                        from jsonb_array_elements_text(
                          cast(:data_categories as jsonb)
                        ) category
                        where not (processor_release_records.data_categories ? category)
                      )
                    order by published_at desc
                    limit 1
                    """
                ),
                {
                    "provider_release": declared_provider_release,
                    "endpoint_fingerprint": provider_endpoint_fingerprint,
                    "checked_at": at,
                    "purpose": purpose,
                    "data_categories": json.dumps(data_categories),
                },
            )
            .mappings()
            .one_or_none()
        )

    def require_active_release(
        self,
        session: Session,
        *,
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        purpose: str,
        data_categories: tuple[str, ...],
        at: datetime | None = None,
    ) -> str:
        checked_at = at or datetime.now(UTC)
        release = self._active_release(
            session,
            declared_provider_release=declared_provider_release,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            purpose=purpose,
            data_categories=data_categories,
            at=checked_at,
        )
        if release is None:
            raise ExternalProcessingAuthorizationError(
                "no active processor release permits this real-user dispatch"
            )
        return str(release["processor_release_record_id"])

    def require_subject_authorization(
        self,
        session: Session,
        *,
        user_id: str | None,
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        purpose: str,
        data_categories: tuple[str, ...],
        at: datetime | None = None,
    ) -> tuple[str, str | None]:
        checked_at = at or datetime.now(UTC)
        release = self._active_release(
            session,
            declared_provider_release=declared_provider_release,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            purpose=purpose,
            data_categories=data_categories,
            at=checked_at,
        )
        if release is None:
            raise ExternalProcessingAuthorizationError(
                "no active processor release permits this real-user dispatch"
            )
        release_id = str(release["processor_release_record_id"])
        if str(release["authorization_mode"]) == "contracted_data_protection":
            return release_id, None
        policy_version = release["consent_policy_version"]
        if user_id is None or policy_version is None:
            raise ExternalProcessingAuthorizationError(
                "external AI consent is required before this dispatch"
            )
        consent = (
            session.execute(
                text(
                    """
                    select consent_event_id, action
                    from privacy.consent_events
                    where user_id = cast(:user_id as uuid)
                      and policy_version = :policy_version
                      and created_at <= :checked_at
                    order by created_at desc, consent_event_id desc
                    limit 1
                    """
                ),
                {
                    "user_id": user_id,
                    "policy_version": str(policy_version),
                    "checked_at": checked_at,
                },
            )
            .mappings()
            .one_or_none()
        )
        if consent is None or str(consent["action"]) != "accepted":
            raise ExternalProcessingAuthorizationError(
                "external AI consent is required before this dispatch"
            )
        return release_id, str(consent["consent_event_id"])

    def record_model_dispatch(
        self,
        session: Session,
        *,
        call_id: UUID,
        user_id: str | None,
        scenario_key: str,
        purpose: str,
        data_categories: tuple[str, ...],
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        input_fingerprint: str,
        production_authorization_required: bool,
        dispatched_at: datetime | None = None,
    ) -> UUID:
        return self._record(
            session,
            model_call_id=call_id,
            external_operation_id=None,
            community_candidate_id=None,
            user_id=user_id,
            scenario_key=scenario_key,
            purpose=purpose,
            data_categories=data_categories,
            declared_provider_release=declared_provider_release,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            input_fingerprint=input_fingerprint,
            production_authorization_required=production_authorization_required,
            dispatched_at=dispatched_at,
        )

    def record_recognition_dispatch(
        self,
        session: Session,
        *,
        operation_id: UUID,
        user_id: str,
        scenario_key: str,
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        input_fingerprint: str,
        production_authorization_required: bool,
        dispatched_at: datetime | None = None,
    ) -> UUID:
        return self.record_external_operation_dispatch(
            session,
            operation_id=operation_id,
            user_id=user_id,
            scenario_key=scenario_key,
            purpose="recognition_process",
            data_categories=("uploaded_image",),
            declared_provider_release=declared_provider_release,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            input_fingerprint=input_fingerprint,
            production_authorization_required=production_authorization_required,
            dispatched_at=dispatched_at,
        )

    def record_external_operation_dispatch(
        self,
        session: Session,
        *,
        operation_id: UUID,
        user_id: str,
        scenario_key: str,
        purpose: str,
        data_categories: tuple[str, ...],
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        input_fingerprint: str,
        production_authorization_required: bool,
        dispatched_at: datetime | None = None,
    ) -> UUID:
        return self._record(
            session,
            model_call_id=None,
            external_operation_id=operation_id,
            community_candidate_id=None,
            user_id=user_id,
            scenario_key=scenario_key,
            purpose=purpose,
            data_categories=data_categories,
            declared_provider_release=declared_provider_release,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            input_fingerprint=input_fingerprint,
            production_authorization_required=production_authorization_required,
            dispatched_at=dispatched_at,
        )

    def record_community_candidate_dispatch(
        self,
        session: Session,
        *,
        candidate_id: UUID,
        user_id: str,
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        input_fingerprint: str,
        production_authorization_required: bool,
        dispatched_at: datetime | None = None,
    ) -> UUID:
        return self._record(
            session,
            model_call_id=None,
            external_operation_id=None,
            community_candidate_id=candidate_id,
            user_id=user_id,
            scenario_key="community_method_candidate",
            purpose="community_candidate_generate",
            data_categories=("community_content",),
            declared_provider_release=declared_provider_release,
            provider_endpoint_fingerprint=provider_endpoint_fingerprint,
            input_fingerprint=input_fingerprint,
            production_authorization_required=production_authorization_required,
            dispatched_at=dispatched_at,
        )

    def _record(
        self,
        session: Session,
        *,
        model_call_id: UUID | None,
        external_operation_id: UUID | None,
        community_candidate_id: UUID | None,
        user_id: str | None,
        scenario_key: str,
        purpose: str,
        data_categories: tuple[str, ...],
        declared_provider_release: str,
        provider_endpoint_fingerprint: str,
        input_fingerprint: str,
        production_authorization_required: bool,
        dispatched_at: datetime | None,
        receipt_phase: str = "authorized_dispatch_intent",
    ) -> UUID:
        if not declared_provider_release.strip():
            raise ExternalProcessingAuthorizationError(
                "external processor release identity is required"
            )
        if len(provider_endpoint_fingerprint) != 64:
            raise ExternalProcessingAuthorizationError(
                "external processor endpoint fingerprint is invalid"
            )
        if not data_categories or any(not item.strip() for item in data_categories):
            raise ExternalProcessingAuthorizationError(
                "external processing data categories are required"
            )
        if receipt_phase != "authorized_dispatch_intent":
            raise ExternalProcessingAuthorizationError(
                "external processing receipt phase is unsupported"
            )
        at = dispatched_at or datetime.now(UTC)
        release_record_id: str | None = None
        consent_event_id: str | None = None
        authorization_state = "local_unreleased"
        if production_authorization_required:
            release_record_id, consent_event_id = self.require_subject_authorization(
                session,
                user_id=user_id,
                declared_provider_release=declared_provider_release,
                provider_endpoint_fingerprint=provider_endpoint_fingerprint,
                purpose=purpose,
                data_categories=data_categories,
                at=at,
            )
            authorization_state = "real_user_approved"

        receipt_id = uuid4()
        session.execute(
            text(
                """
                insert into ai_audit.external_processing_receipts (
                  receipt_id, user_id, model_call_id, external_operation_id,
                  community_candidate_id,
                  processor_release_record_id, consent_event_id,
                  declared_provider_release,
                  endpoint_fingerprint, scenario_key, purpose, data_categories,
                  input_fingerprint, authorization_state, receipt_phase,
                  dispatched_at
                ) values (
                  cast(:receipt_id as uuid), cast(:user_id as uuid),
                  cast(:model_call_id as uuid), cast(:external_operation_id as uuid),
                  cast(:community_candidate_id as uuid),
                  :release_record_id, cast(:consent_event_id as uuid),
                  :declared_provider_release,
                  :endpoint_fingerprint, :scenario_key, :purpose,
                  cast(:data_categories as jsonb), :input_fingerprint,
                  :authorization_state, :receipt_phase, :dispatched_at
                )
                on conflict do nothing
                """
            ),
            {
                "receipt_id": str(receipt_id),
                "user_id": user_id,
                "model_call_id": str(model_call_id) if model_call_id else None,
                "external_operation_id": (
                    str(external_operation_id) if external_operation_id else None
                ),
                "community_candidate_id": (
                    str(community_candidate_id) if community_candidate_id else None
                ),
                "release_record_id": release_record_id,
                "consent_event_id": consent_event_id,
                "declared_provider_release": declared_provider_release,
                "endpoint_fingerprint": provider_endpoint_fingerprint,
                "scenario_key": scenario_key,
                "purpose": purpose,
                "data_categories": json.dumps(data_categories),
                "input_fingerprint": input_fingerprint,
                "authorization_state": authorization_state,
                "receipt_phase": receipt_phase,
                "dispatched_at": at,
            },
        )
        existing = (
            session.execute(
                text(
                    """
                select receipt_id, user_id, processor_release_record_id,
                       consent_event_id,
                       declared_provider_release, endpoint_fingerprint,
                       scenario_key, purpose, data_categories, input_fingerprint,
                       authorization_state, receipt_phase
                from ai_audit.external_processing_receipts
                where model_call_id = cast(:model_call_id as uuid)
                   or external_operation_id = cast(:external_operation_id as uuid)
                   or community_candidate_id = cast(:community_candidate_id as uuid)
                """
                ),
                {
                    "model_call_id": str(model_call_id) if model_call_id else None,
                    "external_operation_id": (
                        str(external_operation_id) if external_operation_id else None
                    ),
                    "community_candidate_id": (
                        str(community_candidate_id) if community_candidate_id else None
                    ),
                },
            )
            .mappings()
            .one()
        )
        expected = (
            str(user_id) if user_id is not None else None,
            release_record_id,
            consent_event_id,
            declared_provider_release,
            provider_endpoint_fingerprint,
            scenario_key,
            purpose,
            tuple(data_categories),
            input_fingerprint,
            authorization_state,
            receipt_phase,
        )
        observed = (
            str(existing["user_id"]) if existing["user_id"] is not None else None,
            existing["processor_release_record_id"],
            (
                str(existing["consent_event_id"])
                if existing["consent_event_id"] is not None
                else None
            ),
            str(existing["declared_provider_release"]),
            str(existing["endpoint_fingerprint"]),
            str(existing["scenario_key"]),
            str(existing["purpose"]),
            tuple(existing["data_categories"]),
            str(existing["input_fingerprint"]),
            str(existing["authorization_state"]),
            str(existing["receipt_phase"]),
        )
        if observed != expected:
            raise ExternalProcessingAuthorizationError(
                "external processing receipt replay changed its authorization contract"
            )
        return UUID(str(existing["receipt_id"]))
