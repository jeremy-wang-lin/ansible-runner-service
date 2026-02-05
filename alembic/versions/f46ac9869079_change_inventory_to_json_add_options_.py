"""change inventory to json add options column

Revision ID: f46ac9869079
Revises: a1b2c3d4e5f6
Create Date: 2026-02-06 01:23:30.119634

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f46ac9869079'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "jobs",
        "inventory",
        existing_type=sa.String(255),
        type_=sa.JSON,
        existing_nullable=False,
        postgresql_using="inventory::json",
    )
    op.add_column("jobs", sa.Column("options", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "options")
    op.alter_column(
        "jobs",
        "inventory",
        existing_type=sa.JSON,
        type_=sa.String(255),
        existing_nullable=False,
    )
