"""Freeze learning-loop selection and scratch evidence contracts.

Revision ID: 20260809_103
Revises: 20260808_102

Historical plan tasks, attempt records and MyScript formula versions are left
untouched.  The nullable columns are populated only by the new application
contract; absence remains the explicit legacy discriminator.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


revision: str = "20260809_103"
down_revision: str | Sequence[str] | None = "20260808_102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table learning.daily_plan_tasks "
        "add column selection_intent jsonb"
    )
    op.execute(
        """
        alter table learning.daily_plan_tasks
          add constraint daily_plan_tasks_selection_intent_v1_check check (
            selection_intent is null
            or (
              track = 'xingce'
              and stage = 'objective_practice'
              and kind = 'practice'
              and jsonb_typeof(selection_intent) = 'object'
              and selection_intent - array[
                    'contractVersion', 'intentId', 'knowledgeRelease',
                    'targetModule', 'targetKnowledgeNode', 'targetMicroSkillNode',
                    'confirmedCauseFocus', 'difficultyPolicy', 'noveltyPolicy',
                    'fallbackReason', 'evidenceRefs'
                  ] = '{}'::jsonb
              and selection_intent ?& array[
                    'contractVersion', 'intentId', 'knowledgeRelease',
                    'targetModule', 'targetKnowledgeNode', 'targetMicroSkillNode',
                    'confirmedCauseFocus', 'difficultyPolicy', 'noveltyPolicy',
                    'fallbackReason', 'evidenceRefs'
                  ]
              and selection_intent ->> 'contractVersion' =
                    'xingce-selection-intent@v1'
              and selection_intent ->> 'intentId' ~ '^[0-9a-f]{64}$'
              and char_length(trim(selection_intent ->> 'targetModule')) > 0
              and jsonb_typeof(selection_intent -> 'knowledgeRelease') = 'object'
              and (selection_intent -> 'knowledgeRelease') - array[
                    'releaseId', 'releaseVersion', 'sourceDataVersion',
                    'vocabVersion', 'freshUntil'
                  ] = '{}'::jsonb
              and (selection_intent -> 'knowledgeRelease') ?& array[
                    'releaseId', 'releaseVersion', 'sourceDataVersion',
                    'vocabVersion', 'freshUntil'
                  ]
              and char_length(selection_intent #>> '{knowledgeRelease,releaseId}') > 0
              and char_length(selection_intent #>> '{knowledgeRelease,releaseVersion}') > 0
              and char_length(selection_intent #>> '{knowledgeRelease,sourceDataVersion}') > 0
              and char_length(selection_intent #>> '{knowledgeRelease,vocabVersion}') > 0
              and char_length(selection_intent #>> '{knowledgeRelease,freshUntil}') > 0
              and (
                selection_intent -> 'targetKnowledgeNode' = 'null'::jsonb
                or (
                  jsonb_typeof(selection_intent -> 'targetKnowledgeNode') = 'object'
                  and (selection_intent -> 'targetKnowledgeNode') - array[
                        'releaseId', 'nodeId', 'nodeType', 'tagType', 'tagValue'
                      ] = '{}'::jsonb
                  and (selection_intent -> 'targetKnowledgeNode') ?& array[
                        'releaseId', 'nodeId', 'nodeType', 'tagType', 'tagValue'
                      ]
                  and selection_intent #>> '{targetKnowledgeNode,nodeType}' =
                        'knowledge_point'
                  and selection_intent #>> '{targetKnowledgeNode,releaseId}' =
                        selection_intent #>> '{knowledgeRelease,releaseId}'
                  and char_length(selection_intent #>> '{targetKnowledgeNode,nodeId}') > 0
                  and char_length(selection_intent #>> '{targetKnowledgeNode,tagType}') > 0
                  and char_length(selection_intent #>> '{targetKnowledgeNode,tagValue}') > 0
                )
              )
              and (
                selection_intent -> 'targetMicroSkillNode' = 'null'::jsonb
                or (
                  jsonb_typeof(selection_intent -> 'targetMicroSkillNode') = 'object'
                  and (selection_intent -> 'targetMicroSkillNode') - array[
                        'releaseId', 'nodeId', 'nodeType', 'tagType', 'tagValue'
                      ] = '{}'::jsonb
                  and (selection_intent -> 'targetMicroSkillNode') ?& array[
                        'releaseId', 'nodeId', 'nodeType', 'tagType', 'tagValue'
                      ]
                  and selection_intent #>> '{targetMicroSkillNode,nodeType}' =
                        'micro_skill'
                  and selection_intent #>> '{targetMicroSkillNode,releaseId}' =
                        selection_intent #>> '{knowledgeRelease,releaseId}'
                  and char_length(selection_intent #>> '{targetMicroSkillNode,nodeId}') > 0
                  and char_length(selection_intent #>> '{targetMicroSkillNode,tagType}') > 0
                  and char_length(selection_intent #>> '{targetMicroSkillNode,tagValue}') > 0
                )
              )
              and (
                selection_intent -> 'confirmedCauseFocus' = 'null'::jsonb
                or (
                  jsonb_typeof(selection_intent -> 'confirmedCauseFocus') = 'object'
                  and (selection_intent -> 'confirmedCauseFocus') - array[
                        'causeCode', 'focus', 'questionType',
                        'confirmedCauseVersionId', 'decisionVersionId',
                        'candidateVersionId'
                      ] = '{}'::jsonb
                  and (selection_intent -> 'confirmedCauseFocus') ?& array[
                        'causeCode', 'focus', 'questionType',
                        'confirmedCauseVersionId', 'decisionVersionId',
                        'candidateVersionId'
                      ]
                  and selection_intent #>> '{confirmedCauseFocus,causeCode}' in (
                        'stem_misread', 'knowledge_gap', 'method_selection',
                        'calculation_error', 'time_pressure', 'guessing',
                        'active_abandon', 'other'
                      )
                  and selection_intent #>> '{confirmedCauseFocus,focus}' in (
                        'stem_reading', 'knowledge_base', 'method_application',
                        'calculation', 'time_bounded', 'careful_selection',
                        'core_drilling', 'balanced_drilling'
                      )
                  and (
                    selection_intent #> '{confirmedCauseFocus,questionType}' =
                          'null'::jsonb
                    or char_length(
                      selection_intent #>> '{confirmedCauseFocus,questionType}'
                    ) > 0
                  )
                  and char_length(selection_intent #>>
                        '{confirmedCauseFocus,confirmedCauseVersionId}') > 0
                  and char_length(selection_intent #>>
                        '{confirmedCauseFocus,decisionVersionId}') > 0
                  and char_length(selection_intent #>>
                        '{confirmedCauseFocus,candidateVersionId}') > 0
                )
              )
              and jsonb_typeof(selection_intent -> 'evidenceRefs') = 'array'
              and jsonb_array_length(selection_intent -> 'evidenceRefs') > 0
              and jsonb_typeof(selection_intent -> 'difficultyPolicy') = 'object'
              and jsonb_typeof(selection_intent -> 'noveltyPolicy') = 'object'
              and selection_intent -> 'noveltyPolicy' =
                    '{"answered":"exclude","exposed":"exclude","scope":"current_user"}'::jsonb
              and (
                (
                  selection_intent #>> '{difficultyPolicy,state}' = 'known'
                  and (selection_intent -> 'difficultyPolicy') - array[
                        'state', 'targetTheta', 'sourceVersionId',
                        'algorithmVersion'
                      ] = '{}'::jsonb
                  and (selection_intent -> 'difficultyPolicy') ?& array[
                        'state', 'targetTheta', 'sourceVersionId',
                        'algorithmVersion'
                      ]
                  and jsonb_typeof(selection_intent #>
                        '{difficultyPolicy,targetTheta}') = 'number'
                  and char_length(selection_intent #>>
                        '{difficultyPolicy,sourceVersionId}') > 0
                )
                or (
                  selection_intent #>> '{difficultyPolicy,state}' = 'unknown'
                  and (selection_intent -> 'difficultyPolicy') - array[
                        'state', 'reason', 'sourceVersionId', 'algorithmVersion'
                      ] = '{}'::jsonb
                  and (selection_intent -> 'difficultyPolicy') ?& array[
                        'state', 'reason', 'sourceVersionId', 'algorithmVersion'
                      ]
                  and selection_intent #>> '{difficultyPolicy,reason}' in (
                        'missing_shadow_estimate', 'insufficient_shadow_sample'
                      )
                  and (
                    selection_intent #> '{difficultyPolicy,sourceVersionId}' =
                          'null'::jsonb
                    or char_length(selection_intent #>>
                          '{difficultyPolicy,sourceVersionId}') > 0
                  )
                )
              )
              and selection_intent #>> '{difficultyPolicy,algorithmVersion}' =
                    'ability-estimate-v2-shadow-1'
              and (
                selection_intent -> 'fallbackReason' = 'null'::jsonb
                or selection_intent ->> 'fallbackReason' in (
                  'cold_start_no_grounded_evidence',
                  'insufficient_knowledge_point_evidence',
                  'knowledge_node_unavailable'
                )
              )
            )
          )
        """
    )
    op.execute(
        "alter table learning.xingce_attempt_records "
        "add column scratch_evidence_projection jsonb"
    )
    op.execute(
        "alter table learning.xingce_attempt_records "
        "add column scratch_evidence_fingerprint text"
    )
    op.execute(
        """
        alter table learning.xingce_attempt_records
          add constraint xingce_attempt_scratch_projection_v1_check check (
            (scratch_evidence_projection is null) =
              (scratch_evidence_fingerprint is null)
            and (
              scratch_evidence_projection is null
              or (
                jsonb_typeof(scratch_evidence_projection) = 'object'
                and scratch_evidence_projection ->> 'contractVersion' =
                      'scratch-evidence-projection@v1'
                and scratch_evidence_fingerprint ~ '^[0-9a-f]{64}$'
              )
            )
          )
        """
    )
    op.execute(
        "alter table learning.math_ink_commands "
        "add column recognition_metadata jsonb"
    )
    op.execute(
        """
        alter table learning.math_ink_commands
          add constraint math_ink_recognition_metadata_v1_check check (
            recognition_metadata is null
            or (
              jsonb_typeof(recognition_metadata) = 'object'
              and recognition_metadata - array[
                    'contractVersion', 'provider', 'candidateSource',
                    'providerRelease', 'modelRelease', 'priceVersion',
                    'unitPriceCny', 'deploymentId', 'rendererVersion',
                    'confidence', 'providerRequestIdFingerprint',
                    'vectorFingerprint', 'renderFingerprint'
                  ] = '{}'::jsonb
              and recognition_metadata ?& array[
                    'contractVersion', 'provider', 'candidateSource',
                    'providerRelease', 'modelRelease', 'priceVersion',
                    'unitPriceCny', 'deploymentId', 'rendererVersion',
                    'confidence', 'providerRequestIdFingerprint',
                    'vectorFingerprint', 'renderFingerprint'
                  ]
              and recognition_metadata ->> 'contractVersion' =
                    'formula-recognition-metadata@v1'
              and (
                (
                  recognition_metadata ->> 'provider' = 'MyScript'
                  and recognition_metadata ->> 'candidateSource' =
                        'myscript_candidate'
                )
                or (
                  recognition_metadata ->> 'provider' = 'Xfyun'
                  and recognition_metadata ->> 'candidateSource' =
                        'xfyun_candidate'
                )
              )
              and char_length(recognition_metadata ->> 'providerRelease') > 0
              and char_length(recognition_metadata ->> 'modelRelease') > 0
              and char_length(recognition_metadata ->> 'priceVersion') > 0
              and recognition_metadata ->> 'unitPriceCny' ~
                    '^[0-9]+([.][0-9]+)?$'
              and (
                recognition_metadata -> 'deploymentId' = 'null'::jsonb
                or char_length(recognition_metadata ->> 'deploymentId') > 0
              )
              and recognition_metadata ->> 'rendererVersion' =
                    'math-ink-renderer@v1'
              and recognition_metadata ->> 'vectorFingerprint' ~
                    '^[0-9a-f]{64}$'
              and recognition_metadata ->> 'renderFingerprint' ~
                    '^[0-9a-f]{64}$'
              and (
                recognition_metadata -> 'providerRequestIdFingerprint' = 'null'::jsonb
                or recognition_metadata ->> 'providerRequestIdFingerprint' ~
                     '^[0-9a-f]{64}$'
              )
              and (
                recognition_metadata -> 'confidence' = 'null'::jsonb
                or (
                  jsonb_typeof(recognition_metadata -> 'confidence') = 'number'
                  and cast(recognition_metadata ->> 'confidence' as numeric)
                        between 0 and 1
                )
              )
            )
          )
        """
    )
    _replace_formula_source_constraints(
        op.get_bind(),
        candidate_sources=("myscript_candidate", "xfyun_candidate"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_new_facts(bind):
        raise RuntimeError(
            "Selection intents, scratch projections or Xfyun formula facts exist; "
            "destructive downgrade is forbidden. Repair with a forward migration."
        )
    _replace_formula_source_constraints(
        bind,
        candidate_sources=("myscript_candidate",),
    )
    op.drop_constraint(
        "math_ink_recognition_metadata_v1_check",
        "math_ink_commands",
        schema="learning",
        type_="check",
    )
    op.drop_column("math_ink_commands", "recognition_metadata", schema="learning")
    op.drop_constraint(
        "xingce_attempt_scratch_projection_v1_check",
        "xingce_attempt_records",
        schema="learning",
        type_="check",
    )
    op.drop_column(
        "xingce_attempt_records",
        "scratch_evidence_fingerprint",
        schema="learning",
    )
    op.drop_column(
        "xingce_attempt_records",
        "scratch_evidence_projection",
        schema="learning",
    )
    op.drop_constraint(
        "daily_plan_tasks_selection_intent_v1_check",
        "daily_plan_tasks",
        schema="learning",
        type_="check",
    )
    op.drop_column("daily_plan_tasks", "selection_intent", schema="learning")


def _replace_formula_source_constraints(
    bind: Connection,
    *,
    candidate_sources: tuple[str, ...],
) -> None:
    # Revision 44 created unnamed CHECK constraints.  Resolve only the two
    # constraints whose definitions mention `source`; never guess generated
    # constraint names.
    bind.execute(
        text(
            """
            do $$
            declare item record;
            begin
              for item in
                select conname
                from pg_constraint
                where conrelid = 'learning.math_formula_versions'::regclass
                  and contype = 'c'
                  and pg_get_constraintdef(oid) like '%source%'
              loop
                execute format(
                  'alter table learning.math_formula_versions drop constraint %I',
                  item.conname
                );
              end loop;
            end
            $$
            """
        )
    )
    quoted = ", ".join(f"'{value}'" for value in candidate_sources)
    bind.exec_driver_sql(
        "alter table learning.math_formula_versions "
        "add constraint math_formula_versions_source_v103_check "
        f"check (source in ({quoted}, 'user_confirmed'))"
    )
    bind.exec_driver_sql(
        "alter table learning.math_formula_versions "
        "add constraint math_formula_versions_chain_v103_check check ("
        f"(version_no = 1 and state = 'candidate' and source in ({quoted})) "
        "or (version_no > 1 and source = 'user_confirmed'))"
    )


def _has_new_facts(bind: Connection) -> bool:
    return bool(
        bind.execute(
            text(
                """
                select exists (
                  select 1 from learning.daily_plan_tasks
                  where selection_intent is not null
                  union all
                  select 1 from learning.xingce_attempt_records
                  where scratch_evidence_projection is not null
                  union all
                  select 1 from learning.math_formula_versions
                  where source = 'xfyun_candidate'
                  union all
                  select 1 from learning.math_ink_commands
                  where recognition_metadata is not null
                )
                """
            )
        ).scalar()
    )
