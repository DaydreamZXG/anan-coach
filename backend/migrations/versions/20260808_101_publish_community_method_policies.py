"""Publish the first production community-method policy set.

Revision ID: 20260808_101
Revises: 20260807_100

The Stage 3.5 tables intentionally failed closed when no policy had been
published.  The product surface is now open for a limited trial, so publish a
small-sample, conservative and fully versioned policy set.  Existing published
policies win: this migration never replaces an operator-published resource.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260808_101"
down_revision: str | Sequence[str] | None = "20260807_100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        insert into product.community_method_card_policy_resources (
          policy_resource_id, operation, policy_version, state, rules,
          rules_sha256, prompt_version, provider, model,
          provider_release_id, output_schema_version,
          published_at, created_at
        )
        select
          '4255b0ab-178a-551e-9f01-8f476393f1b6'::uuid,
          'candidate_generation',
          'community-candidate@2026-08-08-v1',
          'published',
          '{"humanConfirmationRequired":true,"sourceGroundingRequired":true}'::jsonb,
          'd62f375ebb1772baa47dc4e0d15bf30828d10888bca646648469fef1f7f615a6',
          'community-candidate-prompt@2',
          'tokenrhythm',
          'deepseek-v4-flash',
          'tokenrhythm-deepseek-v4-flash',
          'community-candidate-output@2',
          transaction_timestamp(),
          transaction_timestamp()
        where not exists (
          select 1
          from product.community_method_card_policy_resources
          where operation = 'candidate_generation' and state = 'published'
        );

        insert into product.community_method_card_policy_resources (
          policy_resource_id, operation, policy_version, state, rules,
          rules_sha256, prompt_version, provider, model,
          provider_release_id, output_schema_version,
          published_at, created_at
        )
        select
          'a3212c07-3a68-5b24-b31b-25804738cadc'::uuid,
          'automated_review',
          'community-review@2026-08-08-v1',
          'published',
          '{
            "blockedPatterns":[
              "(微信|VX|v信|QQ|电话|手机号|联系我|加群)",
              "(包过|保过|代考|押题必中)",
              "(忽略|绕过).*(指令|规则|审核)"
            ],
            "dimensions":[
              "duplicate","copyright","safety","knowledge","answer_consistency"
            ]
          }'::jsonb,
          '102f7522581580aa7d09f007e7127f042f61c73612c1de8696efd5ac9ec27e09',
          'community-review-prompt@1',
          'deterministic',
          'five-dimension-review-v1',
          'community-review-engine@1',
          'community-review-output@1',
          transaction_timestamp(),
          transaction_timestamp()
        where not exists (
          select 1
          from product.community_method_card_policy_resources
          where operation = 'automated_review' and state = 'published'
        );

        insert into product.community_method_card_policy_resources (
          policy_resource_id, operation, policy_version, state, rules,
          rules_sha256, prompt_version, provider, model,
          provider_release_id, output_schema_version,
          published_at, created_at
        )
        select
          'd79eebfa-e577-52e4-a963-852f8ac7869b'::uuid,
          'report_decision',
          'community-report@2026-08-08-v1',
          'published',
          '{"confirmedCategories":["unsafe"]}'::jsonb,
          'd499316a05e4240f488ce963c706f6dbdb98cea83bc1bbee9a65eeb276ebb6dc',
          'community-report-prompt@1',
          'deterministic',
          'fixed-report-policy-v1',
          'community-report-policy@1',
          'community-report-output@1',
          transaction_timestamp(),
          transaction_timestamp()
        where not exists (
          select 1
          from product.community_method_card_policy_resources
          where operation = 'report_decision' and state = 'published'
        );

        insert into product.community_method_card_effect_policies (
          policy_id, policy_version, state, rules, rules_sha256,
          published_at, created_at
        )
        select
          'bc75140e-fa83-5dc5-8171-13fc0bced355'::uuid,
          'community-effect@2026-08-08-v1',
          'published',
          '{
            "algorithm":"wilson_composite_v1",
            "rankingPolicyVersion":"community-ranking@2026-08-08-v1",
            "minimumEligibleTrials":5,
            "minimumDelayedVerifiedTrials":2,
            "minimumHelpfulVotes":3,
            "confidenceZ":"1.96",
            "correctnessWeight":"0.5",
            "helpfulnessWeight":"0.2",
            "delayedVerificationWeight":"0.3",
            "validationThreshold":"0.45",
            "minimumApplicabilityOverlap":0,
            "maximumDiscoveryResults":12,
            "personalizationWeight":"0.35",
            "delayedVerificationDelaySeconds":604800,
            "requireBaseline":true
          }'::jsonb,
          'fc052dc6eb6e6db14f0bbde95a834f15ff420663ee1cf42ae51f4c5a371b7afe',
          transaction_timestamp(),
          transaction_timestamp()
        where not exists (
          select 1
          from product.community_method_card_effect_policies
          where state = 'published'
        );

        do $$
        begin
          if (
            select count(*)
            from product.community_method_card_policy_resources
            where state = 'published'
              and operation in (
                'candidate_generation', 'automated_review', 'report_decision'
              )
          ) <> 3 then
            raise exception 'community method policy publication is incomplete';
          end if;
          if (
            select count(*)
            from product.community_method_card_effect_policies
            where state = 'published'
          ) <> 1 then
            raise exception 'community effect policy publication is incomplete';
          end if;
        end $$;
        """
    )


def downgrade() -> None:
    # Published policy resources are immutable audit facts.  A code rollback may
    # keep reading them, so downgrading the schema revision deliberately retains
    # these compatible rows instead of deleting or rewriting history.
    pass
