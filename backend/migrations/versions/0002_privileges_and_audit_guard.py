"""Least-privilege grants for the runtime role and an append-only audit table.

The API connects as the runtime role (default `cb_app`, override with
CB_APP_DB_ROLE). It gets no DELETE anywhere and no UPDATE on audit_events,
review_decisions or data_releases. A trigger additionally rejects UPDATE,
DELETE and TRUNCATE on audit_events for every role, including the owner; only
a superuser who disables the trigger can bypass it, and the hash chain then
makes such tampering detectable.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import os
import re

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

MUTABLE = ["organizations", "users", "patients", "patient_consents", "encounters", "insurance_claims",
           "referrals", "reporting_mandates", "policy_versions", "data_requests"]
APPEND_ONLY = ["audit_events", "review_decisions", "data_releases"]


def _role() -> str:
    role = os.environ.get("CB_APP_DB_ROLE", "cb_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise RuntimeError("invalid CB_APP_DB_ROLE")
    return role


def upgrade() -> None:
    role = _role()
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            RAISE EXCEPTION 'runtime role {role} does not exist; create it before migrating';
          END IF;
        END $$;
    """)
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    for t in MUTABLE:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {t} TO {role}")
    for t in APPEND_ONLY:
        op.execute(f"GRANT SELECT, INSERT ON {t} TO {role}")
    op.execute(f"GRANT SELECT ON alembic_version TO {role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")

    op.execute("""
        CREATE OR REPLACE FUNCTION cb_audit_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION cb_audit_append_only();
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION cb_audit_append_only();
    """)


def downgrade() -> None:
    role = _role()
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS cb_audit_append_only()")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
