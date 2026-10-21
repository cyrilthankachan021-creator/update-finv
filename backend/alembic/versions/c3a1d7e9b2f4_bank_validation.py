"""bank statement validation report on documents

Revision ID: c3a1d7e9b2f4
Revises: 85a808cb39e4
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3a1d7e9b2f4'
down_revision = '85a808cb39e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('validation', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('validation')
