from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal
from app.gmail.send import send_reply
from app.models import Customer, Mailbox, Setting, Ticket
from app.tickets.escalation import check_mailbox_health, escalate

from fakes import FakeGmail, build_message
from test_m2 import do_ingest, make_address, make_exec, make_mailbox, make_store

BOSS = "boss@example.com"
UTC = timezone.utc


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def set_hours_247(db):
    """24/7 business hours, so business minutes equal wall-clock minutes in a test."""
    db.merge(Setting(key="business_hours", value={"timezone": "UTC", "week": {d: [["00:00", "23:59"]] for d in
             ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}, "holidays": []}))
    db.commit()


def recorder():
    calls = []
    return calls, lambda db, emails, subject, body: calls.append((sorted(emails), subject))


def seed(db):
    store = make_store(db)
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    return store, mailbox


def make_ticket(db, store, mailbox, **fields) -> Ticket:
    cust = Customer(email=f"c{id(fields)}@buyer.com", name="C")
    db.add(cust)
    db.flush()
    fields.setdefault("status", "open")
    fields.setdefault("queue", "customer")
    t = Ticket(store_id=store.id, mailbox_id=mailbox.id, customer_id=cust.id, gmail_thread_id="t", subject="S",
               **fields)
    db.add(t)
    db.commit()
    return t


# ---- clock wiring ----


def test_new_customer_ticket_gets_both_clocks(db):
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    t = db.query(Ticket).one()
    assert t.reply_due_at is not None and t.resolution_due_at is not None


def test_sla_disabled_arms_no_clocks(db):
    db.merge(Setting(key="sla_enabled", value={"enabled": False}))
    db.commit()
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    t = db.query(Ticket).one()
    assert t.reply_due_at is None and t.resolution_due_at is None


def test_reply_clears_reply_clock_and_pauses_resolution(db):
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    t = db.query(Ticket).one()
    agent = make_exec(db, "a@example.com")

    send_reply(db, t, agent.id, body_text="hi", cc=[], client_request_id="req-0001",
               last_seen_message_id=None, resolve=False, client=FakeGmail())
    db.refresh(t)
    assert t.reply_due_at is None and t.awaiting_reply_since is None
    assert t.resolution_due_at is None and t.resolution_remaining_min is not None  # paused


def test_customer_reply_resumes_the_clocks(db):
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    t = db.query(Ticket).one()
    agent = make_exec(db, "a@example.com")
    send_reply(db, t, agent.id, body_text="hi", cc=[], client_request_id="req-0001",
               last_seen_message_id=None, resolve=False, client=FakeGmail())

    do_ingest(db, mailbox, build_message("m2", thread_id="t1", from_addr="cust@buyer.com"))
    db.refresh(t)
    assert t.status == "open"
    assert t.reply_due_at is not None and t.resolution_due_at is not None
    assert t.resolution_remaining_min is None  # resumed


def test_resolve_stops_both_clocks(db):
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    t = db.query(Ticket).one()
    agent = make_exec(db, "a@example.com")
    send_reply(db, t, agent.id, body_text="done", cc=[], client_request_id="req-0001",
               last_seen_message_id=None, resolve=True, client=FakeGmail())
    db.refresh(t)
    assert t.status == "resolved"
    assert t.reply_due_at is None and t.resolution_due_at is None and t.resolution_remaining_min is None


# ---- escalation ----


def test_reply_breach_alerts_managers_once(db):
    store, mailbox = seed(db)
    mgr = make_exec(db, "m@example.com")
    mgr.role = "manager"
    agent = make_exec(db, "a@example.com")
    t = make_ticket(db, store, mailbox, status="open", assignee_id=agent.id,
                    reply_due_at=datetime.now(UTC) - timedelta(minutes=5))

    calls, notify = recorder()
    escalate(db, notify=notify)
    db.refresh(t)
    assert t.reply_breached_at is not None
    assert any("breach" in s for _, s in calls)

    escalate(db, notify=notify)  # second pass must not re-alert
    assert sum("breach" in s for _, s in calls) == 1


