from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session


REVISION_33 = "20260716_33"
REVISION_33_PREDECESSOR = "20260716_32"


class Revision33MigrationContextError(RuntimeError):
    pass


def assert_revision_33_migration_context(*, session: Session) -> None:
    """Allow the legacy query only while revision 33 is executing.

    Alembic retains the predecessor in ``alembic_version`` until the revision
    body completes. Revision 33 calls the repository immediately after its two
    session-version columns and prerequisite tables have been created.
    """

    in_revision = bool(
        session.execute(
            text(
                """
                select
                  (
                    select count(*) = 1
                       and min(version_num) = :predecessor
                    from alembic_version
                  )
                  and to_regclass(
                    'product.mock_exam_scoring_policy_versions'
                  ) is not null
                  and to_regclass(
                    'product.mock_exam_paper_versions'
                  ) is not null
                  and exists (
                    select 1
                    from information_schema.columns
                    where table_schema = 'learner'
                      and table_name = 'mock_exam_sessions'
                      and column_name = 'paper_version_id'
                  )
                  and exists (
                    select 1
                    from information_schema.columns
                    where table_schema = 'learner'
                      and table_name = 'mock_exam_sessions'
                      and column_name = 'scoring_policy_version_id'
                  )
                """
            ),
            {"predecessor": REVISION_33_PREDECESSOR},
        ).scalar_one()
    )
    if not in_revision:
        raise Revision33MigrationContextError(
            "revision-33 legacy question reads are unavailable outside its "
            "in-progress Alembic upgrade"
        )


def get_revision_33_frozen_question_sources(
    *,
    session: Session,
    question_ids: list[str],
) -> list[dict[str, object]]:
    """Execute the immutable source query published with revision 33.

    Revision 20260716_33 imports ``MockExamRepository`` and calls its historical
    one-argument interface. Keep this query isolated from current mock-exam
    selection, which requires an explicit frozen knowledge release.
    """

    rows = (
        session.execute(
            text(
                """
                with assigned as (
                  select value as question_id, ordinality
                  from jsonb_array_elements_text(cast(:question_ids as jsonb))
                    with ordinality
                )
                select
                  q.question_id, q.module_name, q.question_type,
                  q.stem, q.stem_html, q.answer,
                  q.material_text, q.material_html,
                  q.shared_context_required, q.visual_required,
                  e.preferred_analysis, e.analysis_source, e.analysis_status,
                  e.prefer_ai_analysis, e.analysis_needs_review,
                  assigned.ordinality
                from assigned
                join product.questions q on q.question_id = assigned.question_id
                left join product.question_explanations e
                  on e.question_id = q.question_id
                order by assigned.ordinality
                """
            ),
            {"question_ids": json.dumps(question_ids, ensure_ascii=False)},
        )
        .mappings()
        .all()
    )
    sources: list[dict[str, object]] = []
    for row in rows:
        source = dict(row)
        source.pop("ordinality", None)
        source["options"] = [
            dict(item)
            for item in session.execute(
                text(
                    """
                    select label, option_text, option_html
                    from product.question_options
                    where question_id = :question_id
                    order by option_index
                    """
                ),
                {"question_id": row["question_id"]},
            ).mappings()
        ]
        source["assets"] = [
            dict(item)
            for item in session.execute(
                text(
                    """
                    select asset_id, part_kind, part_index, image_ordinal,
                           image_src, object_key, is_representative_asset
                    from product.question_assets
                    where question_id = :question_id
                    order by part_kind nulls last, part_index nulls last,
                             image_ordinal nulls last, asset_id
                    """
                ),
                {"question_id": row["question_id"]},
            ).mappings()
        ]
        sources.append(source)
    return sources
