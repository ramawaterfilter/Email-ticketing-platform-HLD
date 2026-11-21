import pytest

from app.db import SessionLocal
from app.gmail.send import send_reply
from app.llm import SuggestionResult, build_prompt, generate_suggestion
from app.models import KbDocument, Message, Store, Suggestion, Ticket
from app.worker import run_suggestions_once

from fakes import FakeGmail, build_message
from test_m2 import do_ingest, make_address, make_exec, make_mailbox, make_store

BOSS = "boss@example.com"


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


class FakeSuggester:
    def __init__(self, result=None, raise_exc=None):
        self.result = result
        self.raise_exc = raise_exc
        self.last_system = None
        self.last_user = None

    def suggest(self, *, system, user_text):
        self.last_system = system
        self.last_user = user_text
        if self.raise_exc:
            raise self.raise_exc
        return self.result or SuggestionResult(
            category="order_status", priority="normal", sentiment="neutral", language="en",
            summary="Customer asks where their order is.", suggested_reply="Your order ships tomorrow.",
            missing_info=[],
        )


def seed(db):
    store = make_store(db)
    store.ai_guidance = "Be warm and concise. Offer free replacement within 1 year."
    mailbox = make_mailbox(db)
    make_address(db, mailbox, store, "support@store.com", default=True)
    db.commit()
    return store, mailbox


def seed_ticket(db):
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1", subject="Where is my order", body_text="No tracking yet"))
    return store, mailbox, db.query(Ticket).one()


def enable_llm(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")


# ---- context assembly ----


def test_prompt_includes_store_guidance_and_its_documents_only(db):
    store, mailbox, ticket = seed_ticket(db)
    other = make_store(db, "Other Store")
    db.add_all([
        KbDocument(store_id=store.id, title="Returns", body_text="Returns accepted within 30 days."),
        KbDocument(store_id=None, title="Product manual", body_text="The RO filter installs under the sink."),
        KbDocument(store_id=other.id, title="Other policy", body_text="SECRET OTHER STORE TERMS"),
    ])
    db.commit()

    system, user_text, meta = build_prompt(db, ticket, store)
    blob = "\n".join(b["text"] for b in system)
    assert "Be warm and concise" in blob
    assert "Returns accepted within 30 days" in blob
    assert "installs under the sink" in blob  # shared doc included
    assert "SECRET OTHER STORE" not in blob  # another store's doc never leaks
    assert {d["title"] for d in meta["documents"]} == {"Returns", "Product manual"}
    assert "Where is my order" in user_text


def test_prompt_notes_when_no_documents_on_file(db):
    store, mailbox, ticket = seed_ticket(db)
    system, _, meta = build_prompt(db, ticket, store)
    assert meta["documents"] == []
    assert "No store policy documents" in system[1]["text"]


# ---- suggestion generation ----


def test_generate_suggestion_fills_the_row(db):
    store, mailbox, ticket = seed_ticket(db)
    s = Suggestion(message_id=db.query(Message).one().id, ticket_id=ticket.id, store_id=store.id)
    db.add(s)
    db.commit()

    generate_suggestion(db, s, FakeSuggester())
    db.refresh(s)
    assert s.status == "ready"
    assert s.suggested_reply == "Your order ships tomorrow."
    assert s.category == "order_status" and s.priority == "normal"


def test_ai_priority_sets_ticket_priority(db):
    store, mailbox, ticket = seed_ticket(db)
    assert ticket.priority == "normal"
    s = Suggestion(message_id=db.query(Message).one().id, ticket_id=ticket.id, store_id=store.id)
    db.add(s)
    db.commit()

    result = SuggestionResult(category="leak", priority="urgent", sentiment="negative", language="en",
                              summary="leak", suggested_reply="we'll help", missing_info=[])
    generate_suggestion(db, s, FakeSuggester(result=result))
    db.refresh(ticket)
    assert ticket.priority == "urgent"
    assert any(e.type == "priority_changed" and e.data.get("by_ai") for e in ticket_events(db, ticket.id))


def test_ai_priority_does_not_override_a_manual_priority(db):
    store, mailbox, ticket = seed_ticket(db)
    ticket.priority = "low"  # an agent set it by hand
    db.commit()
    s = Suggestion(message_id=db.query(Message).one().id, ticket_id=ticket.id, store_id=store.id)
    db.add(s)
    db.commit()

    result = SuggestionResult(category="leak", priority="urgent", sentiment="negative", language="en",
                              summary="leak", suggested_reply="x", missing_info=[])
    generate_suggestion(db, s, FakeSuggester(result=result))
    db.refresh(ticket)
    assert ticket.priority == "low"  # untouched


def test_ai_priority_can_be_turned_off(db):
    from app.models import Setting

    db.merge(Setting(key="llm", value={"apply_priority": False}))
    db.commit()
    store, mailbox, ticket = seed_ticket(db)
    s = Suggestion(message_id=db.query(Message).one().id, ticket_id=ticket.id, store_id=store.id)
    db.add(s)
    db.commit()

    result = SuggestionResult(category="leak", priority="urgent", sentiment="negative", language="en",
                              summary="leak", suggested_reply="x", missing_info=[])
    generate_suggestion(db, s, FakeSuggester(result=result))
    db.refresh(ticket)
    assert ticket.priority == "normal"  # toggle off → not applied


def ticket_events(db, ticket_id):
    from app.models import TicketEvent

    return db.query(TicketEvent).filter_by(ticket_id=ticket_id).all()


def test_generate_suggestion_records_failure_without_raising(db):
    store, mailbox, ticket = seed_ticket(db)
    s = Suggestion(message_id=db.query(Message).one().id, ticket_id=ticket.id, store_id=store.id)
    db.add(s)
    db.commit()

    generate_suggestion(db, s, FakeSuggester(raise_exc=RuntimeError("model down")))
    db.refresh(s)
    assert s.status == "failed" and "model down" in s.error


# ---- enqueue + worker ----


def test_customer_message_enqueues_a_suggestion_when_llm_enabled(db, monkeypatch):
    enable_llm(monkeypatch)
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    assert db.query(Suggestion).filter_by(status="pending").count() == 1


def test_no_suggestion_when_llm_disabled(db):
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))
    assert db.query(Suggestion).count() == 0


