"""Ingest one parsed Gmail message into a ticket (LLD 4.4). Runs in one transaction.

This is the core of the "never miss an email" guarantee: idempotent, and it never
discards a message - everything lands in some queue.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Attachment, Customer, Mailbox, Message, SupportAddress, Ticket, TicketEvent
from ..tickets.assignment import pick_assignee
from .mime import Parsed

REOPEN_WINDOW_DAYS = 30
BOUNCE_SENDERS = ("mailer-daemon", "postmaster")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def already_ingested(db: Session, mailbox_id: int, gmail_message_id: str) -> bool:
    return db.scalar(
        select(Message.id).where(
            Message.mailbox_id == mailbox_id, Message.gmail_message_id == gmail_message_id
        )
    ) is not None


def _resolve_address(db: Session, mailbox: Mailbox, parsed: Parsed) -> SupportAddress:
    """The store comes from the address the mail was delivered to (LLD 4.0)."""
    for addr in parsed.recipient_addresses():
        found = db.scalar(
            select(SupportAddress).where(
                SupportAddress.mailbox_id == mailbox.id, SupportAddress.address == addr
            )
        )
        if found:
            return found
    # No recipient matched (bcc, alias we don't know): fall back to the mailbox default.
    return db.scalar(
        select(SupportAddress).where(
            SupportAddress.mailbox_id == mailbox.id, SupportAddress.is_default.is_(True)
        )
    )


def _classify(parsed: Parsed, direction: str) -> tuple[str, bool, bool]:
    """Return (queue, is_auto, is_bounce). Outbound is never auto/bounce/other."""
    if direction != "inbound":
        return "customer", False, False
    if "SPAM" in parsed.label_ids:
        return "spam", False, False

    local = parsed.from_address.split("@", 1)[0]
    is_bounce = local in BOUNCE_SENDERS or parsed.content_type.startswith("multipart/report")
    if is_bounce:
        return "other", False, True

    h = parsed.headers
    is_auto = bool(
        (h.get("auto-submitted") and h["auto-submitted"].lower() != "no")
        or h.get("x-autoreply")
        or (h.get("precedence", "").lower() in ("bulk", "junk", "list", "auto_reply"))
        or h.get("list-id")
        or h.get("list-unsubscribe")
    )
    if is_auto:
        return "other", True, False
    return "customer", False, False


def _upsert_customer(db: Session, email: str, name: str) -> Customer:
    customer = db.scalar(select(Customer).where(Customer.email == email))
    if customer is None:
        customer = Customer(email=email, name=name)
        db.add(customer)
        db.flush()
    elif name and not customer.name:
        customer.name = name
    return customer


def _event(db: Session, ticket: Ticket, type_: str, **data) -> None:
    db.add(TicketEvent(ticket_id=ticket.id, actor_user_id=None, type=type_, data=data))


def ingest(db: Session, mailbox: Mailbox, parsed: Parsed) -> Message:
    """Idempotent: if this Gmail message is already stored, returns the existing row."""
    existing = db.scalar(
        select(Message).where(
            Message.mailbox_id == mailbox.id, Message.gmail_message_id == parsed.gmail_message_id
        )
    )
    if existing:
        return existing

    direction = "outbound" if "SENT" in parsed.label_ids else "inbound"
    address = _resolve_address(db, mailbox, parsed)
    queue, is_auto, is_bounce = _classify(parsed, direction)

    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.mailbox_id == mailbox.id, Ticket.gmail_thread_id == parsed.gmail_thread_id)
        .order_by(Ticket.id.desc())
        .limit(1)
    )

    if direction == "inbound":
        customer = _upsert_customer(db, parsed.from_address, parsed.from_name)
    else:
        # Outbound: the customer is the recipient, or the one already on the thread.
        recipient = parsed.to_addresses[0] if parsed.to_addresses else parsed.from_address
        customer = ticket.customer_id if ticket else None
        customer = db.get(Customer, customer) if customer else _upsert_customer(db, recipient, "")

    prior = ticket
    prior_status = prior.status if prior else None
    ticket = _find_or_create_ticket(db, mailbox, address, customer, parsed, ticket, queue)
    new_ticket = ticket is not prior
    message = _store_message(db, mailbox, ticket, parsed, direction, is_auto)

    if is_bounce and ticket.queue == "customer":
        _apply_bounce(db, ticket)
    elif direction == "inbound" and ticket.queue == "customer":
        _apply_inbound_customer(db, ticket, parsed, new_ticket, prior_status)
    elif direction == "outbound":
        _apply_outbound(db, ticket, parsed)

    db.flush()
    return message


def _find_or_create_ticket(db, mailbox, address, customer, parsed, ticket, queue) -> Ticket:
    if ticket is None:
        return _create_ticket(db, mailbox, address, customer, parsed, queue)

    if ticket.status == "closed":
        age_days = (_now() - ticket.closed_at).days if ticket.closed_at else 999
        if age_days > REOPEN_WINDOW_DAYS:
            new = _create_ticket(db, mailbox, address, customer, parsed, queue)
            new.previous_ticket_id = ticket.id
            return new
    return ticket


def _create_ticket(db, mailbox, address, customer, parsed, queue) -> Ticket:
    ticket = Ticket(
        store_id=address.store_id,
        mailbox_id=mailbox.id,
        support_address_id=address.id,
        customer_id=customer.id,
        gmail_thread_id=parsed.gmail_thread_id,
        subject=parsed.subject,
        queue=queue,
        status="new",
    )
    db.add(ticket)
    db.flush()
    _event(db, ticket, "created", queue=queue)
    return ticket


def _store_message(db, mailbox, ticket, parsed, direction, is_auto) -> Message:
    message = Message(
        ticket_id=ticket.id,
        mailbox_id=mailbox.id,
        direction=direction,
        gmail_message_id=parsed.gmail_message_id,
        gmail_thread_id=parsed.gmail_thread_id,
        rfc_message_id=parsed.rfc_message_id,
        in_reply_to=parsed.in_reply_to,
        references_header=parsed.references_header,
        from_address=parsed.from_address,
        from_name=parsed.from_name,
        to_addresses=parsed.to_addresses,
        cc_addresses=parsed.cc_addresses,
        subject=parsed.subject,
        body_text=parsed.body_text,
        body_html=parsed.body_html,
        snippet=parsed.snippet,
        headers=parsed.headers,
        is_auto=is_auto,
        send_status="sent" if direction == "outbound" else None,
        sent_at=parsed.internal_date if direction == "outbound" else None,
    )
    db.add(message)
    db.flush()
    for a in parsed.attachments:
        db.add(Attachment(message_id=message.id, **a))
    return message


def _apply_bounce(db, ticket: Ticket) -> None:
    """Our reply to this ticket bounced: flag it and pull it back into an executive's view."""
    ticket.delivery_failed = True
    if ticket.status in ("waiting_customer", "resolved", "closed"):
        ticket.status = "open"
        ticket.resolved_at = ticket.closed_at = None
    _event(db, ticket, "delivery_failed")
    # The assignee keeps the ticket; M4 adds the alert to them.


