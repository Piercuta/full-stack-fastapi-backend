"""Add avatar_url to user

Revision ID: b7e4f2a91c05
Revises: 1a31ce608336
Create Date: 2026-08-26 12:38:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = "b7e4f2a91c05"
down_revision = "1a31ce608336"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
    )


def downgrade():
    op.drop_column("user", "avatar_url")
