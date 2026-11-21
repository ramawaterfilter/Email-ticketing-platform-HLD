"""canned responses

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-21 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'canned_response',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('store_id', sa.BigInteger(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('body_text', sa.Text(), server_default='', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['store_id'], ['store.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_canned_response_store_id'), 'canned_response', ['store_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_canned_response_store_id'), table_name='canned_response')
    op.drop_table('canned_response')
