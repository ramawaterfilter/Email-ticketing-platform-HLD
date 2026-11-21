"""Turn a Gmail API `format=full` message dict into the flat shape ingest needs."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

# Only the headers classification and threading actually use are kept (LLD 3.2).
KEPT_HEADERS = (
    "message-id",
    "in-reply-to",
    "references",
    "auto-submitted",
    "x-autoreply",
    "precedence",
    "list-id",
    "list-unsubscribe",
    "return-path",
    "x-desk-request-id",
)


@dataclass
class Parsed:
    gmail_message_id: str
    gmail_thread_id: str
    label_ids: list[str]
    from_address: str
    from_name: str
    to_addresses: list[str]
    cc_addresses: list[str]
    delivered_to: list[str]
    subject: str
    body_text: str
    body_html: str
    snippet: str
    rfc_message_id: str
    in_reply_to: str
    references_header: str
    content_type: str
    internal_date: datetime | None
    headers: dict[str, str]
    attachments: list[dict] = field(default_factory=list)

    def recipient_addresses(self) -> list[str]:
        """Addresses the mail was delivered to, in the order store resolution checks them."""
        return [*self.delivered_to, *self.to_addresses, *self.cc_addresses]


def _headers(payload: dict) -> dict[str, str]:
    # Gmail allows repeated headers; last wins here, which is fine for the ones we keep.
    return {h["name"].lower(): h.get("value", "") for h in payload.get("headers", [])}


def _decode(body: dict) -> str:
    data = body.get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _walk(part: dict, out: dict, attachments: list[dict]) -> None:
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    filename = part.get("filename") or ""
    headers = _headers(part)

    if filename or body.get("attachmentId"):
        attachments.append(
            {
                "filename": filename,
                "mime_type": mime,
                "size_bytes": body.get("size", 0) or 0,
                "gmail_attachment_id": body.get("attachmentId"),
                "content_id": (headers.get("content-id") or "").strip("<>") or None,
            }
        )
        return
    if mime == "text/plain" and "text" not in out:
        out["text"] = _decode(body)
    elif mime == "text/html" and "html" not in out:
        out["html"] = _decode(body)
    for sub in part.get("parts", []):
        _walk(sub, out, attachments)


def parse_message(msg: dict) -> Parsed:
    payload = msg.get("payload", {})
    h = _headers(payload)
    bodies: dict[str, str] = {}
    attachments: list[dict] = []
    _walk(payload, bodies, attachments)

    from_name, from_addr = parseaddr(h.get("from", ""))
    internal = msg.get("internalDate")

    return Parsed(
        gmail_message_id=msg["id"],
        gmail_thread_id=msg.get("threadId", msg["id"]),
        label_ids=msg.get("labelIds", []),
        from_address=from_addr.lower(),
        from_name=from_name,
        to_addresses=_addrs(h.get("to", "")),
        cc_addresses=_addrs(h.get("cc", "")),
        delivered_to=_addrs(h.get("delivered-to", "")),
        subject=h.get("subject", ""),
        body_text=bodies.get("text", ""),
        body_html=bodies.get("html", ""),
        snippet=msg.get("snippet", ""),
        rfc_message_id=h.get("message-id", ""),
        in_reply_to=h.get("in-reply-to", ""),
        references_header=h.get("references", ""),
        content_type=payload.get("mimeType", ""),
        internal_date=datetime.fromtimestamp(int(internal) / 1000, timezone.utc) if internal else None,
        headers={k: v for k, v in h.items() if k in KEPT_HEADERS},
        attachments=attachments,
    )


def _addrs(value: str) -> list[str]:
    return [addr.lower() for _, addr in getaddresses([value]) if addr]
