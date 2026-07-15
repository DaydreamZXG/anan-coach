"""Bind rubrics to materials and add privacy-safe AI cost accounting.

Revision ID: 20260713_13
Revises: 20260713_12
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "20260713_13"
down_revision: str | Sequence[str] | None = "20260713_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "essay_review_versions",
        sa.Column("score_confidence_reasons", JSONB, nullable=True),
        schema="learning",
    )
    op.add_column(
        "essay_review_versions",
        sa.Column("grounding_contract_version", sa.Text(), nullable=True),
        schema="learning",
    )
    op.add_column(
        "essay_review_versions",
        sa.Column("grounding_report", JSONB, nullable=True),
        schema="learning",
    )
    op.add_column(
        "essay_review_versions",
        sa.Column("execution_fingerprint", sa.Text(), nullable=True),
        schema="learning",
    )
    op.add_column(
        "essay_review_versions",
        sa.Column("execution_attempt_no", sa.Integer(), nullable=True),
        schema="learning",
    )
    op.drop_constraint(
        "essay_review_attempt_input_key",
        "essay_review_versions",
        schema="learning",
        type_="unique",
    )
    op.execute(
        "alter table learning.essay_review_versions add constraint "
        "essay_review_execution_fingerprint_check check "
        "(execution_fingerprint is not null and "
        "length(execution_fingerprint) = 64) not valid"
    )
    op.execute(
        "alter table learning.essay_review_versions add constraint "
        "essay_review_execution_attempt_check check "
        "(execution_attempt_no is not null and execution_attempt_no >= 1) "
        "not valid"
    )
    op.create_unique_constraint(
        "essay_review_attempt_execution_key",
        "essay_review_versions",
        ["attempt_id", "execution_fingerprint"],
        schema="learning",
    )
    op.create_unique_constraint(
        "essay_review_input_execution_attempt_key",
        "essay_review_versions",
        ["attempt_id", "input_fingerprint", "execution_attempt_no"],
        schema="learning",
    )
    op.execute(
        "alter table learning.essay_review_versions add constraint "
        "essay_review_score_confidence_reasons_check check ("
        "(status = 'completed' and "
        "score_confidence_reasons is not null and "
        "jsonb_typeof(score_confidence_reasons) = 'array' and "
        "jsonb_array_length(score_confidence_reasons) > 0) or "
        "(status <> 'completed' and score_confidence_reasons is null)) "
        "not valid"
    )
    op.execute(
        "alter table learning.essay_review_versions add constraint "
        "essay_review_grounding_report_check check ("
        "(status = 'completed' "
        "and grounding_contract_version is not null "
        "and grounding_contract_version = 'essay-grounding-v2' "
        "and grounding_report is not null "
        "and jsonb_typeof(grounding_report) = 'object' "
        "and grounding_report @> "
        "'{\"verified\": true, \"eligibleForLearning\": true}'::jsonb) or "
        "(status <> 'completed' and grounding_contract_version is null "
        "and grounding_report is null)) not valid"
    )
    op.add_column(
        "essay_rubric_versions",
        sa.Column("material_bundle_version_id", sa.Uuid(), nullable=True),
        schema="product",
    )
    op.add_column(
        "essay_rubric_versions",
        sa.Column("compilation_input_fingerprint", sa.Text(), nullable=True),
        schema="product",
    )
    op.add_column(
        "essay_rubric_versions",
        sa.Column("compilation_audit", JSONB, nullable=True),
        schema="product",
    )
    op.create_foreign_key(
        "essay_rubric_material_task_fk",
        "essay_rubric_versions",
        "essay_material_bundle_versions",
        ["material_bundle_version_id", "task_version_id"],
        ["material_bundle_version_id", "task_version_id"],
        source_schema="product",
        referent_schema="product",
    )
    op.create_unique_constraint(
        "essay_rubric_material_identity_key",
        "essay_rubric_versions",
        ["rubric_version_id", "task_version_id", "material_bundle_version_id"],
        schema="product",
    )
    op.execute(
        "alter table learning.essay_attempts add constraint "
        "essay_attempt_rubric_material_fk foreign key "
        "(rubric_version_id, task_version_id, material_bundle_version_id) "
        "references product.essay_rubric_versions "
        "(rubric_version_id, task_version_id, material_bundle_version_id) "
        "not valid"
    )
    op.execute(
        "alter table learning.essay_review_versions add constraint "
        "essay_review_rubric_material_fk foreign key "
        "(rubric_version_id, task_version_id, material_bundle_version_id) "
        "references product.essay_rubric_versions "
        "(rubric_version_id, task_version_id, material_bundle_version_id) "
        "not valid"
    )
    op.execute(
        "alter table product.essay_rubric_versions add constraint "
        "essay_rubric_material_binding_check check ("
        "source_kind <> 'system_compiled' or "
        "(material_bundle_version_id is not null and "
        "compilation_input_fingerprint is not null and "
        "length(compilation_input_fingerprint) = 64)) not valid"
    )
    op.execute(
        "alter table product.essay_rubric_versions add constraint "
        "essay_rubric_compilation_audit_check check ("
        "source_kind <> 'system_compiled' or "
        "(compilation_audit is not null "
        "and jsonb_typeof(compilation_audit) = 'object' "
        "and compilation_audit ?& array["
        "'contractVersion','compilerVersion','promptVersion','provider','model',"
        "'inputFingerprint','promptSha256','materialContentSha256'])) not valid"
    )
    op.create_index(
        "product_essay_rubric_compilation_input_idx",
        "essay_rubric_versions",
        ["task_version_id", "compilation_input_fingerprint"],
        schema="product",
        postgresql_where=sa.text("source_kind = 'system_compiled'"),
    )

    op.create_table(
        "model_call_ledger",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("call_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("price_version", sa.Text(), nullable=False),
        sa.Column("input_rate_cny_per_million", sa.Numeric(30, 12), nullable=False),
        sa.Column(
            "cached_input_rate_cny_per_million",
            sa.Numeric(30, 12),
            nullable=False,
        ),
        sa.Column("output_rate_cny_per_million", sa.Numeric(30, 12), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("output_fingerprint", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("input_cost_cny", sa.Numeric(14, 8), nullable=True),
        sa.Column("cached_input_cost_cny", sa.Numeric(14, 8), nullable=True),
        sa.Column("output_cost_cny", sa.Numeric(14, 8), nullable=True),
        sa.Column("total_cost_cny", sa.Numeric(14, 8), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'failed')",
            name="model_call_ledger_status_check",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="model_call_ledger_input_fingerprint_check",
        ),
        sa.CheckConstraint(
            "char_length(price_version) between 1 and 120",
            name="model_call_ledger_price_version_check",
        ),
        sa.CheckConstraint(
            "input_rate_cny_per_million > 0 and "
            "cached_input_rate_cny_per_million >= 0 and "
            "output_rate_cny_per_million > 0",
            name="model_call_ledger_price_snapshot_check",
        ),
        sa.CheckConstraint(
            "output_fingerprint is null or length(output_fingerprint) = 64",
            name="model_call_ledger_output_fingerprint_check",
        ),
        sa.CheckConstraint(
            "input_tokens is null or input_tokens >= 0",
            name="model_call_ledger_input_tokens_check",
        ),
        sa.CheckConstraint(
            "cached_input_tokens is null or cached_input_tokens >= 0",
            name="model_call_ledger_cached_tokens_check",
        ),
        sa.CheckConstraint(
            "output_tokens is null or output_tokens >= 0",
            name="model_call_ledger_output_tokens_check",
        ),
        sa.CheckConstraint(
            "latency_ms is null or latency_ms >= 0",
            name="model_call_ledger_latency_check",
        ),
        sa.CheckConstraint(
            "input_cost_cny is null or input_cost_cny >= 0",
            name="model_call_ledger_input_cost_check",
        ),
        sa.CheckConstraint(
            "cached_input_cost_cny is null or cached_input_cost_cny >= 0",
            name="model_call_ledger_cached_cost_check",
        ),
        sa.CheckConstraint(
            "output_cost_cny is null or output_cost_cny >= 0",
            name="model_call_ledger_output_cost_check",
        ),
        sa.CheckConstraint(
            "total_cost_cny is null or total_cost_cny >= 0",
            name="model_call_ledger_total_cost_check",
        ),
        sa.CheckConstraint(
            "total_cost_cny is null or abs(total_cost_cny - "
            "coalesce(input_cost_cny, 0) - coalesce(cached_input_cost_cny, 0) - "
            "coalesce(output_cost_cny, 0)) < 0.00000001",
            name="model_call_ledger_cost_conservation_check",
        ),
        sa.CheckConstraint(
            "(status = 'running' and completed_at is null) or "
            "(status <> 'running' and completed_at is not null)",
            name="model_call_ledger_terminal_time_check",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' or "
            "num_nonnulls(input_tokens, cached_input_tokens, output_tokens, "
            "input_cost_cny, cached_input_cost_cny, output_cost_cny, "
            "total_cost_cny, latency_ms, output_fingerprint) = 9",
            name="model_call_ledger_success_metrics_check",
        ),
        sa.CheckConstraint(
            "cached_input_tokens is null or input_tokens is null or "
            "cached_input_tokens <= input_tokens",
            name="model_call_ledger_cached_token_bound_check",
        ),
        sa.CheckConstraint(
            "(status = 'running' and num_nonnulls("
            "output_fingerprint, input_tokens, cached_input_tokens, output_tokens, "
            "input_cost_cny, cached_input_cost_cny, output_cost_cny, total_cost_cny, "
            "latency_ms, error_code, completed_at) = 0) or "
            "(status = 'succeeded' and error_code is null) or "
            "(status = 'failed' and "
            "coalesce(char_length(error_code), 0) > 0 "
            "and latency_ms is not null and output_fingerprint is null "
            "and num_nonnulls(input_tokens, cached_input_tokens, output_tokens, "
            "input_cost_cny, cached_input_cost_cny, output_cost_cny, "
            "total_cost_cny) = 0)",
            name="model_call_ledger_state_shape_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(["job_id"], ["platform.background_jobs.job_id"]),
        sa.PrimaryKeyConstraint("call_id"),
        sa.UniqueConstraint("call_key", name="model_call_ledger_call_key_key"),
        schema="ai_audit",
    )
    op.create_index(
        "ai_model_call_ledger_user_feature_idx",
        "model_call_ledger",
        ["user_id", "feature", "started_at"],
        schema="ai_audit",
    )
    op.create_index(
        "ai_model_call_ledger_job_idx",
        "model_call_ledger",
        ["job_id", "started_at"],
        schema="ai_audit",
    )
    op.execute(
        "create index platform_essay_review_job_review_version_idx "
        "on platform.background_jobs ((payload->>'reviewVersionId'), created_at) "
        "where job_type = 'essay_grounded_review_v2'"
    )
    op.execute(
        "create index platform_essay_review_job_user_answer_idx "
        "on platform.background_jobs ((payload->>'userId'), "
        "((payload->'request')->>'answerVersionId'), created_at) "
        "where job_type = 'essay_grounded_review_v2'"
    )
    op.execute(
        """
        create function ai_audit.reject_append_only_mutation()
        returns trigger language plpgsql as $$
        begin
          raise exception using
            errcode = '23514',
            message = tg_table_schema || '.' || tg_table_name ||
                      ' is append-only';
        end;
        $$
        """
    )
    op.execute(
        """
        create function ai_audit.enforce_model_call_ledger_transition()
        returns trigger language plpgsql as $$
        begin
          if tg_op = 'DELETE' then
            raise exception using
              errcode = '23514',
              message = 'model call ledger history cannot be deleted';
          end if;
          if old.status <> 'running' then
            raise exception using
              errcode = '23514',
              message = 'terminal model call ledger rows are immutable';
          end if;
          if new.status not in ('succeeded', 'failed') then
            raise exception using
              errcode = '23514',
              message = 'model call ledger permits one terminal transition';
          end if;
          if row(
            new.call_id, new.call_key, new.user_id, new.job_id, new.feature,
            new.operation, new.provider, new.model, new.prompt_version,
            new.schema_version, new.price_version,
            new.input_rate_cny_per_million,
            new.cached_input_rate_cny_per_million,
            new.output_rate_cny_per_million, new.input_fingerprint,
            new.started_at
          ) is distinct from row(
            old.call_id, old.call_key, old.user_id, old.job_id, old.feature,
            old.operation, old.provider, old.model, old.prompt_version,
            old.schema_version, old.price_version,
            old.input_rate_cny_per_million,
            old.cached_input_rate_cny_per_million,
            old.output_rate_cny_per_million, old.input_fingerprint,
            old.started_at
          ) then
            raise exception using
              errcode = '23514',
              message = 'model call ledger identity is immutable';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger model_call_ledger_enforce_transition "
        "before update or delete on ai_audit.model_call_ledger "
        "for each row execute function "
        "ai_audit.enforce_model_call_ledger_transition()"
    )
    op.create_table(
        "essay_review_checkpoints",
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "phase in ('initial', 'repair')",
            name="essay_review_checkpoint_phase_check",
        ),
        sa.CheckConstraint(
            "contract_version = 'essay-review-checkpoint-v1'",
            name="essay_review_checkpoint_contract_check",
        ),
        sa.CheckConstraint(
            "char_length(key_id) > 0 and octet_length(nonce) = 12 "
            "and octet_length(ciphertext) > 16 and length(payload_sha256) = 64",
            name="essay_review_checkpoint_crypto_check",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="essay_review_checkpoint_expiry_check",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["platform.background_jobs.job_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["ai_audit.model_call_ledger.call_id"],
        ),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint(
            "job_id",
            "phase",
            name="essay_review_checkpoint_job_phase_key",
        ),
        schema="ai_audit",
    )
    op.create_index(
        "ai_essay_review_checkpoint_expiry_idx",
        "essay_review_checkpoints",
        ["expires_at"],
        schema="ai_audit",
    )
    op.create_table(
        "essay_review_execution_commands",
        sa.Column("command_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("command_kind", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "command_kind in ('initial', 'regenerate')",
            name="essay_review_execution_command_kind_check",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="essay_review_execution_command_fingerprint_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["learner.users.user_id"]),
        sa.ForeignKeyConstraint(["job_id"], ["platform.background_jobs.job_id"]),
        sa.PrimaryKeyConstraint("command_id"),
        schema="learning",
    )
    op.create_index(
        "learning_essay_review_execution_command_user_idx",
        "essay_review_execution_commands",
        ["user_id", "created_at"],
        schema="learning",
    )
    op.execute(
        "create trigger essay_review_execution_commands_append_only "
        "before update or delete on learning.essay_review_execution_commands "
        "for each row execute function learning.reject_essay_intelligence_mutation()"
    )
    op.create_table(
        "essay_review_model_calls",
        sa.Column("review_version_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "phase in ('initial', 'repair')",
            name="essay_review_model_call_phase_check",
        ),
        sa.CheckConstraint(
            "ordinal between 1 and 2",
            name="essay_review_model_call_ordinal_check",
        ),
        sa.ForeignKeyConstraint(
            ["review_version_id"],
            ["learning.essay_review_versions.review_version_id"],
        ),
        sa.ForeignKeyConstraint(["call_id"], ["ai_audit.model_call_ledger.call_id"]),
        sa.PrimaryKeyConstraint("review_version_id", "call_id"),
        sa.UniqueConstraint(
            "review_version_id",
            "ordinal",
            name="essay_review_model_call_ordinal_key",
        ),
        schema="ai_audit",
    )
    op.execute(
        "create trigger essay_review_model_calls_append_only "
        "before update or delete on ai_audit.essay_review_model_calls "
        "for each row execute function ai_audit.reject_append_only_mutation()"
    )


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (
            select 1
              from learning.essay_review_versions
             group by attempt_id, input_fingerprint
            having count(*) > 1
          ) then
            raise exception using
              message = 'cannot downgrade grounded review execution history '
                        'without deleting immutable retry versions';
          end if;
        end
        $$
        """
    )
    op.execute("drop index platform.platform_essay_review_job_user_answer_idx")
    op.execute("drop index platform.platform_essay_review_job_review_version_idx")
    op.execute(
        "drop trigger essay_review_execution_commands_append_only "
        "on learning.essay_review_execution_commands"
    )
    op.drop_index(
        "learning_essay_review_execution_command_user_idx",
        table_name="essay_review_execution_commands",
        schema="learning",
    )
    op.drop_table("essay_review_execution_commands", schema="learning")
    op.drop_table("essay_review_model_calls", schema="ai_audit")
    op.drop_index(
        "ai_essay_review_checkpoint_expiry_idx",
        table_name="essay_review_checkpoints",
        schema="ai_audit",
    )
    op.drop_table("essay_review_checkpoints", schema="ai_audit")
    op.drop_index(
        "ai_model_call_ledger_job_idx",
        table_name="model_call_ledger",
        schema="ai_audit",
    )
    op.drop_index(
        "ai_model_call_ledger_user_feature_idx",
        table_name="model_call_ledger",
        schema="ai_audit",
    )
    op.drop_table("model_call_ledger", schema="ai_audit")
    op.execute("drop function ai_audit.enforce_model_call_ledger_transition()")
    op.execute("drop function ai_audit.reject_append_only_mutation()")
    op.drop_constraint(
        "essay_review_input_execution_attempt_key",
        "essay_review_versions",
        schema="learning",
        type_="unique",
    )
    op.drop_constraint(
        "essay_review_attempt_execution_key",
        "essay_review_versions",
        schema="learning",
        type_="unique",
    )
    op.drop_constraint(
        "essay_review_execution_attempt_check",
        "essay_review_versions",
        schema="learning",
        type_="check",
    )
    op.drop_constraint(
        "essay_review_execution_fingerprint_check",
        "essay_review_versions",
        schema="learning",
        type_="check",
    )
    op.create_unique_constraint(
        "essay_review_attempt_input_key",
        "essay_review_versions",
        ["attempt_id", "input_fingerprint"],
        schema="learning",
    )
    op.drop_column(
        "essay_review_versions",
        "execution_attempt_no",
        schema="learning",
    )
    op.drop_column(
        "essay_review_versions",
        "execution_fingerprint",
        schema="learning",
    )
    op.drop_constraint(
        "essay_review_grounding_report_check",
        "essay_review_versions",
        schema="learning",
        type_="check",
    )
    op.drop_index(
        "product_essay_rubric_compilation_input_idx",
        table_name="essay_rubric_versions",
        schema="product",
    )
    op.drop_constraint(
        "essay_review_score_confidence_reasons_check",
        "essay_review_versions",
        schema="learning",
        type_="check",
    )
    op.drop_column(
        "essay_review_versions",
        "grounding_report",
        schema="learning",
    )
    op.drop_column(
        "essay_review_versions",
        "grounding_contract_version",
        schema="learning",
    )
    op.drop_column(
        "essay_review_versions",
        "score_confidence_reasons",
        schema="learning",
    )
    op.drop_constraint(
        "essay_rubric_compilation_audit_check",
        "essay_rubric_versions",
        schema="product",
        type_="check",
    )
    op.drop_constraint(
        "essay_rubric_material_binding_check",
        "essay_rubric_versions",
        schema="product",
        type_="check",
    )
    op.drop_constraint(
        "essay_review_rubric_material_fk",
        "essay_review_versions",
        schema="learning",
        type_="foreignkey",
    )
    op.drop_constraint(
        "essay_attempt_rubric_material_fk",
        "essay_attempts",
        schema="learning",
        type_="foreignkey",
    )
    op.drop_constraint(
        "essay_rubric_material_identity_key",
        "essay_rubric_versions",
        schema="product",
        type_="unique",
    )
    op.drop_constraint(
        "essay_rubric_material_task_fk",
        "essay_rubric_versions",
        schema="product",
        type_="foreignkey",
    )
    op.drop_column("essay_rubric_versions", "compilation_audit", schema="product")
    op.drop_column(
        "essay_rubric_versions",
        "compilation_input_fingerprint",
        schema="product",
    )
    op.drop_column(
        "essay_rubric_versions",
        "material_bundle_version_id",
        schema="product",
    )
