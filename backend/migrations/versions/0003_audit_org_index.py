"""GIN index so organisation-scoped audit queries (related_org_ids @> ARRAY[org]) stay fast.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_audit_related_orgs", "audit_events", ["related_org_ids"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_audit_related_orgs", table_name="audit_events")
