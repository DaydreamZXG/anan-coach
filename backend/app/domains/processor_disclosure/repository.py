from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.processor_disclosure.schemas import (
    ExternalAIConsentView,
    ProcessorDisclosure,
    ProcessorDisclosureList,
)


CAUSE_EXTERNAL_AI_POLICY_KIND = "xingce_cause_external_ai"


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
                       authorization_mode, consent_policy_version,
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
                        str(row["operator_name"]) if row["operator_name"] is not None else None
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
                    authorizationMode=str(row["authorization_mode"]),
                    consentPolicyVersion=(
                        str(row["consent_policy_version"])
                        if row["consent_policy_version"] is not None
                        else None
                    ),
                    evidence=dict(row["evidence"]),
                    verifiedAt=row["verified_at"],
                    validUntil=row["valid_until"],
                )
                for row in rows
            ],
            generatedAt=generated_at,
        )

    def external_ai_consent(
        self,
        *,
        user_id: str,
        generated_at: datetime,
    ) -> ExternalAIConsentView:
        policy = (
            self._session.execute(
                text(
                    """
                select policy_version, title, content
                from privacy.policy_versions
                where state = 'active'
                  and content->>'policyKind' = :policy_kind
                """
                ),
                {"policy_kind": CAUSE_EXTERNAL_AI_POLICY_KIND},
            )
            .mappings()
            .one()
        )
        event = (
            self._session.execute(
                text(
                    """
                select consent_event_id, action, created_at
                from privacy.consent_events
                where user_id = cast(:user_id as uuid)
                  and policy_version = :policy_version
                order by created_at desc, consent_event_id desc
                limit 1
                """
                ),
                {"user_id": user_id, "policy_version": str(policy["policy_version"])},
            )
            .mappings()
            .one_or_none()
        )
        processors = [
            item
            for item in self.published(generated_at=generated_at).processors
            if item.authorization_mode == "user_consent"
            and item.consent_policy_version == str(policy["policy_version"])
            and item.real_user_status == "approved"
        ]
        content = dict(policy["content"])
        action = str(event["action"]) if event is not None else None
        return ExternalAIConsentView(
            policyVersion=str(policy["policy_version"]),
            state=(
                "accepted"
                if action == "accepted"
                else "withdrawn"
                if action == "withdrawn"
                else "not_accepted"
            ),
            consentEventId=(str(event["consent_event_id"]) if event is not None else None),
            acceptedAt=(
                event["created_at"] if event is not None and action == "accepted" else None
            ),
            withdrawnAt=(
                event["created_at"] if event is not None and action == "withdrawn" else None
            ),
            title=str(policy["title"]),
            summary=str(content["purpose"]),
            dataSent=list(content["dataSent"]),
            providerRisk=str(content["providerRisk"]),
            withdrawalEffect=str(content["withdrawal"]),
            processors=processors,
            generatedAt=generated_at,
        )

    def record_external_ai_consent(
        self,
        *,
        user_id: str,
        action: str,
        command_key: str,
        surface: str,
        generated_at: datetime,
    ) -> ExternalAIConsentView:
        if action not in {"accepted", "withdrawn"}:
            raise ValueError("unsupported external AI consent action")
        self._session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"external-ai-consent:{user_id}"},
        )
        policy_version = self._session.execute(
            text(
                """
                select policy_version
                from privacy.policy_versions
                where state = 'active'
                  and content->>'policyKind' = :policy_kind
                """
            ),
            {"policy_kind": CAUSE_EXTERNAL_AI_POLICY_KIND},
        ).scalar_one()
        current_action = self._session.execute(
            text(
                """
                select action
                from privacy.consent_events
                where user_id = cast(:user_id as uuid)
                  and policy_version = :policy_version
                order by created_at desc, consent_event_id desc
                limit 1
                """
            ),
            {"user_id": user_id, "policy_version": str(policy_version)},
        ).scalar_one_or_none()
        if current_action != action:
            self._session.execute(
                text(
                    """
                    insert into privacy.consent_events (
                      consent_event_id, user_id, policy_version, action,
                      surface, request_id, created_at
                    ) values (
                      gen_random_uuid(), cast(:user_id as uuid), :policy_version,
                      :action, :surface, :request_id, :created_at
                    )
                    on conflict (user_id, policy_version, action, request_id)
                      do nothing
                    """
                ),
                {
                    "user_id": user_id,
                    "policy_version": str(policy_version),
                    "action": action,
                    "surface": surface,
                    "request_id": command_key,
                    "created_at": generated_at,
                },
            )
        return self.external_ai_consent(user_id=user_id, generated_at=generated_at)