def test_reply_warning_goes_to_the_assignee(db):
    set_hours_247(db)
    store, mailbox = seed(db)
    agent = make_exec(db, "a@example.com")
    t = make_ticket(db, store, mailbox, status="open", assignee_id=agent.id,
                    reply_due_at=datetime.now(UTC) + timedelta(minutes=10))

    calls, notify = recorder()
    escalate(db, notify=notify)
    db.refresh(t)
    assert t.reply_warned_at is not None
    assert calls and calls[0][0] == ["a@example.com"]


def test_unassigned_ticket_is_flagged_once(db):
    store, mailbox = seed(db)
    make_exec(db, "m@example.com").role = "manager"
    db.commit()
    make_ticket(db, store, mailbox, status="new", created_at=datetime.now(UTC) - timedelta(minutes=20))

    calls, notify = recorder()
    escalate(db, notify=notify)
    escalate(db, notify=notify)
    assert sum("Unassigned" in s for _, s in calls) == 1


def test_resolution_breach_alerts(db):
    store, mailbox = seed(db)
    make_exec(db, "m@example.com").role = "manager"
    db.commit()
    t = make_ticket(db, store, mailbox, status="open", assignee_id=None,
                    resolution_due_at=datetime.now(UTC) - timedelta(minutes=1))
    calls, notify = recorder()
    escalate(db, notify=notify)
    db.refresh(t)
    assert t.resolution_breached_at is not None
    assert any("resolution" in s for _, s in calls)


def test_resolved_tickets_auto_close_after_seven_days(db):
    store, mailbox = seed(db)
    t = make_ticket(db, store, mailbox, status="resolved", resolved_at=datetime.now(UTC) - timedelta(days=8))
    _, notify = recorder()
    escalate(db, notify=notify)
    db.refresh(t)
    assert t.status == "closed" and t.closed_at is not None


def test_mailbox_health_alert_fires_once_until_recovery(db):
    store, mailbox = seed(db)
    db.query(Mailbox).update({Mailbox.last_sync_at: datetime.now(UTC) - timedelta(minutes=20),
                              Mailbox.created_at: datetime.now(UTC) - timedelta(hours=1)})
    db.commit()
    make_exec(db, "admin2@example.com").role = "admin"
    db.commit()

    calls, notify = recorder()
    check_mailbox_health(db, notify=notify)
    check_mailbox_health(db, notify=notify)
    assert sum("not syncing" in s for _, s in calls) == 1
    assert db.get(Mailbox, mailbox.id).health_alerted_at is not None


# ---- dashboard & settings API ----


def test_dashboard_counts(login, db):
    store, mailbox = seed(db)
    agent = make_exec(db, "a@example.com")
    make_ticket(db, store, mailbox, status="open", assignee_id=None)  # unassigned
    make_ticket(db, store, mailbox, status="open", assignee_id=agent.id,
                reply_breached_at=datetime.now(UTC))  # breached
    make_ticket(db, store, mailbox, status="open", assignee_id=agent.id,
                reply_due_at=datetime.now(UTC) + timedelta(minutes=30))  # due soon

    board = login(BOSS).get("/api/v1/dashboard").json()
    assert board["unassigned"] == 1 and board["breached"] == 1 and board["due_soon"] == 1
    assert board["by_store"][0]["open_count"] == 3
    assert len(board["mailboxes"]) == 1


def test_executive_cannot_see_dashboard(login, db):
    make_exec(db, "e1@example.com")
    assert login("e1@example.com").get("/api/v1/dashboard").status_code == 403


def test_sla_policy_and_settings_roundtrip(login, db):
    store = make_store(db)
    admin = login(BOSS)
    assert any(p["store_id"] is None for p in admin.get("/api/v1/sla-policies").json())  # default surfaced

    r = admin.put("/api/v1/sla-policies", json={"store_id": store.id, "first_response_min": 60,
                                                "next_response_min": 120, "resolution_min": 1440})
    assert r.status_code == 200 and r.json()["first_response_min"] == 60

    assert admin.put("/api/v1/settings/business-hours",
                     json={"timezone": "Mars/Phobos", "week": {}, "holidays": []}).status_code == 400
    ok = admin.put("/api/v1/settings/business-hours",
                   json={"timezone": "Asia/Kolkata", "week": {"mon": [["10:00", "19:00"]]}, "holidays": []})
    assert ok.status_code == 200
    assert admin.put("/api/v1/settings/alerts",
                     json={"manager_emails": ["ops@x.com"], "google_chat_webhook": None}).status_code == 200
