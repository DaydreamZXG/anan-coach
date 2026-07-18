"""Add versioned mock papers, external score reports and report projections.

Revision ID: 20260716_33
Revises: 20260716_32
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import Connection


revision: str = "20260716_33"
down_revision: str | Sequence[str] | None = "20260716_32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)
        if statement is _SESSION_VERSION_COLUMNS:
            _freeze_legacy_assigned_sessions(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    if _has_stage6_facts(bind):
        raise RuntimeError(
            "Stage 6 mock/report facts exist; destructive downgrade is forbidden. "
            "Repair the schema with a forward migration."
        )
    op.execute("drop table if exists learning.report_projection_versions")
    op.execute("drop table if exists learning.external_mock_report_commands")
    op.execute("drop table if exists learning.external_mock_report_versions")
    op.execute("drop table if exists learner.mock_exam_trajectory_events")
    op.drop_column("mock_exam_sessions", "scoring_policy_version_id", schema="learner")
    op.drop_column("mock_exam_sessions", "paper_version_id", schema="learner")
    op.execute("drop table if exists product.mock_exam_paper_versions")
    op.execute("drop table if exists product.mock_exam_scoring_policy_versions")


def _has_stage6_facts(bind: Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "select exists (select 1 from learning.external_mock_report_versions) "
                "or exists (select 1 from learning.report_projection_versions) "
                "or exists (select 1 from learner.mock_exam_trajectory_events) "
                "or exists (select 1 from learner.mock_exam_sessions)"
            )
        ).scalar_one()
    )


def _freeze_legacy_assigned_sessions(bind: Connection) -> None:
    import json

    from sqlalchemy.orm import Session

    from app.domains.mock_exam.repository import MockExamRepository
    from app.domains.task_content.service import (
        TaskContentContractError,
        freeze_learner_bundle,
    )
    from app.domains.xingce_learning.content_version import (
        question_content_version_id,
    )

    session = Session(bind=bind)
    repository = MockExamRepository(session)
    assigned = bind.execute(
        sa.text(
            """
            select session_id::text, question_ids, checkpoint
            from learner.mock_exam_sessions
            where status = 'assigned'
            order by created_at, session_id
            """
        )
    ).mappings()
    for row in assigned:
        session_id = str(row["session_id"])
        question_ids = [str(value) for value in (row["question_ids"] or [])]
        if not question_ids:
            raise RuntimeError(
                f"legacy assigned mock session {session_id} has no questions"
            )
        sources = repository.get_frozen_question_sources(question_ids=question_ids)
        if len(sources) != len(question_ids):
            raise RuntimeError(
                f"legacy assigned mock session {session_id} has incomplete question sources"
            )
        try:
            bundle, scoring_snapshot = freeze_learner_bundle(
                sources,
                scoring_policy_version="mock-balanced-performance-index-v1",
                content_version_id=question_content_version_id,
                published_explanation=lambda source: (None, None),
            )
        except TaskContentContractError as exc:
            raise RuntimeError(
                f"legacy assigned mock session {session_id} cannot be frozen: "
                f"{exc.code}: {exc.message}"
            ) from exc
        checkpoint = dict(row["checkpoint"] or {})
        checkpoint["currentQuestionId"] = (
            checkpoint.get("currentQuestionId") or question_ids[0]
        )
        checkpoint.setdefault("answers", [])
        bind.execute(
            sa.text(
                """
                update learner.mock_exam_sessions
                set learner_bundle = cast(:learner_bundle as jsonb),
                    scoring_snapshot = cast(:scoring_snapshot as jsonb),
                    checkpoint = cast(:checkpoint as jsonb),
                    checkpoint_updated_at = greatest(checkpoint_updated_at, updated_at)
                where session_id = cast(:session_id as uuid)
                  and status = 'assigned'
                """
            ),
            {
                "session_id": session_id,
                "learner_bundle": json.dumps(
                    bundle.model_dump(by_alias=True, mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "scoring_snapshot": json.dumps(
                    scoring_snapshot, ensure_ascii=False, sort_keys=True
                ),
                "checkpoint": json.dumps(
                    checkpoint, ensure_ascii=False, sort_keys=True
                ),
            },
        )


_SESSION_VERSION_COLUMNS = """
alter table learner.mock_exam_sessions
  add column paper_version_id uuid,
  add column scoring_policy_version_id text
