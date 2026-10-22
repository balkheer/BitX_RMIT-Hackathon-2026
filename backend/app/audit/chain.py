"""Tamper-evident, hash-linked audit log.

Hash inputs
-----------
event_hash = SHA-256( canonical_json(payload) ) where payload is:

    {seq, occurred_at, actor_user_id, actor_display, actor_role, actor_org_id,
     action, outcome, resource_type, resource_id, request_id, related_org_ids,
     decision, policy_codes, details, prev_hash}

canonical_json: UTF-8, keys sorted, no insignificant whitespace, no floats
(details are restricted to str/int/bool/None/list/dict), UUIDs as lowercase
hyphenated strings, lists kept in insertion order except related_org_ids which
is sorted.

Timestamps are normalised to UTC with microsecond precision and rendered as
`YYYY-MM-DDTHH:MM:SS.ffffffZ`. Postgres `timestamptz` stores microseconds, so
the stored value round-trips exactly.

Ordering and concurrency
------------------------
Appends take a transaction-scoped advisory lock, read the current head, and
insert `seq = head.seq + 1` with `prev_hash = head.event_hash`. The lock is
held until commit, so chain order equals commit order and two writers can never
link to the same head. `UNIQUE(seq)` is a second line of defence. The audit row
is written in the same transaction as the business change it records, so a
rolled-back change leaves no audit row and vice versa.

Verification walks events in `seq` order and checks: seq starts at 1 and is
contiguous (detects deletions and gaps), each prev_hash equals the previous
event_hash (detects reordering, insertion and deletion), and each event_hash
matches the recomputed hash (detects modification).

Limits: this is tamper-evident, not tamper-proof. Someone with write access
who rewrites the whole tail and recomputes every hash produces a valid chain,
and truncating the newest events is only detectable against an externally kept
head. See docs/AUDIT.md for the stronger controls.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AuditEvent

GENESIS_HASH = "0" * 64
AUDIT_LOCK_KEY = 0x434F4E53454E54  # "CONSENT" - arbitrary constant for pg_advisory_xact_lock

# Keys that must never appear in audit details, however they arrive.
FORBIDDEN_DETAIL_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "password",
    "secret",
    "client_secret",
    "cookie",
    "values",
    "data",
    "payload",
}


def normalize_ts(ts: dt.datetime) -> str:
    if ts.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    u = ts.astimezone(dt.UTC)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond:06d}Z"


def _clean(value: Any, path: str = "details") -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise TypeError(f"floats are not allowed in audit details ({path}); use integers or strings")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_clean(v, f"{path}[]") for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError("audit detail keys must be strings")
            if k.lower() in FORBIDDEN_DETAIL_KEYS:
                raise ValueError(f"audit details may not contain '{k}'")
            out[k] = _clean(v, f"{path}.{k}")
        return out
    raise TypeError(f"unsupported audit detail type at {path}: {type(value).__name__}")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def hash_payload(e: AuditEvent) -> dict[str, Any]:
    return {
        "seq": int(e.seq),
        "occurred_at": normalize_ts(e.occurred_at),
        "actor_user_id": _s(e.actor_user_id),
        "actor_display": e.actor_display,
        "actor_role": e.actor_role,
        "actor_org_id": _s(e.actor_org_id),
        "action": e.action,
        "outcome": e.outcome,
        "resource_type": e.resource_type,
        "resource_id": e.resource_id,
        "request_id": _s(e.request_id),
        "related_org_ids": sorted(str(o) for o in (e.related_org_ids or [])),
        "decision": e.decision,
        "policy_codes": list(e.policy_codes or []),
        "details": e.details or {},
        "prev_hash": e.prev_hash,
    }


def compute_hash(e: AuditEvent) -> str:
    return hashlib.sha256(canonical_json(hash_payload(e))).hexdigest()


@dataclass
class Actor:
    user_id: uuid.UUID | None
    display: str | None
    role: str | None
    org_id: uuid.UUID | None


def append_event(
    db: Session,
    *,
    actor: Actor | None,
    action: str,
    outcome: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_id: uuid.UUID | None = None,
    related_org_ids: list[uuid.UUID] | None = None,
    decision: str | None = None,
    policy_codes: list[str] | None = None,
    details: dict[str, Any] | None = None,
    occurred_at: dt.datetime | None = None,
) -> AuditEvent:
    """Append one event inside the caller's transaction. Caller commits."""
    if outcome not in ("success", "denied", "failure", "info"):
        raise ValueError("invalid audit outcome")
    clean_details = _clean(details or {})
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": AUDIT_LOCK_KEY})
    head = db.execute(select(AuditEvent.seq, AuditEvent.event_hash).order_by(AuditEvent.seq.desc()).limit(1)).first()
    seq = 1 if head is None else int(head.seq) + 1
    prev = GENESIS_HASH if head is None else head.event_hash

    now = occurred_at or dt.datetime.now(dt.UTC)
    now = now.astimezone(dt.UTC)
    orgs = set(related_org_ids or [])
    if actor and actor.org_id:
        orgs.add(actor.org_id)
    event = AuditEvent(
        seq=seq,
        occurred_at=now,
        actor_user_id=actor.user_id if actor else None,
        actor_display=actor.display if actor else None,
        actor_role=actor.role if actor else None,
        actor_org_id=actor.org_id if actor else None,
        action=action,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        related_org_ids=sorted(orgs, key=str),
        decision=decision,
        policy_codes=list(policy_codes or []),
        details=clean_details,
        prev_hash=prev,
        event_hash="",
    )
    event.event_hash = compute_hash(event)
    db.add(event)
    db.flush()
    return event


@dataclass
class VerificationResult:
    valid: bool
    checked: int
    head_seq: int | None
    head_hash: str | None
    first_invalid_seq: int | None
    problem: str | None
    verified_at: dt.datetime


def verify_chain(db: Session, batch_size: int = 1000) -> VerificationResult:
    expected_seq = 1
    prev_hash = GENESIS_HASH
    checked = 0
    last_seq: int | None = None
    last_hash: str | None = None
    now = dt.datetime.now(dt.UTC)
    offset_seq = 0
    while True:
        rows = (
            db.execute(select(AuditEvent).where(AuditEvent.seq > offset_seq).order_by(AuditEvent.seq).limit(batch_size))
            .scalars()
            .all()
        )
        if not rows:
            break
        for e in rows:
            if e.seq != expected_seq:
                kind = "missing event(s)" if e.seq > expected_seq else "duplicate or out-of-order sequence"
                return VerificationResult(False, checked, last_seq, last_hash, expected_seq,
                                          f"Sequence break: expected {expected_seq}, found {e.seq} ({kind}).", now)
            if e.prev_hash != prev_hash:
                return VerificationResult(False, checked, last_seq, last_hash, e.seq,
                                          f"Link break at seq {e.seq}: previous-hash does not match the prior event "
                                          "(event removed, inserted or reordered).", now)
            if compute_hash(e) != e.event_hash:
                return VerificationResult(False, checked, last_seq, last_hash, e.seq,
                                          f"Content mismatch at seq {e.seq}: stored hash does not match the event "
                                          "contents (event modified).", now)
            prev_hash = e.event_hash
            expected_seq += 1
            checked += 1
            last_seq, last_hash = e.seq, e.event_hash
        offset_seq = rows[-1].seq
    return VerificationResult(True, checked, last_seq, last_hash, None, None, now)
