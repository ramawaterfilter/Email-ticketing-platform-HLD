from datetime import datetime, timedelta, timezone

import pytest

from app.db import SessionLocal
from app.gmail.ingest import ingest
from app.gmail.mime import parse_message
from app.gmail.sync import reconcile_mailbox, sync_mailbox
from app.models import AppUser, Customer, Mailbox, Message, Store, SupportAddress, Ticket
from app.worker import run_sync_once

from fakes import FakeGmail, build_message


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def make_store(db, name="Store A"):
    s = Store(name=name, is_active=True)
    db.add(s)
    db.commit()
    return s


def make_mailbox(db, email="support@store.com"):
    m = Mailbox(google_email=email, status="active", desk_label_id="LABEL_DESK")
    db.add(m)
    db.commit()
    return m


def make_address(db, mailbox, store, address, default=False):
    a = SupportAddress(address=address, mailbox_id=mailbox.id, store_id=store.id, is_default=default)
    db.add(a)
    db.commit()
    return a


def make_exec(db, email, **kw):
    u = AppUser(email=email, role="executive", is_active=True, is_available=True, max_open_tickets=30, **kw)
    db.add(u)
    db.commit()
    return u


def do_ingest(db, mailbox, msg):
    message = ingest(db, mailbox, parse_message(msg))
    db.commit()
    return message


@pytest.fixture
def setup(db):
    """A mailbox whose default address serves Store A, plus the fake Gmail."""
    store = make_store(db)
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    return db, mailbox, store, FakeGmail()


def test_ingesting_the_same_message_twice_makes_one_ticket(setup):
    db, mailbox, store, _ = setup
    msg = build_message("m1")
    do_ingest(db, mailbox, msg)
    do_ingest(db, mailbox, msg)  # exact same Gmail message id
    assert db.query(Message).count() == 1
    assert db.query(Ticket).count() == 1


def test_crash_midway_then_rerun_loses_nothing_and_no_duplicates(setup):
    db, mailbox, store, gmail = setup
    for i in range(10):
        gmail.add(build_message(f"m{i}", subject=f"Q{i}"))
    gmail.fail_get_id = "m3"  # 4th message blows up mid-run

    first = sync_mailbox(db, mailbox, gmail)
    assert first.status == "error" and mailbox.last_history_id is None  # cursor did not advance

    gmail.fail_get_id = None
    second = sync_mailbox(db, mailbox, gmail)
    assert second.status == "ok"
    assert db.query(Message).count() == 10  # every email, exactly once
    assert db.query(Message.gmail_message_id).distinct().count() == 10


def test_history_cursor_expired_falls_back_to_full_sync(setup):
    db, mailbox, store, gmail = setup
    for i in range(3):
        gmail.add(build_message(f"m{i}"))
    mailbox.last_history_id = "999999"  # pretend we have an old cursor
    db.commit()
    gmail.expire_history = True

    run = sync_mailbox(db, mailbox, gmail)
    assert run.kind == "full" and run.status == "ok"
    assert db.query(Message).count() == 3


def test_reconciliation_recovers_an_email_missing_from_the_database(setup):
    db, mailbox, store, gmail = setup
    gmail.add(build_message("seen"))
    sync_mailbox(db, mailbox, gmail)
    gmail.add(build_message("missed", thread_id="missed"))  # arrives but imagine sync missed it

    run = reconcile_mailbox(db, mailbox, gmail)
    assert run.recovered_count == 1
    assert db.query(Message).count() == 2


def test_healthy_reconciliation_recovers_nothing(setup):
    db, mailbox, store, gmail = setup
    gmail.add(build_message("a"))
    sync_mailbox(db, mailbox, gmail)
    run = reconcile_mailbox(db, mailbox, gmail)
    assert run.recovered_count == 0


def test_revoked_token_errors_one_mailbox_but_others_keep_syncing(db):
    good_store = make_store(db, "Good")
    bad_store = make_store(db, "Bad")
    good = make_mailbox(db, "good@store.com")
    bad = make_mailbox(db, "bad@store.com")
    make_address(db, good, good_store, "good@store.com", default=True)
    make_address(db, bad, bad_store, "bad@store.com", default=True)

    good_gmail = FakeGmail("good@store.com")
    good_gmail.add(build_message("g1", to="good@store.com"))
    bad_gmail = FakeGmail("bad@store.com")
    bad_gmail.auth_revoked = True
    clients = {good.id: good_gmail, bad.id: bad_gmail}

    run_sync_once(factory=lambda m: clients[m.id])

    db.expire_all()
    assert db.get(Mailbox, bad.id).status == "error"
    assert db.get(Mailbox, good.id).status == "active"
    assert db.query(Message).count() == 1  # the good mailbox still ingested


def test_store_comes_from_the_delivered_address(db):
    store_a = make_store(db, "Store A")
    store_b = make_store(db, "Store B")
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store_a, "support@store.com", default=True)
    make_address(db, mailbox, store_b, "care@store-b.com")  # an alias serving a different store

    do_ingest(db, mailbox, build_message("m1", to="care@store-b.com", delivered_to="care@store-b.com"))
    ticket = db.query(Ticket).one()
    assert ticket.store_id == store_b.id


