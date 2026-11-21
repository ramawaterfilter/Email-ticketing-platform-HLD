"""In-memory stand-in for the Gmail API, with the same methods as RealGmailClient.

Lets every sync / ingest / reconcile path be tested without Google credentials.
"""

from __future__ import annotations

import base64
import itertools

from app.gmail.client import AuthRevoked, HistoryExpired


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def build_message(
    mid: str,
    *,
    thread_id: str | None = None,
    from_addr: str = "cust@buyer.com",
    from_name: str = "A Customer",
    to: str = "support@store.com",
    cc: str = "",
    delivered_to: str | None = None,
    subject: str = "Hello",
    body_text: str = "Where is my order?",
    body_html: str = "",
    labels: list[str] | None = None,
    extra_headers: dict[str, str] | None = None,
    content_type: str | None = None,
    internal_ms: int = 1_700_000_000_000,
) -> dict:
    headers = [
        {"name": "From", "value": f"{from_name} <{from_addr}>" if from_name else from_addr},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
        {"name": "Delivered-To", "value": delivered_to if delivered_to is not None else to},
        {"name": "Message-ID", "value": f"<{mid}@mail>"},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    for name, value in (extra_headers or {}).items():
        headers.append({"name": name, "value": value})

    if body_html:
        mime = "multipart/alternative"
        parts = [
            {"mimeType": "text/plain", "body": {"data": _b64(body_text)}},
            {"mimeType": "text/html", "body": {"data": _b64(body_html)}},
        ]
        payload = {"mimeType": mime, "headers": headers, "parts": parts}
    else:
        payload = {"mimeType": content_type or "text/plain", "headers": headers, "body": {"data": _b64(body_text)}}

    return {
        "id": mid,
        "threadId": thread_id or mid,
        "labelIds": list(labels or ["INBOX"]),
        "snippet": body_text[:50],
        "internalDate": str(internal_ms),
        "payload": payload,
    }


class FakeGmail:
    def __init__(self, email: str = "support@store.com", send_as: list[dict] | None = None):
        self.email = email
        self._messages: dict[str, dict] = {}
        self._hist = itertools.count(1)
        self._history_of: dict[str, int] = {}
        self.send_as = send_as or [{"sendAsEmail": email, "displayName": "Support", "isPrimary": True}]
        self.label_calls: list[str] = []
        self.sent: list[dict] = []
        # Flags tests can flip to simulate failure modes:
        self.expire_history = False
        self.auth_revoked = False
        self.fail_get_id: str | None = None

    def add(self, msg: dict) -> dict:
        self._messages[msg["id"]] = msg
        self._history_of[msg["id"]] = next(self._hist)
        return msg

    def _guard(self):
        if self.auth_revoked:
            raise AuthRevoked("token revoked")

    def history_list(self, start_history_id: str) -> list[str]:
        self._guard()
        if self.expire_history:
            raise HistoryExpired("cursor too old")
        start = int(start_history_id)
        return [mid for mid, h in sorted(self._history_of.items(), key=lambda kv: kv[1]) if h > start]

    def messages_list(self, query: str) -> list[str]:
        self._guard()
        spam = "in:spam" in query
        out = []
        for mid, msg in self._messages.items():
            labels = msg.get("labelIds", [])
            is_spam = "SPAM" in labels
            if {"DRAFT", "CHAT"} & set(labels):
                continue
            if spam == is_spam:
                out.append(mid)
        return out

    def message_get(self, message_id: str) -> dict:
        self._guard()
        if message_id == self.fail_get_id:
            raise RuntimeError("simulated fetch failure")
        return self._messages[message_id]

    def message_add_label(self, message_id: str, label_id: str) -> None:
        self._guard()
        self.label_calls.append(message_id)
        labels = self._messages[message_id].setdefault("labelIds", [])
        if label_id not in labels:
            labels.append(label_id)

    def attachment_get(self, message_id: str, attachment_id: str) -> bytes:
        return b"attachment-bytes"

    def label_ensure(self, name: str) -> str:
        return "LABEL_DESK"

    def profile_get(self) -> dict:
        self._guard()
        latest = max(self._history_of.values(), default=0)
        return {"emailAddress": self.email, "historyId": str(latest)}

    def send_as_list(self) -> list[dict]:
        return self.send_as

    def message_send(self, raw: str, thread_id: str | None) -> dict:
        self._guard()
        new_id = f"sent-{len(self.sent) + 1}"
        self.sent.append({"id": new_id, "raw": raw, "threadId": thread_id})
        return {"id": new_id, "threadId": thread_id or new_id}