def test_worker_processes_pending_suggestions(db, monkeypatch):
    enable_llm(monkeypatch)
    store, mailbox = seed(db)
    do_ingest(db, mailbox, build_message("m1", thread_id="t1"))

    processed = run_suggestions_once(factory=lambda: FakeSuggester())
    assert processed == 1
    assert db.query(Suggestion).filter_by(status="ready").count() == 1


# ---- API ----


def test_kb_crud_and_store_scope(login, db):
    store = make_store(db)
    admin = login(BOSS)
    r = admin.post("/api/v1/kb", json={"title": "Warranty", "store_id": store.id, "body_text": "1 year warranty."})
    assert r.status_code == 201 and r.json()["char_count"] == len("1 year warranty.")
    doc_id = r.json()["id"]

    assert admin.post("/api/v1/kb", json={"title": "x", "body_text": ""}).json()["code"] == "empty"
    assert admin.post("/api/v1/kb", json={"title": "x", "store_id": 999, "body_text": "y"}).json()["code"] == "unknown_store"
    assert len(admin.get("/api/v1/kb").json()) == 1
    assert admin.patch(f"/api/v1/kb/{doc_id}", json={"is_active": False}).json()["is_active"] is False
    assert admin.delete(f"/api/v1/kb/{doc_id}").status_code == 204
    assert admin.get("/api/v1/kb").json() == []


def test_executive_cannot_manage_kb(login, db):
    make_exec(db, "e1@example.com")
    ex = login("e1@example.com")
    assert ex.get("/api/v1/kb").status_code == 403
    assert ex.post("/api/v1/kb", json={"title": "x", "body_text": "y"}).status_code == 403


def test_suggestion_endpoint_and_rating(login, db):
    store, mailbox, ticket = seed_ticket(db)
    msg_id = db.query(Message).one().id
    s = Suggestion(message_id=msg_id, ticket_id=ticket.id, store_id=store.id, status="ready",
                   suggested_reply="Hi there", summary="asks about order")
    db.add(s)
    db.commit()

    admin = login(BOSS)
    got = admin.get(f"/api/v1/tickets/{ticket.id}/suggestion").json()
    assert got["suggested_reply"] == "Hi there" and got["status"] == "ready"

    rated = admin.post(f"/api/v1/tickets/{ticket.id}/suggestion/{s.id}/rating", json={"rating": 1})
    assert rated.status_code == 200 and rated.json()["rating"] == 1


