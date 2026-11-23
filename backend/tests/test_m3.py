import pytest

from app.db import SessionLocal
from app.gmail.send import retry_reply, send_reply
from app.main import app
from app.models import AppUser, Message, Ticket

from fakes import FakeGmail, build_message
from test_m2 import do_ingest, make_address, make_exec, make_mailbox, make_store

BOSS = "boss@example.com"


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def desk(db):
    """A store, active mailbox, and one open ticket from an inbound customer email."""
    store = make_store(db)
    store.signature_text = "Regards,\nRama Support"
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1", from_addr="cust@buyer.com", subject="Help me"))
    ticket = db.query(Ticket).one()
    agent = make_exec(db, "agent@example.com")
    return db, store, mailbox, ticket, agent


@pytest.fixture
def gmail():
    """Override the ticket API's Gmail factory with a fake for the duration of a test."""
    from app.api.tickets import gmail_factory

    fake = FakeGmail()
    app.dependency_overrides[gmail_factory] = lambda: (lambda mailbox: fake)
    yield fake
    app.dependency_overrides.pop(gmail_factory, None)


def reply_args(**over):
    base = dict(
        body_text="Your order ships tomorrow.",
        cc=[],
        client_request_id="req-00000001",
        last_seen_message_id=None,
        resolve=False,
    )
    base.update(over)
    return base


# ---- reply sender (unit) ----


def test_reply_sends_one_email_and_moves_ticket_to_waiting(desk):
    db, store, mailbox, ticket, agent = desk
    fake = FakeGmail()
    msg = send_reply(db, ticket, agent.id, client=fake, **reply_args())

    assert len(fake.sent) == 1
    db.refresh(ticket)
    assert ticket.status == "waiting_customer"
    assert ticket.first_responded_at is not None and ticket.awaiting_reply_since is None
    assert msg.send_status == "sent" and msg.subject == "Re: Help me"
    assert store.signature_text in msg.body_text  # signature appended


def test_safe_mode_blocks_customer_reply_but_not_alerts(desk):
    from app.errors import ApiError
    from app.models import Setting

    db, store, mailbox, ticket, agent = desk
    db.add(Setting(key="alerts", value={"manager_emails": [], "block_customer_email": True}))
    db.commit()

    fake = FakeGmail()
    with pytest.raises(ApiError) as exc:
        send_reply(db, ticket, agent.id, client=fake, **reply_args())
    assert exc.value.status == 403 and exc.value.code == "sending_disabled"
    assert fake.sent == []  # nothing was emailed to the customer
    assert db.query(Message).filter_by(direction="outbound").count() == 0  # and no outbound row created

    # Internal alerts must still be deliverable in safe mode.
    from app import alerts

    alerts.notify(db, ["manager@example.com"], "Test alert", "body", factory=lambda _m: fake)
    assert len(fake.sent) == 1  # the alert went out even though customer replies are blocked


def test_mute_alert_emails_suppresses_internal_alerts(desk):
    from app import alerts
    from app.models import Setting

    db, store, mailbox, ticket, agent = desk
    db.add(Setting(key="alerts", value={"mute_alert_emails": True}))
    db.commit()

    fake = FakeGmail()
    alerts.notify(db, ["manager@example.com"], "SLA breach", "body", factory=lambda _m: fake)
    assert fake.sent == []  # alert emails are turned off


def test_reply_is_idempotent_on_client_request_id(desk):
    db, store, mailbox, ticket, agent = desk
    fake = FakeGmail()
    send_reply(db, ticket, agent.id, client=fake, **reply_args())
    send_reply(db, ticket, agent.id, client=fake, **reply_args())  # same request id (e.g. a retry/double-click)
    assert len(fake.sent) == 1
    assert db.query(Message).filter_by(direction="outbound").count() == 1


def test_reply_is_blocked_when_a_newer_message_arrived(desk):
    from app.errors import ApiError

    db, store, mailbox, ticket, agent = desk
    seen = db.query(Message).filter_by(direction="inbound").one().id
    do_ingest(db, mailbox, build_message("m2", thread_id="t1", from_addr="cust@buyer.com"))  # customer wrote again

    with pytest.raises(ApiError) as exc:
        send_reply(db, ticket, agent.id, client=FakeGmail(), **reply_args(last_seen_message_id=seen))
    assert exc.value.status == 409


def test_reply_then_resolve_sets_resolved(desk):
    db, store, mailbox, ticket, agent = desk
    send_reply(db, ticket, agent.id, client=FakeGmail(), **reply_args(resolve=True))
    db.refresh(ticket)
    assert ticket.status == "resolved" and ticket.resolved_at is not None


def test_reply_includes_in_reply_to_from_the_customer_message(desk):
    db, store, mailbox, ticket, agent = desk
    fake = FakeGmail()
    send_reply(db, ticket, agent.id, client=fake, **reply_args())
    import base64

    raw = base64.urlsafe_b64decode(fake.sent[0]["raw"]).decode()
    assert "In-Reply-To: <m1@mail>" in raw
    assert "X-Desk-Request-Id: req-00000001" in raw


