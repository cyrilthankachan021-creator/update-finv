"""per-client GST filing frequency and TDS setup

Revision ID: d5b2e8a1c6f3
Revises: c3a1d7e9b2f4
"""
from alembic import op
import sqlalchemy as sa

revision = 'd5b2e8a1c6f3'
down_revision = 'c3a1d7e9b2f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('company_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gst_filing', sa.String(length=10), nullable=False, server_default='monthly'))
        batch_op.add_column(sa.Column('tds_applicable', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('tan', sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('company_settings', schema=None) as batch_op:
        batch_op.drop_column('tan')
        batch_op.drop_column('tds_applicable')
        batch_op.drop_column('gst_filing')
