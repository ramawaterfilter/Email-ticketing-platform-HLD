"""Auto-assignment settings (enable/strategy) + executive performance report."""

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.models import Customer, Setting, Ticket
from app.tickets.assignment import pick_assignee

from test_m2 import make_exec, make_mailbox, make_store


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _ticket(db, store, mailbox):
    c = Customer(email="buyer@example.com")
    db.add(c)
    db.flush()
    t = Ticket(store_id=store.id, mailbox_id=mailbox.id, customer_id=c.id, gmail_thread_id="t1", queue="customer")
    db.add(t)
    db.flush()
    return t


def _set_assignment(db, **conf):
    row = db.get(Setting, "assignment")
    if row is None:
        db.add(Setting(key="assignment", value=conf))
    else:
        row.value = conf
        flag_modified(row, "value")
    db.commit()


def test_disabled_assignment_leaves_ticket_unassigned(db):
    store, mailbox = make_store(db), make_mailbox(db)
    ex = make_exec(db, "e1@example.com")
    t = _ticket(db, store, mailbox)

    _set_assignment(db, enabled=False, strategy="least_loaded")
    assert pick_assignee(db, t) is None  # auto-assign off → nobody picked

    _set_assignment(db, enabled=True, strategy="least_loaded")
    assert pick_assignee(db, t).id == ex.id  # back on → picks the only eligible exec


def test_round_robin_rotates_by_oldest_assigned(db):
    store, mailbox = make_store(db), make_mailbox(db)
    from datetime import datetime, timezone

    a = make_exec(db, "a@example.com", last_assigned_at=datetime(2020, 1, 2, tzinfo=timezone.utc))
    b = make_exec(db, "b@example.com", last_assigned_at=datetime(2020, 1, 1, tzinfo=timezone.utc))  # assigned longer ago
    db.commit()
    _set_assignment(db, enabled=True, strategy="round_robin")
    t = _ticket(db, store, mailbox)
    # Both have zero load, so round-robin picks whoever was assigned longest ago.
    assert pick_assignee(db, t).id == b.id


def test_exec_performance_endpoint_admin_only(login, add_user):
    add_user("admin@example.com", role="admin")
    add_user("exec@example.com", role="executive")

    admin = login("admin@example.com")
    rows = admin.get("/api/v1/reports/executives").json()["rows"]
    assert any(r["email"] == "exec@example.com" for r in rows)
    row = next(r for r in rows if r["email"] == "exec@example.com")
    assert {"assigned", "resolved", "open", "reopened"} <= row.keys()

    # Executives cannot see the team performance report.
    assert login("exec@example.com").get("/api/v1/reports/executives").status_code == 403
