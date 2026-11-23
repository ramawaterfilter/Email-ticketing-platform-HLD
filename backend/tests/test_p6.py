"""Audit log: critical changes are recorded (with the actor) and never store secrets."""

import json


def test_critical_changes_are_logged_without_secrets(login, add_user):
    add_user("admin@example.com", role="admin")
    add_user("exec@example.com", role="executive")
    admin = login("admin@example.com")

    assert admin.post("/api/v1/stores", json={"name": "Audit Store"}).status_code == 201
    r = admin.put(
        "/api/v1/settings/llm",
        json={"provider": "openai", "model": "gpt", "base_url": "https://x/v1", "api_key": "sk-SECRET-123"},
    )
    assert r.status_code == 200, r.text

    rows = admin.get("/api/v1/audit").json()
    actions = {row["action"] for row in rows}
    assert "store.create" in actions
    assert "llm.settings" in actions
    assert any(row["actor_email"] == "admin@example.com" for row in rows)  # attributed to the actor
    # The API key value must never appear in any audit payload.
    assert "sk-SECRET-123" not in json.dumps(rows)
    llm_row = next(row for row in rows if row["action"] == "llm.settings")
    assert llm_row["data"]["api_key_changed"] is True  # only the fact of change is recorded


def test_audit_log_is_admin_only(login, add_user):
    add_user("exec@example.com", role="executive")
    exec_c = login("exec@example.com")
    assert exec_c.get("/api/v1/audit").status_code == 403