def test_reply_records_what_was_sent_against_the_draft(db):
    store, mailbox, ticket = seed_ticket(db)
    msg_id = db.query(Message).one().id
    s = Suggestion(message_id=msg_id, ticket_id=ticket.id, store_id=store.id, status="ready",
                   suggested_reply="draft text")
    db.add(s)
    db.commit()
    agent = make_exec(db, "a@example.com")

    send_reply(db, ticket, agent.id, body_text="Edited final reply", cc=[], client_request_id="req-0001",
               last_seen_message_id=None, resolve=False, client=FakeGmail())
    db.refresh(s)
    assert s.final_sent_text == "Edited final reply"


def test_extract_plain_text():
    from app.llm import extract_text

    assert "hello world" in extract_text("notes.txt", "text/plain", b"hello world")
    assert "a,b,c" in extract_text("data.csv", "text/csv", b"a,b,c\n1,2,3")


def test_llm_provider_config_roundtrip_hides_key(login, db):
    from app.crypto import decrypt
    from app.models import Setting

    admin = login(BOSS)
    r = admin.put("/api/v1/settings/llm", json={"provider": "openai", "model": "gpt-4o",
                                                "base_url": "https://api.openai.com/v1", "api_key": "sk-secret"})
    assert r.status_code == 200
    b = r.json()
    assert b["provider"] == "openai" and b["model"] == "gpt-4o" and b["configured"] is True
    assert "api_key" not in b and "api_key_enc" not in b  # secret never exposed

    db.expire_all()
    assert decrypt(db.get(Setting, "llm").value["api_key_enc"]) == "sk-secret"

    # Saving again without a key keeps the stored one.
    r2 = admin.put("/api/v1/settings/llm", json={"provider": "openai", "model": "gpt-4o-mini"})
    assert r2.json()["configured"] is True and r2.json()["model"] == "gpt-4o-mini"
    # Empty key clears and disables.
    r3 = admin.put("/api/v1/settings/llm", json={"provider": "openai", "api_key": ""})
    assert r3.json()["configured"] is False


def test_build_suggester_selects_provider(db):
    from app.crypto import encrypt
    from app.llm import AnthropicSuggester, OpenAISuggester, build_suggester
    from app.models import Setting

    db.merge(Setting(key="llm", value={"provider": "openai", "model": "gpt-4o",
                                       "base_url": "https://x/v1", "api_key_enc": encrypt("k")}))
    db.commit()
    assert isinstance(build_suggester(db), OpenAISuggester)

    db.merge(Setting(key="llm", value={"provider": "anthropic", "model": "claude-opus-5-5",
                                       "api_key_enc": encrypt("k")}))
    db.commit()
    assert isinstance(build_suggester(db), AnthropicSuggester)


def test_is_enabled_is_off_without_config(db):
    from app.llm import is_enabled

    assert is_enabled(db) is False  # no DB config and no env key in tests


def test_extract_json_handles_fences_and_prose():
    from app.llm import _extract_json

    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Sure! {"a": 1, "b": "x"} done') == {"a": 1, "b": "x"}
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_openai_result_coercion_is_tolerant():
    import pytest as _pytest

    from app.llm import _coerce_result

    # A model that used "draft" and odd casing still parses.
    r = _coerce_result({"draft": "hello there", "priority": "URGENT", "sentiment": "Negative",
                        "category": "x", "missing_info": "order number"})
    assert r.suggested_reply == "hello there" and r.priority == "urgent" and r.sentiment == "negative"
    assert r.missing_info == ["order number"]
    # An unknown priority falls back to a safe default.
    r2 = _coerce_result({"reply": "hi", "priority": "P1"})
    assert r2.priority == "normal" and r2.language == "en"
    # No reply text at all is an error (the draft is the essential output).
    with _pytest.raises(ValueError):
        _coerce_result({"summary": "x"})


