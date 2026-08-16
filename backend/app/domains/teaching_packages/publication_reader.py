from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domains.teaching_packages.structured_visual import (
    StructuredVisualEvidence,
    TeachingQuestionVisualBinding,
)


class StructuredVisualEvidenceReader:
    """Read every published visual for one exact current question head.

    Missing, quarantined, and all other inactive questions return ``None``;
    an active question without published visuals returns an empty binding.
    """

    def binding_for_question(
        self,
        session: Session,
        *,
        question_ref: str,
        visual_required: bool,
    ) -> TeachingQuestionVisualBinding | None:
        question_asset_rows = tuple(
            session.execute(
                text(
                    """
                    select binding.question_id, binding.content_version_id,
                           binding.source_asset_sha256, version.evidence_ref,
                           version.chart_type, version.payload,
                           asset.part_kind
                    from product.question_structured_visual_evidence_bindings binding
                    join product.structured_visual_evidence_heads evidence_head
                      on evidence_head.evidence_ref = binding.evidence_ref
                     and evidence_head.evidence_version_id = binding.evidence_version_id
                    join product.structured_visual_evidence_versions version
                      on version.evidence_version_id = binding.evidence_version_id
                    join product.question_content_heads content_head
                      on content_head.question_id = binding.question_id
                     and content_head.content_version_id = binding.content_version_id
                    join product.questions question
                      on question.question_id = binding.question_id
                     and question.status in ('active', 'canonical')
                    left join product.question_assets asset
                      on asset.asset_id = binding.runtime_asset_id
                     and asset.question_id = binding.question_id
                    where binding.question_id = :question_ref
                    order by version.evidence_ref
                    """
                ),
                {"question_ref": question_ref},
            ).mappings()
        )
        analysis_asset_rows = ()
        analysis_table = session.execute(
            text(
                "select to_regclass(" 
                "'product.question_analysis_visual_evidence_bindings')"
            )
        ).scalar_one_or_none()
        if analysis_table is not None:
            analysis_asset_rows = tuple(session.execute(
                text(
                    """
                    select binding.question_id, binding.content_version_id,
                           binding.source_asset_sha256, version.evidence_ref,
                           version.chart_type, version.payload,
                           'analysis'::text as part_kind
                    from product.question_analysis_visual_evidence_bindings binding
                    join product.structured_visual_evidence_heads evidence_head
                      on evidence_head.evidence_ref = binding.evidence_ref
                     and evidence_head.evidence_version_id = binding.evidence_version_id
                    join product.structured_visual_evidence_versions version
                      on version.evidence_version_id = binding.evidence_version_id
                    join product.question_content_heads content_head
                      on content_head.question_id = binding.question_id
                     and content_head.content_version_id = binding.content_version_id
                    join product.question_content_versions content_version
                      on content_version.question_id = content_head.question_id
                     and content_version.content_version_id = content_head.content_version_id
                    join product.questions question
                      on question.question_id = binding.question_id
                     and question.status in ('active', 'canonical')
                    join product.question_explanations explanation
                      on explanation.question_id = binding.question_id
                     and explanation.analysis_source = binding.analysis_source
                    join product.question_occurrences occurrence
                      on occurrence.question_id = binding.question_id
                     and occurrence.source_occurrence_question_key =
                         binding.source_occurrence_question_key
                    where binding.question_id = :question_ref
                      and content_version.snapshot_payload ->> 'analysis_source' =
                          binding.analysis_source
                      and (
                        binding.runtime_object_key is null
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
                                ) = binding.runtime_object_key
                            and coalesce(
                                  asset ->> 'contentSha256',
                                  asset ->> 'content_sha256'
                                ) = binding.source_asset_sha256
                            and coalesce(
                                  asset ->> 'imageSrc',
                                  asset ->> 'image_src'
                                ) = binding.source_image_ref
                        )
                      )
                    order by version.evidence_ref
                    """
                ),
                {"question_ref": question_ref},
            ).mappings())
        rows_by_identity = {
            (
                str(row["evidence_ref"]),
                str(row["source_asset_sha256"]),
                str(row["content_version_id"]),
            ): row
            for row in (*question_asset_rows, *analysis_asset_rows)
        }
        rows = tuple(
            rows_by_identity[key]
            for key in sorted(rows_by_identity)
        )
        if not rows:
            head = session.execute(
                text(
                    """
                    select content_head.content_version_id
                    from product.question_content_heads content_head
                    join product.questions question
                      on question.question_id = content_head.question_id
                     and question.status in ('active', 'canonical')
                    where content_head.question_id = :question_ref
                    """
                ),
                {"question_ref": question_ref},
            ).scalar_one_or_none()
            if head is None:
                return None
            return TeachingQuestionVisualBinding(
                questionRef=question_ref,
                contentVersionId=str(head),
                visualRequired=visual_required,
                sourceAssetSha256s=(),
                evidence=(),
            )
        content_version_id = str(rows[0]["content_version_id"])
        evidence = tuple(
            StructuredVisualEvidence(
                evidenceRef=str(row["evidence_ref"]),
                questionRef=question_ref,
                contentVersionId=content_version_id,
                sourceAssetSha256=str(row["source_asset_sha256"]),
                reviewState=str(row["payload"].get("reviewState") or "verified"),
                chartType=str(row["chart_type"]),
                title=_fact_value(row["payload"], "title"),
                unit=_fact_value(row["payload"], "unit"),
                facts=tuple(row["payload"]["facts"]),
                evidenceKind=str(
                    row["payload"].get("evidenceKind") or row["chart_type"]
                ),
                audiences=_runtime_audiences(row["part_kind"], row["payload"]),
                sourceRoles=tuple(row["payload"].get("sourceRoles") or ()),
            )
            for row in rows
        )
        return TeachingQuestionVisualBinding(
            questionRef=question_ref,
            contentVersionId=content_version_id,
            visualRequired=visual_required,
            sourceAssetSha256s=tuple(
                dict.fromkeys(str(row["source_asset_sha256"]) for row in rows)
            ),
            evidence=evidence,
        )


def _fact_value(payload: dict, kind: str) -> str:
    for fact in payload.get("facts", ()):
        if fact.get("kind") == kind:
            return str(fact.get("value") or "")
    return ""


def _runtime_audiences(part_kind: object, payload: dict) -> tuple[str, ...]:
    from app.domains.teaching_packages.publication.media_projector import (
        audiences_for_part_kind,
    )

    runtime = audiences_for_part_kind(str(part_kind or ""))
    if runtime:
        return runtime
    # Data-analysis v1 rows predate explicit audience metadata. They were all
    # published from question material assets and keep that safe compatibility.
    if "evidenceKind" not in payload:
        return ("student", "verifier")
    return tuple(payload.get("audiences") or ("verifier",))
