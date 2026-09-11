"""Add created_at to item for dashboard activity series.

Revision ID: c3a91f0e2b77
Revises: b7e4f2a91c05
Create Date: 2026-08-27 17:40:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c3a91f0e2b77"
down_revision = "b7e4f2a91c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "item",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_item_created_at"), "item", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_item_created_at"), table_name="item")
    op.drop_column("item", "created_at")
