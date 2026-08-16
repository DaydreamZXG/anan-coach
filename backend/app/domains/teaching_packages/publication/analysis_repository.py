from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.teaching_packages.publication.models import AnalysisPublicationPlan
from app.domains.teaching_packages.publication.projector import canonical_json
from app.domains.teaching_packages.publication.repository import (
    PublicationApplyResult,
    PublicationConflictError,
    publication_plan_sha256,
)


def _version_id(item: Any) -> str:
    canonical = {
        "evidenceRef": item.evidence_ref,
        "sourceAssetSha256": item.source_asset_sha256,
        "sourcePayloadSha256": item.source_payload_sha256,
        "verificationPolicyVersion": item.verification_policy_version,
        "payload": item.payload,
    }
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def _binding_id(item: Any, binding: Any, version_id: str) -> str:
    canonical = {
        "evidenceVersionId": version_id,
        "questionId": binding.question_id,
        "contentVersionId": binding.content_version_id,
        "analysisSource": binding.analysis_source,
        "sourceOccurrenceQuestionKey": binding.source_occurrence_question_key,
        "sourceImageRef": binding.source_image_ref,
        "runtimeObjectKey": binding.runtime_object_key,
        "sourceAssetSha256": item.source_asset_sha256,
    }
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


class AnalysisMediaPublicationRepository:
    """Append-only verifier evidence publisher for current explanation assets."""

    def apply(
        self,
        session: Session,
        *,
        plan: AnalysisPublicationPlan,
        expected_plan_sha256: str | None = None,
        created_at: datetime | None = None,
    ) -> PublicationApplyResult:
        plan_sha = publication_plan_sha256(plan)
        if expected_plan_sha256 is not None and plan_sha != expected_plan_sha256:
            raise PublicationConflictError("publication plan SHA-256 changed after dry-run")
        now = created_at or datetime.now(timezone.utc)
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"structured-visual-publication:{plan.batch_id}"},
        )
        existing = session.execute(
            text(
                """
                select plan_sha256, state, evidence_count, binding_count,
                       quarantined_count
                from product.structured_visual_evidence_import_receipts
                where batch_id = :batch_id
                """
            ),
            {"batch_id": plan.batch_id},
        ).mappings().one_or_none()
        if existing is not None:
            if str(existing["plan_sha256"]) != plan_sha:
                raise PublicationConflictError("batch identity already has a different plan")
            if str(existing["state"]) != "active":
                raise PublicationConflictError("withdrawn batch identity cannot be reused")
            return PublicationApplyResult(
                state="published",
                published_evidence=int(existing["evidence_count"]),
                published_bindings=int(existing["binding_count"]),
                quarantined=int(existing["quarantined_count"]),
                replayed=True,
            )

        self._assert_plan_is_current(session, plan)
        refs = [item.evidence_ref for item in plan.publishable_items]
        previous_heads = {
            str(row["evidence_ref"]): {
                "evidenceVersionId": str(row["evidence_version_id"]),
                "activatedByBatchId": str(row["activated_by_batch_id"]),
                "updatedAt": row["updated_at"].isoformat(),
            }
            for row in session.execute(
                text(
                    """
                    select evidence_ref, evidence_version_id,
                           activated_by_batch_id, updated_at
                    from product.structured_visual_evidence_heads
                    where evidence_ref = any(cast(:refs as text[]))
                    """
                ),
                {"refs": refs},
            ).mappings()
        } if refs else {}

        for item in plan.publishable_items:
            version_id = _version_id(item)
            existing_version = session.execute(
                text(
                    """
                    select evidence_version_id
                    from product.structured_visual_evidence_versions
                    where evidence_ref = :evidence_ref
                      and source_payload_sha256 = :source_payload_sha256
                      and verification_policy_version = :policy
                    """
                ),
                {
                    "evidence_ref": item.evidence_ref,
                    "source_payload_sha256": item.source_payload_sha256,
                    "policy": item.verification_policy_version,
                },
            ).scalar_one_or_none()
            if existing_version is None:
                revision = int(
                    session.execute(
                        text(
                            """
                            select coalesce(max(revision), 0) + 1
                            from product.structured_visual_evidence_versions
                            where evidence_ref = :evidence_ref
                            """
                        ),
                        {"evidence_ref": item.evidence_ref},
                    ).scalar_one()
                )
                session.execute(
                    text(
                        """
                        insert into product.structured_visual_evidence_versions (
                          evidence_version_id, evidence_ref, revision,
                          source_asset_sha256, source_payload_sha256,
                          verification_policy_version, chart_type, payload, created_at
                        ) values (
                          :version_id, :evidence_ref, :revision,
                          :source_asset_sha256, :source_payload_sha256,
                          :policy, :chart_type, cast(:payload as jsonb), :created_at
                        )
                        """
                    ),
                    {
                        "version_id": version_id,
                        "evidence_ref": item.evidence_ref,
                        "revision": revision,
                        "source_asset_sha256": item.source_asset_sha256,
                        "source_payload_sha256": item.source_payload_sha256,
                        "policy": item.verification_policy_version,
                        "chart_type": str(item.payload["chartType"]),
                        "payload": canonical_json(item.payload),
                        "created_at": now,
                    },
                )
            elif str(existing_version) != version_id:
                raise PublicationConflictError(
                    "existing evidence version payload is inconsistent"
                )
            session.execute(
                text(
                    """
                    insert into product.structured_visual_evidence_heads (
                      evidence_ref, evidence_version_id,
                      activated_by_batch_id, updated_at
                    ) values (
                      :evidence_ref, :version_id, :batch_id, :updated_at
                    )
                    on conflict (evidence_ref) do update set
                      evidence_version_id = excluded.evidence_version_id,
                      activated_by_batch_id = excluded.activated_by_batch_id,
                      updated_at = excluded.updated_at
                    """
                ),
                {
                    "evidence_ref": item.evidence_ref,
                    "version_id": version_id,
                    "batch_id": plan.batch_id,
                    "updated_at": now,
                },
            )
            for binding in item.analysis_bindings:
                session.execute(
                    text(
                        """
                        insert into product.question_analysis_visual_evidence_bindings (
                          binding_id, evidence_ref, evidence_version_id,
                          question_id, content_version_id, analysis_source,
                          source_occurrence_question_key, source_image_ref,
                          runtime_object_key, source_asset_sha256, batch_id, bound_at
                        ) values (
                          :binding_id, :evidence_ref, :version_id,
                          :question_id, :content_version_id, :analysis_source,
                          :source_occurrence_question_key, :source_image_ref,
                          nullif(:runtime_object_key, ''), :source_asset_sha256,
                          :batch_id, :bound_at
                        )
                        on conflict (
                          evidence_ref, evidence_version_id, question_id,
                          content_version_id, source_occurrence_question_key,
                          source_image_ref, source_asset_sha256
                        ) do nothing
                        """
                    ),
                    {
                        "binding_id": _binding_id(item, binding, version_id),
                        "evidence_ref": item.evidence_ref,
                        "version_id": version_id,
                        "question_id": binding.question_id,
                        "content_version_id": binding.content_version_id,
                        "analysis_source": binding.analysis_source,
                        "source_occurrence_question_key": (
                            binding.source_occurrence_question_key
                        ),
                        "source_image_ref": binding.source_image_ref,
                        "runtime_object_key": binding.runtime_object_key,
                        "source_asset_sha256": item.source_asset_sha256,
                        "batch_id": plan.batch_id,
                        "bound_at": now,
                    },
                )

        report = {
            "planSha256": plan_sha,
            "bindingKind": "analysis_media",
            "publishedEvidenceRefs": [
                item.evidence_ref for item in plan.publishable_items
            ],
            "quarantined": {
                item.evidence_ref: [reason.value for reason in item.quarantine_reasons]
                for item in plan.quarantined_items
            },
        }
        session.execute(
            text(
                """
                insert into product.structured_visual_evidence_import_receipts (
                  batch_id, plan_sha256, verification_policy_version, state,
                  evidence_count, binding_count, quarantined_count,
                  previous_heads, report, created_at, withdrawn_at
                ) values (
                  :batch_id, :plan_sha, :policy, 'active',
                  :evidence_count, :binding_count, :quarantined_count,
                  cast(:previous_heads as jsonb), cast(:report as jsonb),
                  :created_at, null
                )
                """
            ),
            {
                "batch_id": plan.batch_id,
                "plan_sha": plan_sha,
                "policy": plan.policy_version,
                "evidence_count": len(plan.publishable_items),
                "binding_count": plan.binding_count,
                "quarantined_count": len(plan.quarantined_items),
                "previous_heads": json.dumps(previous_heads, sort_keys=True),
                "report": canonical_json(report),
                "created_at": now,
            },
        )
        return PublicationApplyResult(
            state="published",
            published_evidence=len(plan.publishable_items),
            published_bindings=plan.binding_count,
            quarantined=len(plan.quarantined_items),
        )

    def _assert_plan_is_current(
        self, session: Session, plan: AnalysisPublicationPlan
    ) -> None:
        for item in plan.publishable_items:
            for binding in item.analysis_bindings:
                current = session.execute(
                    text(
                        """
                        select exists (
                          select 1
                          from product.questions question
                          join product.question_content_heads head
                            on head.question_id = question.question_id
                          join product.question_content_versions version
                            on version.question_id = head.question_id
                           and version.content_version_id = head.content_version_id
                          join product.question_explanations explanation
                            on explanation.question_id = question.question_id
                          join product.question_occurrences occurrence
                            on occurrence.question_id = question.question_id
                           and occurrence.source_occurrence_question_key =
                               :source_occurrence_question_key
                          where question.question_id = :question_id
                            and question.status in ('active', 'canonical')
                            and head.content_version_id = :content_version_id
                            and version.snapshot_payload ->> 'analysis_source' =
                                :analysis_source
                            and explanation.analysis_source = :analysis_source
                            and (
                              nullif(:runtime_object_key, '') is null
                              or exists (
                                select 1
                                from jsonb_array_elements(
                                  coalesce(
                                    explanation.final_analysis_asset_refs -> 'assets',
                                    '[]'::jsonb
                                  )
                                ) asset
                                where coalesce(
                                        asset ->> 'objectKey',
                                        asset ->> 'object_key'
                                      ) = :runtime_object_key
                                  and coalesce(
                                        asset ->> 'contentSha256',
                                        asset ->> 'content_sha256'
                                      ) = :source_asset_sha256
                                  and coalesce(
                                        asset ->> 'imageSrc',
                                        asset ->> 'image_src'
                                      ) = :source_image_ref
                              )
                            )
                        )
                        """
                    ),
                    {
                        "question_id": binding.question_id,
                        "content_version_id": binding.content_version_id,
                        "analysis_source": binding.analysis_source,
                        "source_occurrence_question_key": (
                            binding.source_occurrence_question_key
                        ),
                        "source_image_ref": binding.source_image_ref,
                        "source_asset_sha256": item.source_asset_sha256,
                        "runtime_object_key": binding.runtime_object_key,
                    },
                ).scalar_one()
                if not bool(current):
                    raise PublicationConflictError("PUBLICATION_STALE")

    def verify(
        self, session: Session, *, plan: AnalysisPublicationPlan
    ) -> PublicationApplyResult:
        plan_sha = publication_plan_sha256(plan)
        receipt = session.execute(
            text(
                """
                select state, evidence_count, binding_count, quarantined_count
                from product.structured_visual_evidence_import_receipts
                where batch_id = :batch_id and plan_sha256 = :plan_sha
                """
            ),
            {"batch_id": plan.batch_id, "plan_sha": plan_sha},
        ).mappings().one_or_none()
        if receipt is None or str(receipt["state"]) != "active":
            raise PublicationConflictError("publication receipt is missing or withdrawn")
        self._assert_plan_is_current(session, plan)
        active_bindings = int(
            session.execute(
                text(
                    """
                    select count(*)
                    from product.question_analysis_visual_evidence_bindings binding
                    join product.structured_visual_evidence_heads head
                      on head.evidence_ref = binding.evidence_ref
                     and head.evidence_version_id = binding.evidence_version_id
                    join product.question_content_heads content_head
                      on content_head.question_id = binding.question_id
                     and content_head.content_version_id = binding.content_version_id
                    where binding.batch_id = :batch_id
                    """
                ),
                {"batch_id": plan.batch_id},
            ).scalar_one()
        )
        if active_bindings != plan.binding_count:
            raise PublicationConflictError("published binding count does not match plan")
        return PublicationApplyResult(
            state="published",
            published_evidence=int(receipt["evidence_count"]),
            published_bindings=active_bindings,
            quarantined=int(receipt["quarantined_count"]),
            replayed=True,
        )

    def rollback(
        self,
        session: Session,
        *,
        batch_id: str,
        withdrawn_at: datetime | None = None,
    ) -> PublicationApplyResult:
        now = withdrawn_at or datetime.now(timezone.utc)
        session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"structured-visual-publication:{batch_id}"},
        )
        receipt = session.execute(
            text(
                """
                select state, evidence_count, binding_count, quarantined_count,
                       previous_heads
                from product.structured_visual_evidence_import_receipts
                where batch_id = :batch_id
                for update
                """
            ),
            {"batch_id": batch_id},
        ).mappings().one_or_none()
        if receipt is None:
            raise PublicationConflictError("publication batch does not exist")
        if str(receipt["state"]) == "withdrawn":
            return PublicationApplyResult(
                state="withdrawn",
                published_evidence=int(receipt["evidence_count"]),
                published_bindings=0,
                quarantined=int(receipt["quarantined_count"]),
                replayed=True,
            )
        newer_head = session.execute(
            text(
                """
                select evidence_ref
                from product.structured_visual_evidence_heads
                where activated_by_batch_id <> :batch_id
                  and evidence_ref in (
                    select evidence_ref
                    from product.question_analysis_visual_evidence_bindings
                    where batch_id = :batch_id
                  )
                limit 1
                """
            ),
            {"batch_id": batch_id},
        ).scalar_one_or_none()
        if newer_head is not None:
            raise PublicationConflictError("a newer publication head depends on this batch")
        session.execute(
            text(
                "delete from product.question_analysis_visual_evidence_bindings "
                "where batch_id = :batch_id"
            ),
            {"batch_id": batch_id},
        )
        previous_heads: dict[str, Any] = dict(receipt["previous_heads"])
        session.execute(
            text(
                "delete from product.structured_visual_evidence_heads "
                "where activated_by_batch_id = :batch_id"
            ),
            {"batch_id": batch_id},
        )
        for evidence_ref, previous in previous_heads.items():
            session.execute(
                text(
                    """
                    insert into product.structured_visual_evidence_heads (
                      evidence_ref, evidence_version_id,
                      activated_by_batch_id, updated_at
                    ) values (
                      :evidence_ref, :version_id, :previous_batch_id,
                      cast(:updated_at as timestamptz)
                    )
                    """
                ),
                {
                    "evidence_ref": evidence_ref,
                    "version_id": previous["evidenceVersionId"],
                    "previous_batch_id": previous["activatedByBatchId"],
                    "updated_at": previous["updatedAt"],
                },
            )
        session.execute(
            text(
                """
                update product.structured_visual_evidence_import_receipts
                set state = 'withdrawn', withdrawn_at = :withdrawn_at
                where batch_id = :batch_id
                """
            ),
            {"batch_id": batch_id, "withdrawn_at": now},
        )
        return PublicationApplyResult(
            state="withdrawn",
            published_evidence=int(receipt["evidence_count"]),
            published_bindings=0,
            quarantined=int(receipt["quarantined_count"]),
        )
