"""per-client assignments

Revision ID: f7d4a0c3e8b5
Revises: e6c3f9b2d7a4
"""
from alembic import op
import sqlalchemy as sa

revision = 'f7d4a0c3e8b5'
down_revision = 'e6c3f9b2d7a4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('company_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'user_id'))
    with op.batch_alter_table('company_assignments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_company_assignments_company_id'), ['company_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('company_assignments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_company_assignments_company_id'))
    op.drop_table('company_assignments')