class FakeShopify:
    def __init__(self, orders):
        self.orders = orders
        self.asked = None

    def orders_for_email(self, email):
        self.asked = email
        return self.orders


def test_prompt_includes_shopify_orders(db):
    from app.llm import build_prompt

    store, mailbox, ticket = seed_ticket(db)
    orders = [{
        "name": "#4821", "created_at": "2026-11-18T00:00:00Z", "financial_status": "paid",
        "fulfillment_status": "fulfilled", "total_price": "2499", "currency": "INR",
        "line_items": [{"quantity": 1, "title": "RO Filter"}],
        "fulfillments": [{"tracking_number": "TRK123", "tracking_url": "http://track/TRK123"}],
        "email": "cust@buyer.com",
    }]
    fake = FakeShopify(orders)
    system, user_text, meta = build_prompt(db, ticket, store, fake)
    assert "#4821" in user_text and "TRK123" in user_text and "RO Filter" in user_text
    assert meta["orders"] == 1
    assert fake.asked == "cust@buyer.com"  # looked up by the sender's email only


def test_prompt_includes_similar_past_tickets(db):
    from app.gmail.send import send_reply
    from app.llm import build_prompt

    store, mailbox = seed(db)
    agent = make_exec(db, "a@example.com")
    # A resolved past ticket about a leaking filter, with the team's answer.
    do_ingest(db, mailbox, build_message("old", thread_id="old", subject="Filter leaking from base",
                                         body_text="water leaking"))
    old = db.query(Ticket).one()
    send_reply(db, old, agent.id, body_text="Please send a photo and we'll replace it.", cc=[],
               client_request_id="req-old01", last_seen_message_id=None, resolve=True, client=FakeGmail())

    # A new ticket on the same theme.
    do_ingest(db, mailbox, build_message("new", thread_id="new", subject="Filter leaking again",
                                         body_text="still leaks"))
    new = db.query(Ticket).filter(Ticket.gmail_thread_id == "new").one()
    _, user_text, meta = build_prompt(db, new, store)
    assert meta["past_tickets"] == 1
    assert "Please send a photo" in user_text


def test_generate_suggestion_passes_shopify(db):
    store, mailbox, ticket = seed_ticket(db)
    s = Suggestion(message_id=db.query(Message).one().id, ticket_id=ticket.id, store_id=store.id)
    db.add(s)
    db.commit()
    fake_shop = FakeShopify([{"name": "#99", "line_items": [], "email": "cust@buyer.com"}])
    generate_suggestion(db, s, FakeSuggester(), shopify_factory=lambda store: fake_shop)
    assert fake_shop.asked == "cust@buyer.com" and s.status == "ready"


def test_shopify_connect_roundtrip_hides_token(login, db):
    from app.crypto import decrypt

    store = make_store(db)
    admin = login(BOSS)
    r = admin.patch(f"/api/v1/stores/{store.id}",
                    json={"shopify_domain": "rama.myshopify.com", "shopify_token": "shpat_secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["shopify_domain"] == "rama.myshopify.com" and body["shopify_connected"] is True
    assert "shopify_token" not in body and "shopify_token_enc" not in body  # secret never exposed

    db.expire_all()
    assert decrypt(db.get(Store, store.id).shopify_token_enc) == "shpat_secret"  # stored encrypted

    # Clearing it disconnects.
    cleared = admin.patch(f"/api/v1/stores/{store.id}", json={"shopify_token": ""})
    assert cleared.json()["shopify_connected"] is False


def test_kb_upload_extracts_file_text(login, db):
    store = make_store(db)
    admin = login(BOSS)
    r = admin.post(
        "/api/v1/kb/upload",
        data={"title": "Shipping policy", "store_id": str(store.id)},
        files={"file": ("shipping.txt", b"We ship within 2 business days.", "text/plain")},
    )
    assert r.status_code == 201
    assert r.json()["filename"] == "shipping.txt" and r.json()["char_count"] > 0
    # empty / unreadable file is rejected
    bad = admin.post("/api/v1/kb/upload", data={"title": "x"}, files={"file": ("e.txt", b"   ", "text/plain")})
    assert bad.status_code == 400 and bad.json()["code"] == "no_text"
