"""create heartbeats table

Revision ID: 0001_create_heartbeats
Revises:
Create Date: 2026-08-15
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001_create_heartbeats"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "heartbeats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="scheduler"),
    )


def downgrade() -> None:
    op.drop_table("heartbeats")