def test_failed_send_can_be_retried(desk):
    from app.errors import ApiError

    db, store, mailbox, ticket, agent = desk
    broken = FakeGmail()
    broken.auth_revoked = True  # message_send raises
    with pytest.raises(ApiError):
        send_reply(db, ticket, agent.id, client=broken, **reply_args())
    failed = db.query(Message).filter_by(direction="outbound").one()
    assert failed.send_status == "failed"

    retry_reply(db, ticket, failed, FakeGmail())
    db.refresh(failed)
    assert failed.send_status == "sent"


# ---- ticket API ----


def seed_ticket(db) -> Ticket:
    store = make_store(db)
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1", subject="Where is my order"))
    return db.query(Ticket).one()


def test_inbox_list_and_filters(login, db):
    ticket = seed_ticket(db)
    admin = login(BOSS)
    res = admin.get("/api/v1/tickets").json()
    assert res["items"][0]["number"] == ticket.number
    assert res["items"][0]["store_name"] == "Store A"
    # unassigned view (no executives exist, so it is unassigned)
    assert len(admin.get("/api/v1/tickets?view=unassigned").json()["items"]) == 1
    assert admin.get("/api/v1/tickets?view=mine").json()["items"] == []
    assert len(admin.get("/api/v1/tickets?q=order").json()["items"]) == 1
    assert admin.get("/api/v1/tickets?queue=spam").json()["items"] == []


def test_ticket_detail_shows_thread_and_customer(login, db):
    ticket = seed_ticket(db)
    admin = login(BOSS)
    detail = admin.get(f"/api/v1/tickets/{ticket.id}").json()
    assert detail["customer_email"] == "cust@buyer.com"
    assert len(detail["messages"]) == 1 and detail["messages"][0]["direction"] == "inbound"


def test_notes_are_internal_and_not_emailed(login, db):
    ticket = seed_ticket(db)
    admin = login(BOSS)
    r = admin.post(f"/api/v1/tickets/{ticket.id}/notes", json={"body_text": "called the warehouse"})
    assert r.status_code == 201 and r.json()["direction"] == "note"
    detail = admin.get(f"/api/v1/tickets/{ticket.id}").json()
    assert sum(m["direction"] == "note" for m in detail["messages"]) == 1


def test_assign_to_me_and_status_change(login, db):
    ticket = seed_ticket(db)
    admin = login(BOSS)
    me = admin.get("/api/v1/me").json()["id"]
    r = admin.patch(f"/api/v1/tickets/{ticket.id}", json={"assignee_id": me, "status": "open", "priority": "high"})
    assert r.status_code == 200
    body = r.json()
    assert body["assignee_id"] == me and body["status"] == "open" and body["priority"] == "high"


def test_executive_cannot_assign_to_someone_else(login, db):
    ticket = seed_ticket(db)
    exec_id = make_exec(db, "e1@example.com").id
    other_id = make_exec(db, "e2@example.com").id
    ex = login("e1@example.com")
    r = ex.patch(f"/api/v1/tickets/{ticket.id}", json={"assignee_id": other_id})
    assert r.status_code == 403
    assert ex.patch(f"/api/v1/tickets/{ticket.id}", json={"assignee_id": exec_id}).status_code == 200


def test_executive_restricted_to_other_store_gets_404(login, db):
    ticket = seed_ticket(db)
    other = make_store(db, "Other")
    restricted = make_exec(db, "only-other@example.com")
    restricted.stores = [other]
    db.commit()
    ex = login("only-other@example.com")
    assert ex.get(f"/api/v1/tickets/{ticket.id}").status_code == 404
    assert ex.get("/api/v1/tickets").json()["items"] == []


def test_reply_through_the_api_sends_via_gmail(login, db, gmail):
    ticket = seed_ticket(db)
    admin = login(BOSS)
    r = admin.post(
        f"/api/v1/tickets/{ticket.id}/replies",
        json={"body_text": "On its way.", "client_request_id": "req-abcdef12", "then": "resolve"},
    )
    assert r.status_code == 201 and r.json()["send_status"] == "sent"
    assert len(gmail.sent) == 1
    assert admin.get(f"/api/v1/tickets/{ticket.id}").json()["status"] == "resolved"


def test_pagination_cursor_walks_all_tickets(login, db):
    store = make_store(db)
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    for i in range(5):
        do_ingest(db, mailbox, build_message(f"m{i}", thread_id=f"t{i}"))
    admin = login(BOSS)

    first = admin.get("/api/v1/tickets?limit=2").json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = admin.get(f"/api/v1/tickets?limit=2&cursor={first['next_cursor']}").json()
    assert len(second["items"]) == 2
    ids = {t["id"] for t in first["items"]} | {t["id"] for t in second["items"]}
    assert len(ids) == 4  # no overlap between pages
