"""add source columns to jobs table

Revision ID: a1b2c3d4e5f6
Revises: 18eef6f316c3
Create Date: 2026-01-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '18eef6f316c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add source tracking columns to jobs table."""
    op.add_column('jobs', sa.Column('source_type', sa.String(20), nullable=False, server_default='local'))
    op.add_column('jobs', sa.Column('source_repo', sa.String(512), nullable=True))
    op.add_column('jobs', sa.Column('source_branch', sa.String(255), nullable=True))


def downgrade() -> None:
    """Remove source tracking columns from jobs table."""
    op.drop_column('jobs', 'source_branch')
    op.drop_column('jobs', 'source_repo')
    op.drop_column('jobs', 'source_type')
