import pytest

from app.auth import LoginRefused, resolve_login
from app.db import SessionLocal

BOSS = "boss@example.com"  # the bootstrap admin configured in conftest.py


def test_not_signed_in_is_401(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401 and r.json()["code"] == "not_signed_in"


def test_first_admin_signs_in_and_creates_a_store(login):
    admin = login(BOSS)
    assert admin.get("/api/v1/me").json()["role"] == "admin"

    r = admin.post("/api/v1/stores", json={"name": "Rama Pure", "signature_text": "Team Rama"})
    assert r.status_code == 201
    assert [s["name"] for s in admin.get("/api/v1/stores").json()] == ["Rama Pure"]

    dup = admin.post("/api/v1/stores", json={"name": "rama pure"})  # same name, different case
    assert dup.status_code == 409 and dup.json()["code"] == "store_exists"
    assert admin.post("/api/v1/stores", json={"name": "   "}).status_code == 422


@pytest.mark.parametrize("email", ["stranger@example.com", "boss@evil.com"])
def test_unknown_person_and_outside_domain_are_refused(client, email):
    assert client.post("/api/v1/auth/dev-login", json={"email": email}).status_code == 403
    assert client.get("/api/v1/me").status_code == 401


def test_sign_in_rules(add_user):
    add_user("gone@example.com", is_active=False)
    add_user("bound@example.com", google_sub="google-123")
    ok = dict(domain="example.com", verified=True)
    with SessionLocal() as db:
        with pytest.raises(LoginRefused, match="unverified"):
            resolve_login(db, email=BOSS, domain="example.com", verified=False)
        with pytest.raises(LoginRefused, match="domain"):
            resolve_login(db, email=BOSS, domain="", verified=True)  # personal Gmail has no hosted domain
        with pytest.raises(LoginRefused, match="not_allowed"):
            resolve_login(db, email="gone@example.com", **ok)
        with pytest.raises(LoginRefused, match="not_allowed"):
            resolve_login(db, email="bound@example.com", sub="other-google-account", **ok)
        user = resolve_login(db, email="Bound@Example.com", sub="google-123", name="Bina", **ok)
        assert user.email == "bound@example.com" and user.name == "Bina"


def test_writes_without_the_header_are_blocked(login):
    admin = login(BOSS)
    r = admin.post("/api/v1/stores", json={"name": "X"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403 and r.json()["code"] == "missing_header"


def test_executive_can_read_stores_but_not_administer(login, add_user):
    add_user("exec@example.com")
    ex = login("exec@example.com")
    assert ex.get("/api/v1/stores").status_code == 200
    assert ex.post("/api/v1/stores", json={"name": "X"}).status_code == 403
    assert ex.get("/api/v1/users").status_code == 403
    assert ex.post("/api/v1/users", json={"email": "a@example.com", "role": "admin"}).status_code == 403
    assert ex.patch("/api/v1/me", json={"is_available": False}).json()["is_available"] is False


def test_admin_adds_people_and_assigns_stores(login):
    admin = login(BOSS)
    store_id = admin.post("/api/v1/stores", json={"name": "Store A"}).json()["id"]

    r = admin.post("/api/v1/users", json={"email": "Asha@Example.com", "role": "executive", "store_ids": [store_id]})
    assert r.status_code == 201 and r.json()["email"] == "asha@example.com" and r.json()["store_ids"] == [store_id]
    user_id = r.json()["id"]

    post = lambda **body: admin.post("/api/v1/users", json={"role": "executive", **body})  # noqa: E731
    assert post(email="asha@example.com").json()["code"] == "user_exists"
    assert post(email="x@outside.com").json()["code"] == "domain_not_allowed"
    assert post(email="y@example.com", store_ids=[999]).json()["code"] == "unknown_store"
    assert post(email="z@example.com", role="owner").status_code == 422

    r = admin.patch(f"/api/v1/users/{user_id}", json={"role": "manager", "store_ids": []})
    assert r.json()["role"] == "manager" and r.json()["store_ids"] == []
    assert login("asha@example.com").get("/api/v1/users").status_code == 200  # managers may list people


def test_deactivated_person_is_signed_out_immediately(login, add_user):
    user_id = add_user("exec@example.com")
    ex, admin = login("exec@example.com"), login(BOSS)
    assert ex.get("/api/v1/me").status_code == 200
    admin.patch(f"/api/v1/users/{user_id}", json={"is_active": False})
    assert ex.get("/api/v1/me").status_code == 401


def test_admin_cannot_remove_own_access(login):
    admin = login(BOSS)
    me = admin.get("/api/v1/me").json()["id"]
    for change in ({"role": "executive"}, {"is_active": False}):
        r = admin.patch(f"/api/v1/users/{me}", json=change)
        assert r.status_code == 400 and r.json()["code"] == "self_lockout"
    assert admin.patch(f"/api/v1/users/{me}", json={"name": "Boss"}).status_code == 200


def test_dev_login_can_never_be_enabled_in_production():
    from app.config import Settings

    assert Settings(environment="prod", dev_login_enabled=True).dev_login is False
    assert Settings(environment="dev", dev_login_enabled=True).dev_login is True


def test_logout(login):
    admin = login(BOSS)
    assert admin.post("/api/v1/auth/logout").status_code == 204
    assert admin.get("/api/v1/me").status_code == 401
