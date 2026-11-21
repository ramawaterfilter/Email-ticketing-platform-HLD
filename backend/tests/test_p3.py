"""Phase 3: canned responses + reports."""

from datetime import datetime, timezone

from app.db import SessionLocal
from app.models import Customer, Ticket

from test_m2 import make_mailbox, make_store


def _ticket(db, store, mailbox, customer, **kw):
    t = Ticket(
        store_id=store.id, mailbox_id=mailbox.id, customer_id=customer.id,
        gmail_thread_id=f"t{kw.pop('n', 1)}", queue="customer", **kw,
    )
    db.add(t)
    db.commit()
    return t


# ---- canned responses ----


def test_canned_admin_crud_and_exec_read_only(login, add_user):
    add_user("admin@example.com", role="admin")
    add_user("exec@example.com", role="executive")
    with SessionLocal() as db:
        store = make_store(db)
        sid = store.id

    admin = login("admin@example.com")
    r = admin.post("/api/v1/canned", json={"title": "Greeting", "body_text": "Hi there!", "store_id": None})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert admin.post("/api/v1/canned", json={"title": "Store note", "body_text": "Store terms", "store_id": sid}).status_code == 201

    # An executive can read them (needed to insert while replying) but not create.
    exec_c = login("exec@example.com")
    assert len(exec_c.get("/api/v1/canned").json()) == 2
    assert exec_c.post("/api/v1/canned", json={"title": "x", "body_text": "y"}).status_code == 403

    # Edit + delete are admin-only.
    assert admin.patch(f"/api/v1/canned/{cid}", json={"is_active": False}).status_code == 200
    assert admin.delete(f"/api/v1/canned/{cid}").status_code == 204
    assert len(admin.get("/api/v1/canned").json()) == 1


def test_canned_rejects_empty_body(login, add_user):
    add_user("admin@example.com", role="admin")
    admin = login("admin@example.com")
    assert admin.post("/api/v1/canned", json={"title": "T", "body_text": ""}).status_code == 422


# ---- reports ----


def test_reports_counts_created_and_resolved(login, add_user):
    add_user("mgr@example.com", role="manager")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        store = make_store(db)
        mailbox = make_mailbox(db)
        cust = Customer(email="c@buyer.com")
        db.add(cust)
        db.commit()
        _ticket(db, store, mailbox, cust, n=1)
        _ticket(db, store, mailbox, cust, n=2, status="resolved", resolved_at=now, first_responded_at=now)

    r = login("mgr@example.com").get("/api/v1/reports?days=30").json()
    assert r["created"] == 2
    assert r["resolved"] == 1
    assert r["by_store"][0]["created"] == 2
    assert r["by_store"][0]["resolved"] == 1


def test_reports_forbidden_for_executive(login, add_user):
    add_user("exec@example.com", role="executive")
    assert login("exec@example.com").get("/api/v1/reports").status_code == 403
