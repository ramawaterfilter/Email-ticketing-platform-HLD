"""Operational alerts: email (via Gmail) and an optional Google Chat webhook (LLD 5.4).

Delivery is best-effort - an alert that fails to send must never crash the worker loop.
The worker decides *who* and *when*; this module only delivers.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.request
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .gmail.client import client_for
from .models import AppUser, Mailbox, Setting

log = logging.getLogger("alerts")


def manager_recipients(db: Session) -> list[str]:
    """Managers and admins, plus any extra addresses configured on the alerts setting."""
    roled = db.scalars(
        select(AppUser.email).where(AppUser.role.in_(("manager", "admin")), AppUser.is_active.is_(True))
    ).all()
    row = db.get(Setting, "alerts")
    extra = (row.value or {}).get("manager_emails", []) if row else []
    return sorted({*roled, *extra})


def admin_recipients(db: Session) -> list[str]:
    return db.scalars(
        select(AppUser.email).where(AppUser.role == "admin", AppUser.is_active.is_(True))
    ).all()


def notify(db: Session, emails: list[str], subject: str, body: str, factory=client_for) -> None:
    _email(db, emails, subject, body, factory)
    _chat(db, f"*{subject}*\n{body}")


def _email(db: Session, emails: list[str], subject: str, body: str, factory) -> None:
    if not emails:
        return
    mailbox = db.scalar(select(Mailbox).where(Mailbox.status == "active").order_by(Mailbox.id))
    if mailbox is None:
        log.warning("no active mailbox to send alert from: %s", subject)
        return
    try:
        client = factory(mailbox)
        for addr in emails:
            msg = EmailMessage()
            msg["From"] = mailbox.google_email
            msg["To"] = addr
            msg["Subject"] = subject
            msg.set_content(body)
            client.message_send(base64.urlsafe_b64encode(msg.as_bytes()).decode(), None)
    except Exception:  # noqa: BLE001
        log.exception("alert email failed: %s", subject)


def _chat(db: Session, text: str) -> None:
    row = db.get(Setting, "alerts")
    webhook = (row.value or {}).get("google_chat_webhook") if row else None
    if not webhook:
        return
    try:
        req = urllib.request.Request(
            webhook, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - admin-configured URL
    except Exception:  # noqa: BLE001
        log.exception("alert chat webhook failed")
