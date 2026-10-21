"""audit report versions and sign-off

Revision ID: e6c3f9b2d7a4
Revises: d5b2e8a1c6f3
"""
from alembic import op
import sqlalchemy as sa

revision = 'e6c3f9b2d7a4'
down_revision = 'd5b2e8a1c6f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_report_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('signed_by', sa.Integer(), nullable=True),
        sa.Column('signed_at', sa.DateTime(), nullable=True),
        sa.Column('sign_note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['signed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'version'))
    with op.batch_alter_table('audit_report_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_report_versions_company_id'), ['company_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('audit_report_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_report_versions_company_id'))
    op.drop_table('audit_report_versions')
