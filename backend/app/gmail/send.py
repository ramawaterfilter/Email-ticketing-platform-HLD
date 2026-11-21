"""Send a reply through Gmail (LLD 4.5). A customer never receives the same reply twice."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ApiError
from ..models import Customer, Mailbox, Message, Store, SupportAddress, Ticket, TicketEvent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_raw(
    *, from_name: str, from_address: str, to: str, cc: list[str], subject: str, body: str, in_reply_to: str,
    references: str, request_id: str,
) -> str:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_address))
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()
    msg["X-Desk-Request-Id"] = request_id  # lets sync match a reply Gmail accepted but we failed to record
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _dispatch(db: Session, ticket: Ticket, mailbox: Mailbox, message: Message, resolve: bool, client) -> Message:
    """Build the MIME, send it, and move the ticket forward. Shared by first-send and retry."""
    raw = _build_raw(
        from_name=message.from_name,
        from_address=message.from_address,
        to=message.to_addresses[0] if message.to_addresses else "",
        cc=message.cc_addresses,
        subject=message.subject,
        body=message.body_text,
        in_reply_to=message.in_reply_to or "",
        references=message.references_header or "",
        request_id=message.client_request_id,
    )
    try:
        result = client.message_send(raw, ticket.gmail_thread_id)
    except Exception as exc:  # noqa: BLE001 - mark failed and let the UI offer Retry
        message.send_status = "failed"
        db.commit()
        raise ApiError(502, "send_failed", f"Could not send the email: {exc}") from exc

    message.gmail_message_id = result["id"]
    message.gmail_thread_id = result.get("threadId", ticket.gmail_thread_id)
    message.send_status = "sent"
    message.sent_at = _now()
    if mailbox.desk_label_id:
        try:
            client.message_add_label(result["id"], mailbox.desk_label_id)
            message.gmail_labelled_at = _now()
        except Exception:  # noqa: BLE001
            pass

    from ..tickets import sla

    _record_sent_for_feedback(db, ticket, message.body_text)
    ticket.last_agent_message_at = _now()
    if ticket.first_responded_at is None:
        ticket.first_responded_at = _now()
    if resolve:
        ticket.status = "resolved"
        ticket.resolved_at = _now()
    else:
        ticket.status = "waiting_customer"
    sla.after_agent_reply(db, ticket, resolved=resolve)  # clears reply clock, pauses/stops resolution
    db.add(TicketEvent(ticket_id=ticket.id, actor_user_id=message.author_user_id, type="replied", data={}))
    db.commit()
    return message


def send_reply(
    db: Session, ticket: Ticket, user_id: int, *, body_text: str, cc: list[str], client_request_id: str,
    last_seen_message_id: int | None, resolve: bool, client,
) -> Message:
    # Safe retry: the same request id never produces a second email.
    existing = db.scalar(select(Message).where(Message.client_request_id == client_request_id))
    if existing:
        return existing

    # Don't reply over a customer message the agent hasn't seen.
    newest_inbound = db.scalar(
        select(func.max(Message.id)).where(Message.ticket_id == ticket.id, Message.direction == "inbound")
    )
    if last_seen_message_id is not None and newest_inbound and newest_inbound > last_seen_message_id:
        raise ApiError(409, "stale", "A newer message arrived on this ticket; please review it before replying")

    address = db.get(SupportAddress, ticket.support_address_id) if ticket.support_address_id else None
    mailbox = db.get(Mailbox, ticket.mailbox_id)
    store = db.get(Store, ticket.store_id)
    customer = db.get(Customer, ticket.customer_id)

    from_address = address.address if address else mailbox.google_email
    from_name = (address.display_name if address and address.display_name else store.name)
    body = body_text if not store.signature_text else f"{body_text}\n\n{store.signature_text}"
    last_inbound = db.scalar(
        select(Message)
        .where(Message.ticket_id == ticket.id, Message.direction == "inbound")
        .order_by(Message.id.desc())
        .limit(1)
    )

    message = Message(
        ticket_id=ticket.id,
        mailbox_id=mailbox.id,
        direction="outbound",
        from_address=from_address,
        from_name=from_name,
        to_addresses=[customer.email],
        cc_addresses=cc,
        subject=_reply_subject(ticket.subject),
        body_text=body,
        in_reply_to=last_inbound.rfc_message_id if last_inbound else "",
        references_header=last_inbound.references_header if last_inbound else "",
        author_user_id=user_id,
        client_request_id=client_request_id,
        send_status="sending",
    )
    db.add(message)
    db.commit()  # the "sending" row exists before we call Gmail, so a crash is recoverable

    return _dispatch(db, ticket, mailbox, message, resolve, client)


def retry_reply(db: Session, ticket: Ticket, message: Message, client) -> Message:
    if message.send_status != "failed":
        raise ApiError(409, "not_retryable", "Only a failed reply can be retried")
    mailbox = db.get(Mailbox, ticket.mailbox_id)
    message.send_status = "sending"
    db.commit()
    return _dispatch(db, ticket, mailbox, message, resolve=(ticket.status == "resolved"), client=client)


def _record_sent_for_feedback(db: Session, ticket: Ticket, sent_body: str) -> None:
    """Save what the agent actually sent against the draft for the latest customer message,
    so suggestion quality can be measured (draft vs. final)."""
    from ..models import Suggestion

    last_inbound = db.scalar(
        select(Message.id)
        .where(Message.ticket_id == ticket.id, Message.direction == "inbound")
        .order_by(Message.id.desc())
        .limit(1)
    )
    if last_inbound is None:
        return
    suggestion = db.scalar(select(Suggestion).where(Suggestion.message_id == last_inbound))
    if suggestion is not None and suggestion.final_sent_text is None:
        suggestion.final_sent_text = sent_body


def _reply_subject(subject: str) -> str:
    subject = subject or "(no subject)"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"