def test_unknown_recipient_falls_back_to_default_store(setup):
    db, mailbox, store, _ = setup
    do_ingest(db, mailbox, build_message("m1", to="bcc-only@elsewhere.com", delivered_to="bcc-only@elsewhere.com"))
    assert db.query(Ticket).one().store_id == store.id


def test_spam_and_auto_reply_and_bounce_are_sorted_not_dropped(setup):
    db, mailbox, store, _ = setup
    do_ingest(db, mailbox, build_message("spam1", labels=["SPAM"]))
    do_ingest(db, mailbox, build_message("auto1", thread_id="auto1", extra_headers={"Auto-Submitted": "auto-replied"}))
    do_ingest(db, mailbox, build_message("bnc1", thread_id="bnc1", from_addr="mailer-daemon@google.com"))

    queues = {t.queue for t in db.query(Ticket).all()}
    assert queues == {"spam", "other"}  # nothing was discarded
    auto_msg = db.query(Message).filter_by(gmail_message_id="auto1").one()
    assert auto_msg.is_auto is True


def test_bounce_on_an_existing_ticket_flags_delivery_failure(setup):
    db, mailbox, store, _ = setup
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    ticket = db.query(Ticket).one()
    ticket.status = "waiting_customer"
    db.commit()

    do_ingest(db, mailbox, build_message("bounce", thread_id="t1", from_addr="mailer-daemon@google.com"))
    db.refresh(ticket)
    assert ticket.delivery_failed is True and ticket.status == "open"


@pytest.mark.parametrize(
    "status,closed_days_ago,expect_new",
    [("resolved", None, False), ("closed", 10, False), ("closed", 40, True)],
)
def test_reply_reopens_or_starts_a_new_linked_ticket(setup, status, closed_days_ago, expect_new):
    db, mailbox, store, _ = setup
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    ticket = db.query(Ticket).one()
    ticket.status = status
    if closed_days_ago is not None:
        ticket.closed_at = datetime.now(timezone.utc) - timedelta(days=closed_days_ago)
    db.commit()
    old_id = ticket.id

    do_ingest(db, mailbox, build_message("m2", thread_id="t1"))
    tickets = db.query(Ticket).order_by(Ticket.id).all()
    if expect_new:
        assert len(tickets) == 2 and tickets[1].previous_ticket_id == old_id
    else:
        assert len(tickets) == 1 and tickets[0].status == "open"


def test_two_tickets_at_once_go_to_different_executives(setup):
    db, mailbox, store, _ = setup
    make_exec(db, "e1@example.com")
    make_exec(db, "e2@example.com")

    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    do_ingest(db, mailbox, build_message("m2", thread_id="t2"))
    assignees = {t.assignee_id for t in db.query(Ticket).all()}
    assert len(assignees) == 2 and None not in assignees


def test_executive_restricted_to_another_store_is_not_assigned(setup):
    db, mailbox, store, _ = setup
    other = make_store(db, "Other")
    restricted = make_exec(db, "only-other@example.com")
    restricted.stores = [other]
    db.commit()

    do_ingest(db, mailbox, build_message("m1"))
    assert db.query(Ticket).one().assignee_id is None  # no eligible executive for Store A


def test_reply_sent_from_gmail_keeps_the_ticket_accurate(setup):
    db, mailbox, store, _ = setup
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    ticket = db.query(Ticket).one()

    # An executive replied straight from Gmail: it comes back with the SENT label.
    do_ingest(db, mailbox, build_message("reply", thread_id="t1", labels=["SENT"], from_addr="support@store.com"))
    db.refresh(ticket)
    assert ticket.status == "waiting_customer"
    assert ticket.first_responded_at is not None and ticket.awaiting_reply_since is None


def test_sync_labels_messages_in_gmail_as_proof_of_capture(setup):
    db, mailbox, store, gmail = setup
    gmail.add(build_message("m1"))
    sync_mailbox(db, mailbox, gmail)
    assert gmail.label_calls == ["m1"]
    assert db.query(Message).one().gmail_labelled_at is not None


def test_reconcile_relabels_a_message_that_lost_its_label(setup):
    db, mailbox, store, gmail = setup
    gmail.add(build_message("m1"))
    sync_mailbox(db, mailbox, gmail)
    db.query(Message).update({Message.gmail_labelled_at: None})  # pretend the label was removed in Gmail
    db.commit()
    gmail.label_calls.clear()

    reconcile_mailbox(db, mailbox, gmail)
    assert gmail.label_calls == ["m1"]


def test_connect_route_is_not_shadowed_by_mailbox_id():
    # Regression: /mailboxes/connect must reach the connect handler, not be parsed as a
    # mailbox id (which gave a 422 int-parse error). It redirects to Google (307) when
    # configured, or returns 400 "google_not_configured" when not - never 422.
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app, headers={"X-Requested-With": "desk"}, follow_redirects=False)
    c.post("/api/v1/auth/dev-login", json={"email": "boss@example.com"})
    r = c.get("/api/v1/mailboxes/connect")
    assert r.status_code in (307, 400) and r.status_code != 422


def test_html_body_and_customer_name_are_captured(setup):
    db, mailbox, store, _ = setup
    do_ingest(db, mailbox, build_message("m1", from_name="Priya", body_html="<p>Hi</p>"))
    msg = db.query(Message).one()
    assert msg.body_html == "<p>Hi</p>"
    assert db.query(Customer).one().name == "Priya"
