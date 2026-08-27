"""Add media_job table for async variant processing.

Revision ID: d4b82c1f3a88
Revises: c3a91f0e2b77
Create Date: 2026-08-27 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "d4b82c1f3a88"
down_revision = "c3a91f0e2b77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mediajob",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("original_s3_key", sa.String(length=512), nullable=False),
        sa.Column("original_url", sa.String(length=1024), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("result_urls", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mediajob_owner_id"), "mediajob", ["owner_id"], unique=False)
    op.create_index(op.f("ix_mediajob_status"), "mediajob", ["status"], unique=False)
    op.create_index(op.f("ix_mediajob_created_at"), "mediajob", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mediajob_created_at"), table_name="mediajob")
    op.drop_index(op.f("ix_mediajob_status"), table_name="mediajob")
    op.drop_index(op.f("ix_mediajob_owner_id"), table_name="mediajob")
    op.drop_table("mediajob")
