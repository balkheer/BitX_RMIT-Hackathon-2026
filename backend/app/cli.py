"""Operator CLI. Runs with server-side database access; not exposed over HTTP.

    python -m app.cli bootstrap                 # initial policy + platform organisation
    python -m app.cli seed-synthetic            # fictional demo dataset (refused in production)
    python -m app.cli create-admin --username ops.admin --display-name "Ops Admin" --subject <sub>
    python -m app.cli link-identity --username j.walker --subject <tidecloak sub>
    python -m app.cli list-users
    python -m app.cli verify-audit
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.audit.chain import append_event, verify_chain
from app.auth.identity import get_verifier
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Organization, PolicyVersion, User
from app.policy.document import INITIAL_POLICY

PLATFORM_ORG = ("consentbridge-platform", "ConsentBridge Platform Operations", "platform")


def bootstrap() -> None:
    with get_sessionmaker()() as db:
        if db.execute(select(PolicyVersion.id).limit(1)).first() is None:
            db.add(PolicyVersion(version=INITIAL_POLICY.version, document=INITIAL_POLICY.model_dump(mode="json"),
                                 is_active=True, change_note="Initial policy loaded by bootstrap."))
            append_event(db, actor=None, action="POLICY_CHANGED", outcome="success", resource_type="policy",
                         resource_id=INITIAL_POLICY.version, decision="activated",
                         details={"previous_version": None, "new_version": INITIAL_POLICY.version,
                                  "change_note": "Initial policy loaded by bootstrap."})
            print(f"policy {INITIAL_POLICY.version} activated")
        slug, name, kind = PLATFORM_ORG
        if db.execute(select(Organization.id).where(Organization.slug == slug)).first() is None:
            db.add(Organization(slug=slug, name=name, org_type=kind))
            print("platform organisation created")
        db.commit()


def _issuer() -> str:
    adapter = get_verifier().adapter
    if adapter is None:
        sys.exit("TideCloak is not configured (set CB_TIDECLOAK_CONFIG_PATH or CB_TIDECLOAK_* variables)")
    return adapter.issuer


def link_identity(username: str, subject: str) -> None:
    issuer = _issuer()
    with get_sessionmaker()() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            sys.exit(f"no such user: {username}")
        clash = db.execute(select(User.username).where(User.external_issuer == issuer,
                                                       User.external_subject == subject,
                                                       User.id != user.id)).scalar_one_or_none()
        if clash:
            sys.exit(f"subject already linked to {clash}")
        user.external_issuer, user.external_subject = issuer, subject
        append_event(db, actor=None, action="IDENTITY_LINKED", outcome="success", resource_type="user",
                     resource_id=str(user.id), related_org_ids=[user.organization_id],
                     details={"username": username, "via": "operator_cli"})
        db.commit()
        print(f"linked {username} -> {issuer} / {subject}")


def create_admin(username: str, display_name: str, subject: str) -> None:
    issuer = _issuer()
    bootstrap()
    with get_sessionmaker()() as db:
        org = db.execute(select(Organization).where(Organization.slug == PLATFORM_ORG[0])).scalar_one()
        if db.execute(select(User.id).where(User.username == username)).first():
            sys.exit("username exists")
        u = User(username=username, display_name=display_name, organization_id=org.id, role="platform_admin",
                 external_issuer=issuer, external_subject=subject)
        db.add(u)
        db.flush()
        append_event(db, actor=None, action="USER_CREATED", outcome="success", resource_type="user",
                     resource_id=str(u.id), related_org_ids=[org.id],
                     details={"username": username, "role": "platform_admin", "via": "operator_cli"})
        db.commit()
        print(f"platform administrator {username} created")


def seed_synthetic(if_empty: bool = False) -> None:
    if get_settings().environment == "production":
        sys.exit("refusing to load the synthetic dataset when CB_ENVIRONMENT=production")
    bootstrap()
    from app.models import Patient
    from app.seed import seed

    with get_sessionmaker()() as db:
        if if_empty and db.execute(select(Patient.id).limit(1)).first():
            print("synthetic data already present; nothing to do")
            return
        print(json.dumps(seed(db), indent=2))


def list_users() -> None:
    with get_sessionmaker()() as db:
        for u in db.execute(select(User).order_by(User.username)).scalars():
            linked = "linked" if u.external_subject else "UNLINKED"
            print(f"{u.username:14} {u.role:20} {u.organization.slug:26} {linked:9} "
                  f"{'active' if u.is_active else 'disabled'}")


def verify() -> None:
    with get_sessionmaker()() as db:
        r = verify_chain(db)
        print(json.dumps({"valid": r.valid, "checked": r.checked, "head_seq": r.head_seq, "head_hash": r.head_hash,
                          "problem": r.problem}, indent=2))
        sys.exit(0 if r.valid else 2)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap")
    sd = sub.add_parser("seed-synthetic")
    sd.add_argument("--if-empty", action="store_true", help="skip quietly when data already exists")
    sub.add_parser("list-users")
    sub.add_parser("verify-audit")
    li = sub.add_parser("link-identity")
    li.add_argument("--username", required=True)
    li.add_argument("--subject", required=True, help="the `sub` claim of the user's TideCloak access token")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--username", required=True)
    ca.add_argument("--display-name", required=True)
    ca.add_argument("--subject", required=True)
    a = ap.parse_args(argv)
    {
        "bootstrap": bootstrap,
        "seed-synthetic": lambda: seed_synthetic(a.if_empty),
        "list-users": list_users,
        "verify-audit": verify,
        "link-identity": lambda: link_identity(a.username, a.subject),
        "create-admin": lambda: create_admin(a.username, a.display_name, a.subject),
    }[a.cmd]()


if __name__ == "__main__":
    main()
