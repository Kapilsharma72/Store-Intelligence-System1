"""Initial migration

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-20

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(50), nullable=False, index=True),
        sa.Column("camera_id", sa.String(50)),
        sa.Column("visitor_id", sa.String(12), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("zone_id", sa.String(50), nullable=True),
        sa.Column("dwell_ms", sa.Integer, nullable=True),
        sa.Column("is_staff", sa.Boolean, default=False, index=True),
        sa.Column("confidence", sa.Float),
        sa.Column("metadata", sa.JSON),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id"),
    )
    op.create_table(
        "pos_records",
        sa.Column("transaction_id", sa.String(36), primary_key=True),
        sa.Column("store_id", sa.String(50), nullable=False, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("basket_value_inr", sa.Numeric(12, 2)),
    )


def downgrade() -> None:
    op.drop_table("pos_records")
    op.drop_table("events")
