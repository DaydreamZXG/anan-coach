"""Seed the login terms policy version used to record login consent events.

The WeChat mini program login page requires the user to check "我已阅读并同意
用户协议与隐私保护指引" before logging in. Until now that checkbox was a
front-end-only local state and the server never stored any record of consent.
The ``privacy.consent_events`` table (created in migration 20260717_35)
already supports versioned, idempotent consent records, but its
``policy_version`` foreign key needs a policy row to reference.

This migration seeds the ``login-terms@v1`` policy version that the auth login
flow writes accepted consent events against. When the terms/privacy text
changes, a new ``login-terms@vN`` version is published and existing users are
asked to re-consent.

Revision ID: 20260805_97
Revises: 20260804_96
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260805_97"
down_revision: str | Sequence[str] | None = "20260804_96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The content hash is computed over the exact JSON bytes below, matching the
# pattern used by migration 20260717_35.
LOGIN_TERMS_CONTENT = (
    '{"policyKind":"login_terms",'
    '"title":"用户协议与隐私保护指引",'
    '"surfaces":["wechat_miniprogram_login"],'
    '"effectiveFrom":"2026-08-05"}'
)


def upgrade() -> None:
    op.execute(
        f"""
        insert into privacy.policy_versions (
          policy_version, title, content, content_sha256,
          state, published_at, retired_at
        ) values (
          'login-terms@v1',
          '用户协议与隐私保护指引',
          '{LOGIN_TERMS_CONTENT}'::jsonb,
          encode(digest(convert_to('{LOGIN_TERMS_CONTENT}', 'UTF8'), 'sha256'), 'hex'),
          'active', now(), null
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260805_97 is forward-only: login consent events reference "
        "the seeded login-terms@v1 policy version and must remain resolvable"
    )
