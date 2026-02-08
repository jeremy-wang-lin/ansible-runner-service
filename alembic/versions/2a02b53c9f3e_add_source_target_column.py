"""add source_target column

Revision ID: 2a02b53c9f3e
Revises: f46ac9869079
Create Date: 2026-02-08 21:49:48.731183

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a02b53c9f3e'
down_revision: Union[str, Sequence[str], None] = 'f46ac9869079'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add source_target column to jobs table with backfill."""
    # Add column as nullable first
    op.add_column('jobs', sa.Column('source_target', sa.String(20), nullable=True))

    # Backfill based on source_type
    op.execute("""
        UPDATE jobs
        SET source_target = CASE
            WHEN source_type = 'local' THEN 'playbook'
            WHEN source_type = 'playbook' THEN 'playbook'
            WHEN source_type = 'role' THEN 'role'
            ELSE 'playbook'
        END
    """)

    # Make NOT NULL after backfill
    op.alter_column('jobs', 'source_target', existing_type=sa.String(20), nullable=False)


def downgrade() -> None:
    """Remove source_target column from jobs table."""
    op.drop_column('jobs', 'source_target')
