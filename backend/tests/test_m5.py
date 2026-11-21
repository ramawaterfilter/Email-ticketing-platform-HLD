import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.gmail.sync import backfill_mailbox
from app.main import app
from app.models import Mailbox, Message, Ticket
from app.worker import run_sync_once

from fakes import FakeGmail, build_message
from test_m2 import make_address, make_mailbox, make_store

HEADERS = {"X-Requested-With": "desk"}


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def seed(db):
    store = make_store(db)
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    return store, mailbox


def test_backfill_imports_recent_mail(db):
    store, mailbox = seed(db)
    gmail = FakeGmail()
    for i in range(3):
        gmail.add(build_message(f"old{i}", thread_id=f"t{i}", subject=f"Older {i}"))

    backfill_mailbox(db, mailbox, gmail)
    db.refresh(mailbox)
    assert mailbox.backfilled_at is not None
    assert db.query(Ticket).count() == 3


def test_worker_backfills_then_syncs_without_duplicates(db):
    store, mailbox = seed(db)
    mailbox.last_history_id = None
    db.commit()
    gmail = FakeGmail()
    for i in range(3):
        gmail.add(build_message(f"m{i}", thread_id=f"t{i}"))

    run_sync_once(factory=lambda m: gmail)
    db.expire_all()
    assert db.get(Mailbox, mailbox.id).backfilled_at is not None
    assert db.query(Message).count() == 3

    run_sync_once(factory=lambda m: gmail)  # second tick must not re-import
    assert db.query(Message).count() == 3


def test_health_sync_reports_stale_mailboxes(db):
    from datetime import datetime, timedelta, timezone

    client = TestClient(app, headers=HEADERS)
    assert client.get("/api/v1/health/sync").json()["ok"] is True  # no mailboxes yet

    store, mailbox = seed(db)
    db.query(Mailbox).update(
        {
            Mailbox.last_sync_at: datetime.now(timezone.utc) - timedelta(minutes=20),
            Mailbox.created_at: datetime.now(timezone.utc) - timedelta(hours=1),
        }
    )
    db.commit()
    assert client.get("/api/v1/health/sync").status_code == 503

    db.query(Mailbox).update({Mailbox.last_sync_at: datetime.now(timezone.utc)})
    db.commit()
    r = client.get("/api/v1/health/sync")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_activating_sets_backfill_days(login, db):
    from test_m2 import make_address as _  # noqa: F401

    store, mailbox = seed(db)
    mailbox.status = "pending"
    db.commit()
    admin = login("boss@example.com")
    r = admin.patch(f"/api/v1/mailboxes/{mailbox.id}", json={"status": "active", "backfill_days": 14})
    assert r.status_code == 200
    db.expire_all()
    fresh = db.get(Mailbox, mailbox.id)
    assert fresh.status == "active" and fresh.backfill_days == 14 and fresh.backfilled_at is None