def _apply_inbound_customer(db, ticket: Ticket, parsed: Parsed, new_ticket: bool, prior_status: str | None) -> None:
    from ..tickets import sla

    ticket.last_customer_message_at = _now()

    if ticket.status in ("waiting_customer", "resolved", "closed"):
        _event(db, ticket, "reopened", from_status=ticket.status)
        ticket.status = "open"
        ticket.resolved_at = ticket.closed_at = None

    # Start/resume the SLA clocks for this customer message.
    sla.after_customer_message(db, ticket, new_ticket=new_ticket, prior_status=prior_status)

    if ticket.assignee_id is None:
        assignee = pick_assignee(db, ticket)
        if assignee:
            ticket.assignee_id = assignee.id
            assignee.last_assigned_at = _now()
            _event(db, ticket, "assigned", assignee_id=assignee.id, automatic=True)


def _apply_outbound(db, ticket: Ticket, parsed: Parsed) -> None:
    """An executive replied straight from Gmail: keep the ticket accurate (LLD 4.4 step 5)."""
    ticket.last_agent_message_at = _now()
    ticket.awaiting_reply_since = None
    if ticket.first_responded_at is None:
        ticket.first_responded_at = _now()
    if ticket.queue == "customer" and ticket.status in ("new", "open"):
        ticket.status = "waiting_customer"
