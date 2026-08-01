"""Add perceptual hash columns to question_visual_fingerprints.

The visual fingerprints table only stored a composite fingerprint_sha256,
which supports exact matching. For the photo-to-question use case (用户拍照
找原题), photos always have angle/scale/lighting transforms, so exact sha256
never matches. Store the four perceptual hashes (phash/dhash/ahash/edge_dhash)
so the matcher can compare by hamming distance.

Revision ID: 20260801_90
Revises: 20260731_89
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260801_90"
down_revision: str | Sequence[str] | None = "20260731_89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_visual_fingerprints",
        sa.Column("phash", sa.Text(), nullable=True),
        schema="product",
    )
    op.add_column(
        "question_visual_fingerprints",
        sa.Column("dhash", sa.Text(), nullable=True),
        schema="product",
    )
    op.add_column(
        "question_visual_fingerprints",
        sa.Column("ahash", sa.Text(), nullable=True),
        schema="product",
    )
    op.add_column(
        "question_visual_fingerprints",
        sa.Column("edge_dhash", sa.Text(), nullable=True),
        schema="product",
    )
    op.create_check_constraint(
        "question_visual_fingerprints_phash_hex_check",
        "question_visual_fingerprints",
        "phash is null or phash ~ '^[0-9a-f]{16}$'",
        schema="product",
    )
    op.create_check_constraint(
        "question_visual_fingerprints_dhash_hex_check",
        "question_visual_fingerprints",
        "dhash is null or dhash ~ '^[0-9a-f]{16}$'",
        schema="product",
    )
    op.create_check_constraint(
        "question_visual_fingerprints_ahash_hex_check",
        "question_visual_fingerprints",
        "ahash is null or ahash ~ '^[0-9a-f]{16}$'",
        schema="product",
    )
    op.create_check_constraint(
        "question_visual_fingerprints_edge_dhash_hex_check",
        "question_visual_fingerprints",
        "edge_dhash is null or edge_dhash ~ '^[0-9a-f]{16}$'",
        schema="product",
    )


def downgrade() -> None:
    raise RuntimeError(
        "migration 20260801_90 is forward-only: dropping the hash columns would "
        "lose photo-matching capability on a live table"
    )