"""


_STATEMENTS = (
    """
    create table product.mock_exam_scoring_policy_versions (
      scoring_policy_version_id text primary key,
      state text not null check (state in ('published', 'retired')),
      metric_name text not null,
      metric_unit text not null,
      definition text not null,
      module_weights jsonb not null check (jsonb_typeof(module_weights) = 'object'),
      interval_method text not null,
      confidence_level numeric(5,4),
      calibration_status text not null check (
        calibration_status in ('structural_index', 'sample_calibrated')
      ),
      calibration_sample_size integer not null check (calibration_sample_size >= 0),
      evidence_ref text not null,
      created_at timestamptz not null default now(),
      check (
        (calibration_status = 'structural_index' and calibration_sample_size = 0)
        or (calibration_status = 'sample_calibrated' and calibration_sample_size > 0)
      )
    )
    """,
    """
    insert into product.mock_exam_scoring_policy_versions (
      scoring_policy_version_id, state, metric_name, metric_unit, definition,
      module_weights, interval_method, confidence_level,
      calibration_status, calibration_sample_size, evidence_ref
    ) values
    (
      'mock-objective-exact-v1', 'retired', 'legacy_correct_rate', 'percent',
      '历史兼容策略；仅保留追溯，不得作为正式成绩标尺。',
      '{}', 'none', null, 'structural_index', 0,
      'migration:20260716_33:legacy'
    ),
    (
      'mock-balanced-performance-index-v1', 'published',
      'balanced_performance_index', 'index_points',
      '五个行测模块各占 20 点，模块内按已发布标准卷题数等权计算。'
      || '该指数只比较自身表现，不是国考原始分或全国分位。',
      jsonb_build_object(
        '常识判断', 0.2, '言语理解与表达', 0.2,
        '数量关系', 0.2, '判断推理', 0.2, '资料分析', 0.2
      ),
      'wilson_module_balanced_95', 0.95, 'structural_index', 0,
      'product-policy:mock-balanced-performance-index-v1'
    )
    """,
    """
    create table product.mock_exam_paper_versions (
      paper_version_id uuid primary key,
      paper_key text not null,
      version_no integer not null check (version_no >= 1),
      state text not null check (state in ('published', 'retired')),
      exam_scope text not null,
      track text not null check (track = 'xingce'),
      title text not null,
      duration_minutes integer not null check (duration_minutes > 0),
      question_ids jsonb not null check (jsonb_typeof(question_ids) = 'array'),
      blueprint jsonb not null check (jsonb_typeof(blueprint) = 'array'),
      learner_bundle_version_id text not null,
      scoring_policy_version_id text not null references
        product.mock_exam_scoring_policy_versions(scoring_policy_version_id),
      exposure_policy jsonb not null check (jsonb_typeof(exposure_policy) = 'object'),
      equivalence_metadata jsonb not null check (jsonb_typeof(equivalence_metadata) = 'object'),
      content_fingerprint text not null check (content_fingerprint ~ '^[0-9a-f]{64}$'),
      published_at timestamptz not null,
      created_at timestamptz not null default now(),
      unique (paper_key, version_no),
      unique (content_fingerprint, scoring_policy_version_id)
    )
    """,
    """
    create trigger mock_exam_scoring_policy_append_only
      before update or delete on product.mock_exam_scoring_policy_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    create trigger mock_exam_paper_versions_append_only
      before update or delete on product.mock_exam_paper_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    _SESSION_VERSION_COLUMNS,
    """
    insert into product.mock_exam_paper_versions (
      paper_version_id, paper_key, version_no, state, exam_scope, track, title,
      duration_minutes, question_ids, blueprint, learner_bundle_version_id,
      scoring_policy_version_id, exposure_policy, equivalence_metadata,
      content_fingerprint, published_at, created_at
    )
    select
      gen_random_uuid(), 'legacy-session:' || session_id::text, 1,
      case when status = 'assigned' then 'published' else 'retired' end,
      exam_scope, track,
      case when status = 'assigned' then '历史待完成行测模考卷' else '历史行测模考卷' end,
      greatest(1, extract(epoch from (deadline_at - started_at))::integer / 60),
      question_ids,
      case
        when jsonb_typeof(blueprint) = 'array' then blueprint
        when jsonb_typeof(blueprint) = 'object'
          and jsonb_typeof(blueprint->'modules') = 'object'
        then coalesce(
          (
            select jsonb_agg(
              jsonb_build_object(
                'moduleName', module.key,
                'questionCount', module.value
              )
              order by module.key
            )
            from jsonb_each(blueprint->'modules') module
          ),
          '[]'::jsonb
        )
        else '[]'::jsonb
      end,
      coalesce(nullif(learner_bundle->>'bundleVersionId', ''), 'legacy:' || session_id::text),
      case
        when status = 'assigned' then 'mock-balanced-performance-index-v1'
        else 'mock-objective-exact-v1'
      end,
      jsonb_build_object('lookbackDays', 60, 'legacy', true),
      case
        when status = 'assigned' then
          jsonb_build_object('status', 'structural_index', 'reason', 'legacy_assigned_session')
        else
          jsonb_build_object('status', 'not_assessed', 'reason', 'legacy_session')
      end,
      md5(session_id::text || '-paper') || md5(session_id::text || '-version'),
      created_at, created_at
    from learner.mock_exam_sessions
    """,
    """
    update learner.mock_exam_sessions session
    set paper_version_id = paper.paper_version_id,
        scoring_policy_version_id = paper.scoring_policy_version_id
    from product.mock_exam_paper_versions paper
    where paper.paper_key = 'legacy-session:' || session.session_id::text
    """,
    """
    alter table learner.mock_exam_sessions
      alter column paper_version_id set not null,
      alter column scoring_policy_version_id set not null,
      add constraint mock_exam_session_paper_version_fk
        foreign key (paper_version_id) references product.mock_exam_paper_versions(paper_version_id),
      add constraint mock_exam_session_scoring_policy_fk
        foreign key (scoring_policy_version_id) references
          product.mock_exam_scoring_policy_versions(scoring_policy_version_id)
    """,
    """
    create table learner.mock_exam_trajectory_events (
      trajectory_event_id uuid primary key,
      command_id text not null unique,
      user_id uuid not null references learner.users(user_id),
      session_id uuid not null references learner.mock_exam_sessions(session_id),
      event_seq bigint not null check (event_seq >= 1),
      event_type text not null check (event_type in (
        'visited', 'answer_changed', 'mark_changed', 'skipped',
        'left', 'resumed', 'submitted'
      )),
      question_id text,
      client_occurred_at timestamptz not null,
      session_revision bigint not null check (session_revision >= 1),
      payload jsonb not null check (jsonb_typeof(payload) = 'object'),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      created_at timestamptz not null default now(),
      unique (session_id, event_seq)
    )
    """,
    """
    create index learner_mock_trajectory_owner_session_idx
      on learner.mock_exam_trajectory_events(user_id, session_id, event_seq)
    """,
    """
    create trigger mock_exam_trajectory_events_append_only
      before update or delete on learner.mock_exam_trajectory_events
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    create table learning.external_mock_report_versions (
      report_version_id uuid primary key,
      report_id uuid not null,
      command_id text not null unique,
      user_id uuid not null references learner.users(user_id),
      version_no integer not null check (version_no >= 1),
      state text not null check (state in ('candidate', 'confirmed', 'rejected')),
      artifact_version_id uuid not null,
      recognition_revision_id uuid not null,
      platform_name text not null,
      exam_name text not null,
      occurred_at timestamptz not null,
      timed_status text not null check (timed_status in ('timed', 'untimed', 'unknown')),
      duration_minutes integer check (duration_minutes is null or duration_minutes > 0),
      total_score numeric(6,2) check (total_score is null or total_score between 0 and 200),
      xingce_score numeric(5,2) check (xingce_score is null or xingce_score between 0 and 100),
      shenlun_score numeric(5,2) check (shenlun_score is null or shenlun_score between 0 and 100),
      module_scores jsonb not null check (jsonb_typeof(module_scores) = 'object'),
      evidence_grade text not null check (evidence_grade in (
        'candidate_unconfirmed', 'ability_eligible',
        'trend_only_untimed', 'trend_only_incomplete', 'trend_only_stale'
      )),
      score_evidence_id uuid references learning.score_evidence(evidence_id),
      estimate_version_id uuid references learning.ability_estimate_versions(estimate_version_id),
      input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
      supersedes_report_version_id uuid references
        learning.external_mock_report_versions(report_version_id),
      confirmed_at timestamptz,
      created_at timestamptz not null default now(),
      unique (report_id, version_no),
      check (
        (state = 'candidate' and version_no >= 1 and evidence_grade = 'candidate_unconfirmed'
          and score_evidence_id is null and estimate_version_id is null and confirmed_at is null)
        or (state = 'confirmed' and version_no >= 2 and evidence_grade <> 'candidate_unconfirmed'
          and confirmed_at is not null)
        or (state = 'rejected' and score_evidence_id is null
          and estimate_version_id is null and confirmed_at is null)
      ),
      check (
        evidence_grade <> 'ability_eligible'
        or (timed_status = 'timed' and (xingce_score is not null or shenlun_score is not null)
          and score_evidence_id is not null and estimate_version_id is not null)
      ),
      foreign key (recognition_revision_id, user_id, artifact_version_id)
        references learning.recognition_revisions(
          recognition_revision_id, owner_user_id, artifact_version_id
        )
    )
    """,
    """
    create unique index learning_external_mock_one_successor_idx
      on learning.external_mock_report_versions(supersedes_report_version_id)
      where supersedes_report_version_id is not null
    """,
    """
    create unique index learning_external_mock_confirmed_artifact_idx
      on learning.external_mock_report_versions(user_id, artifact_version_id)
      where state = 'confirmed'
    """,
    """
    create index learning_external_mock_owner_time_idx
      on learning.external_mock_report_versions(user_id, occurred_at desc, version_no desc)
    """,
    """
    create trigger external_mock_report_versions_append_only
      before update or delete on learning.external_mock_report_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    create table learning.external_mock_report_commands (
      user_id uuid not null references learner.users(user_id),
      idempotency_key text not null,
      command_type text not null check (command_type in ('draft', 'confirm', 'reject')),
      request_fingerprint text not null check (request_fingerprint ~ '^[0-9a-f]{64}$'),
      report_version_id uuid not null references learning.external_mock_report_versions(report_version_id),
      response_payload jsonb not null check (jsonb_typeof(response_payload) = 'object'),
      created_at timestamptz not null default now(),
      primary key (user_id, idempotency_key)
    )
    """,
    """
    create trigger external_mock_report_commands_append_only
      before update or delete on learning.external_mock_report_commands
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
    """
    create table learning.report_projection_versions (
      projection_version_id uuid primary key,
      user_id uuid not null references learner.users(user_id),
      report_kind text not null check (report_kind in (
        'single_set', 'weekly', 'mock', 'position_gap', 'global'
      )),
      subject_ref text not null,
      chart_contract_version text not null,
      input_fingerprint text not null check (input_fingerprint ~ '^[0-9a-f]{64}$'),
      payload jsonb not null check (jsonb_typeof(payload) = 'object'),
      evidence_refs jsonb not null check (jsonb_typeof(evidence_refs) = 'array'),
      next_plan_evidence_refs jsonb not null check (
        jsonb_typeof(next_plan_evidence_refs) = 'array'
      ),
      generated_at timestamptz not null,
      unique (user_id, report_kind, subject_ref, input_fingerprint)
    )
    """,
    """
    create index learning_report_projection_owner_idx
      on learning.report_projection_versions(user_id, report_kind, generated_at desc)
    """,
    """
    create trigger report_projection_versions_append_only
      before update or delete on learning.report_projection_versions
      for each row execute function learning.reject_xingce_fact_mutation()
    """,
)
