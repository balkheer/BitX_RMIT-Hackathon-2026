"""Audit chain integrity, tamper detection, concurrency and data hygiene."""

from __future__ import annotations

import datetime as dt
import json
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.chain import Actor, append_event, canonical_json, normalize_ts, verify_chain
from tests.conftest import app_engine, audit_rows, owner_engine, super_engine


def _verify_with(conn) -> object:
    return verify_chain(Session(bind=conn))


def test_chain_verifies():
    with app_engine().connect() as c:
        res = _verify_with(c)
    assert res.valid and res.checked > 0 and res.first_invalid_seq is None


def _tamper(sql: str, params: dict | None = None):
    """Run a tampering statement as superuser in a transaction, verify, then roll back."""
    with super_engine().connect() as c:
        tx = c.begin()
        c.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        for stmt in [x.strip() for x in sql.split(";") if x.strip()]:
            c.execute(text(stmt), params or {})
        res = _verify_with(c)
        tx.rollback()
    return res


def test_modified_event_detected():
    res = _tamper("UPDATE audit_events SET decision='tampered' WHERE seq = 5")
    assert not res.valid and res.first_invalid_seq == 5 and "modified" in res.problem


def test_modified_details_detected():
    res = _tamper("UPDATE audit_events SET details = details || '{\"x\": 1}'::jsonb WHERE seq = 7")
    assert not res.valid and res.first_invalid_seq == 7


def test_rehashed_single_event_breaks_next_link():
    # An attacker who edits one event and recomputes its own hash still breaks the next link.
    with super_engine().connect() as c:
        tx = c.begin()
        c.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))
        c.execute(text("UPDATE audit_events SET action='TAMPERED' WHERE seq = 6"))
        from app.audit.chain import compute_hash
        from app.models import AuditEvent

        s = Session(bind=c)
        e = s.execute(text("SELECT id FROM audit_events WHERE seq = 6")).scalar_one()
        ev = s.get(AuditEvent, e)
        new_hash = compute_hash(ev)
        c.execute(text("UPDATE audit_events SET event_hash=:h WHERE seq = 6"), {"h": new_hash})
        s.expire_all()
        res = verify_chain(s)
        tx.rollback()
    assert not res.valid and res.first_invalid_seq == 7 and "Link break" in res.problem


def test_missing_event_detected():
    res = _tamper("DELETE FROM audit_events WHERE seq = 4")
    assert not res.valid and res.first_invalid_seq == 4 and "missing" in res.problem


def test_reordered_events_detected():
    res = _tamper("""
        UPDATE audit_events SET seq = -1 WHERE seq = 8;
        UPDATE audit_events SET seq = 8 WHERE seq = 9;
        UPDATE audit_events SET seq = 9 WHERE seq = -1;
    """.replace("seq = -1", "seq = 999999999"))
    assert not res.valid and res.first_invalid_seq == 8


def test_inserted_event_detected():
    res = _tamper("""
        UPDATE audit_events SET seq = seq + 1000000 WHERE seq >= 10;
        UPDATE audit_events SET seq = seq - 999999 WHERE seq >= 1000000;
        INSERT INTO audit_events (seq, occurred_at, action, outcome, prev_hash, event_hash)
        VALUES (10, now(), 'FORGED', 'success', repeat('0', 64), repeat('f', 64));
    """)
    assert not res.valid and res.first_invalid_seq == 10


def test_runtime_role_cannot_update_or_delete_audit():
    for stmt in ("UPDATE audit_events SET action='X' WHERE seq=1", "DELETE FROM audit_events WHERE seq=1",
                 "TRUNCATE audit_events"):
        with app_engine().connect() as c, pytest.raises(DBAPIError):
            c.execute(text(stmt))


def test_trigger_blocks_owner_modification():
    with owner_engine().connect() as c, pytest.raises(DBAPIError) as exc:
        c.execute(text("UPDATE audit_events SET action='X' WHERE seq=1"))
    assert "append-only" in str(exc.value)


def test_runtime_role_cannot_delete_business_records():
    with app_engine().connect() as c, pytest.raises(DBAPIError):
        c.execute(text("DELETE FROM data_requests"))


def test_concurrent_appends_keep_chain_valid():
    n_threads, per_thread = 8, 15
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for j in range(per_thread):
                with Session(bind=app_engine()) as s:
                    append_event(s, actor=Actor(None, f"worker-{i}", None, None), action="CONCURRENCY_TEST",
                                 outcome="info", details={"thread": i, "n": j})
                    s.commit()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    before = len(audit_rows())
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rows = audit_rows()
    assert len(rows) == before + n_threads * per_thread
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    with app_engine().connect() as c:
        assert _verify_with(c).valid


def test_rolled_back_business_change_leaves_no_audit_row():
    before = len(audit_rows())
    with Session(bind=app_engine()) as s:
        append_event(s, actor=None, action="WILL_ROLL_BACK", outcome="info")
        s.rollback()
    assert len(audit_rows()) == before


def test_no_credentials_or_patient_values_in_audit():
    with owner_engine().connect() as c:
        notes = [r[0] for r in c.execute(text(
            "SELECT clinician_notes FROM encounters UNION SELECT sensitive_notes FROM encounters "
            "UNION SELECT medication FROM encounters UNION SELECT diagnosis_description FROM encounters")).all()
            if r[0]]
        aliases = [r[0] for r in c.execute(text("SELECT display_alias FROM patients")).all()]
    blob = json.dumps([{k: str(v) for k, v in r.items()} for r in audit_rows()])
    assert "eyJ" not in blob  # no JWTs
    for word in ("password", "client_secret", "Bearer "):
        assert word not in blob
    for v in notes + aliases:
        assert v not in blob, v


def test_details_reject_secret_keys_and_floats():
    with Session(bind=app_engine()) as s:
        with pytest.raises(ValueError):
            append_event(s, actor=None, action="X", outcome="info", details={"access_token": "abc"})
        with pytest.raises(TypeError):
            append_event(s, actor=None, action="X", outcome="info", details={"amount": 1.5})
        s.rollback()


def test_canonical_serialisation_is_deterministic():
    a = canonical_json({"b": 1, "a": [1, "ü"], "c": {"z": None, "y": True}})
    b = canonical_json({"c": {"y": True, "z": None}, "a": [1, "ü"], "b": 1})
    assert a == b == '{"a":[1,"ü"],"b":1,"c":{"y":true,"z":null}}'.encode()
    ts = dt.datetime(2026, 10, 22, 14, 0, 0, 123, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert normalize_ts(ts) == "2026-10-22T06:00:00.000123Z"
    with pytest.raises(ValueError):
        normalize_ts(dt.datetime(2026, 1, 1))
