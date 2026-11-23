"""Phase 3+: import/refresh a knowledge-base document from a URL."""

import pytest

from app.db import SessionLocal
from app.errors import ApiError
from app.webfetch import _validate, html_to_text

from test_m2 import make_store


def test_html_to_text_strips_tags_scripts_and_reads_title():
    title, text = html_to_text(
        "<html><head><title>Returns</title><style>.x{color:red}</style></head>"
        "<body><h1>Return policy</h1><p>Refunds within 30 days.</p><script>steal()</script></body></html>"
    )
    assert title == "Returns"
    assert "Return policy" in text
    assert "Refunds within 30 days." in text
    assert "steal()" not in text and "color:red" not in text


@pytest.mark.parametrize(
    "url",
    ["http://localhost/x", "http://127.0.0.1/", "http://192.168.0.5/", "http://169.254.1.1/", "ftp://example.com/"],
)
def test_validate_blocks_internal_hosts_and_bad_schemes(url):
    with pytest.raises(ApiError):
        _validate(url)


def test_import_from_url_then_refresh(login, add_user, monkeypatch):
    add_user("admin@example.com", role="admin")
    import app.api.kb as kb

    pages = iter([("Page title", "first body"), (None, "updated body text")])
    monkeypatch.setattr(kb, "fetch_url_text", lambda _url: next(pages))

    with SessionLocal() as db:
        sid = make_store(db).id

    c = login("admin@example.com")
    created = c.post("/api/v1/kb/from-url", json={"url": "https://shop.example/policy", "store_id": sid})
    assert created.status_code == 201, created.text
    doc = created.json()
    assert doc["source_url"] == "https://shop.example/policy"
    assert doc["title"] == "Page title"
    assert doc["char_count"] == len("first body")

    refreshed = c.post(f"/api/v1/kb/{doc['id']}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["char_count"] == len("updated body text")


def test_refresh_requires_a_source_url(login, add_user):
    add_user("admin@example.com", role="admin")
    c = login("admin@example.com")
    doc = c.post("/api/v1/kb", json={"title": "Manual", "body_text": "hello"}).json()
    assert c.post(f"/api/v1/kb/{doc['id']}/refresh").status_code == 400


def test_import_from_url_is_admin_only(login, add_user, monkeypatch):
    add_user("exec@example.com", role="executive")
    import app.api.kb as kb

    monkeypatch.setattr(kb, "fetch_url_text", lambda _url: ("T", "body"))
    c = login("exec@example.com")
    assert c.post("/api/v1/kb/from-url", json={"url": "https://x.example/p"}).status_code == 403


def test_kb_is_readable_by_staff_but_writable_only_by_admin(login, add_user):
    add_user("admin@example.com", role="admin")
    add_user("exec@example.com", role="executive")
    admin = login("admin@example.com")
    doc = admin.post("/api/v1/kb", json={"title": "Returns", "body_text": "30 day returns."}).json()
    did = doc["id"]

    exec_c = login("exec@example.com")
    assert len(exec_c.get("/api/v1/kb").json()) == 1  # non-admins can see the knowledge base
    # ...but every write is refused for them.
    assert exec_c.post("/api/v1/kb", json={"title": "x", "body_text": "y"}).status_code == 403
    assert exec_c.patch(f"/api/v1/kb/{did}", json={"store_id": None}).status_code == 403
    assert exec_c.post("/api/v1/kb/reindex").status_code == 403
    assert exec_c.delete(f"/api/v1/kb/{did}").status_code == 403
    # Admin still can.
    assert admin.patch(f"/api/v1/kb/{did}", json={"is_active": False}).status_code == 200
