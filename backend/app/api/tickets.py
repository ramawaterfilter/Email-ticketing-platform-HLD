"""The ticket desk: inbox list, ticket view, edits, notes, and replies (LLD 6.2)."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..errors import ApiError
from ..gmail.client import client_for
from ..gmail.send import retry_reply, send_reply
from ..models import OPEN_STATUSES, AppUser, Mailbox, Message, Suggestion, Ticket, TicketEvent
from ..schemas import (
    EventOut,
    LinkedTicket,
    MessageOut,
    NoteIn,
    RatingIn,
    ReplyIn,
    SuggestionOut,
    TicketDetail,
    TicketList,
    TicketListItem,
    TicketPatch,
)

router = APIRouter(tags=["tickets"])

PAGE = 50


def gmail_factory():
    """Overridable in tests; returns the function that builds a Gmail client for a mailbox."""
    return client_for


def _store_scope(user: AppUser):
    """Executives attached to specific stores see only those; everyone else sees all."""
    if user.role == "executive" and user.store_ids:
        return Ticket.store_id.in_(user.store_ids)
    return None


def _visible(db: Session, user: AppUser, ticket_id: int) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    scope = _store_scope(user)
    if ticket is None or (scope is not None and ticket.store_id not in user.store_ids):
        raise ApiError(404, "not_found", "Ticket not found")
    return ticket


def _item(t: Ticket) -> TicketListItem:
    return TicketListItem(
        id=t.id,
        number=t.number,
        subject=t.subject,
        queue=t.queue,
        status=t.status,
        priority=t.priority,
        store_id=t.store_id,
        store_name=t.store.name,
        assignee_id=t.assignee_id,
        assignee_name=(t.assignee.name or t.assignee.email) if t.assignee else None,
        customer_email=t.customer.email,
        customer_name=t.customer.name,
        tags=list(t.tags or []),
        delivery_failed=t.delivery_failed,
        breached=bool(t.reply_breached_at or t.resolution_breached_at),
        awaiting_reply_since=t.awaiting_reply_since,
        reply_due_at=t.reply_due_at,
        last_customer_message_at=t.last_customer_message_at,
        updated_at=t.updated_at,
    )


def _encode_cursor(t: Ticket) -> str:
    return base64.urlsafe_b64encode(f"{t.updated_at.timestamp()}:{t.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        ts, tid = base64.urlsafe_b64decode(cursor.encode()).decode().split(":")
        return datetime.fromtimestamp(float(ts), timezone.utc), int(tid)
    except (ValueError, binascii.Error) as exc:
        raise ApiError(400, "bad_cursor", "Invalid pagination cursor") from exc


@router.get("/tickets", response_model=TicketList)
def list_tickets(
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    view: str = Query("all", pattern="^(mine|unassigned|all)$"),
    queue: str = Query("customer", pattern="^(customer|other|spam)$"),
    status: str | None = None,
    store_id: int | None = None,
    assignee_id: int | None = None,
    sla: str | None = Query(None, pattern="^(due_soon|breached)$"),
    q: str | None = None,
    cursor: str | None = None,
    limit: int = Query(PAGE, ge=1, le=100),
):
    from sqlalchemy import or_

    query = select(Ticket).where(Ticket.queue == queue)
    scope = _store_scope(user)
    if scope is not None:
        query = query.where(scope)
    if view == "mine":
        query = query.where(Ticket.assignee_id == user.id)
    elif view == "unassigned":
        query = query.where(Ticket.assignee_id.is_(None))
    if status:
        query = query.where(Ticket.status == status)
    if store_id:
        query = query.where(Ticket.store_id == store_id)
    if assignee_id:
        query = query.where(Ticket.assignee_id == assignee_id)
    if sla == "breached":
        query = query.where(or_(Ticket.reply_breached_at.is_not(None), Ticket.resolution_breached_at.is_not(None)))
    elif sla == "due_soon":
        soon = datetime.now(timezone.utc) + timedelta(minutes=60)
        query = query.where(
            Ticket.reply_breached_at.is_(None), Ticket.reply_due_at.is_not(None), Ticket.reply_due_at <= soon
        )
    if q:
        query = _apply_search(query, q)

    # Keyset pagination: most recently updated first, stable on (updated_at, id).
    query = query.order_by(Ticket.updated_at.desc(), Ticket.id.desc())
    if cursor:
        cur_time, cur_id = _decode_cursor(cursor)
        query = query.where(tuple_(Ticket.updated_at, Ticket.id) < (cur_time, cur_id))

    rows = db.scalars(query.limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return TicketList(
        items=[_item(t) for t in rows],
        next_cursor=_encode_cursor(rows[-1]) if has_more and rows else None,
    )


def _apply_search(query, q: str):
    from ..models import Customer

    q = q.strip()
    conds = [Ticket.subject.ilike(f"%{q}%")]
    digits = q.lstrip("#")
    if digits.isdigit():
        conds.append(Ticket.number == int(digits))
    query = query.join(Customer, Ticket.customer_id == Customer.id)
    conds.append(Customer.email.ilike(f"%{q}%"))
    return query.where(or_(*conds))


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), user: AppUser = Depends(current_user)):
    t = _visible(db, user, ticket_id)
    messages = db.scalars(
        select(Message).where(Message.ticket_id == t.id, Message.direction != "note").order_by(Message.id)
    ).all()
    notes = db.scalars(
        select(Message).where(Message.ticket_id == t.id, Message.direction == "note").order_by(Message.id)
    ).all()
    events = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == t.id).order_by(TicketEvent.id)).all()
    other = db.scalars(
        select(Ticket)
        .where(
            Ticket.customer_id == t.customer_id,
            Ticket.id != t.id,
            Ticket.status.in_(OPEN_STATUSES),
        )
        .order_by(Ticket.id.desc())
        .limit(10)
    ).all()

    detail = TicketDetail(
        **_item(t).model_dump(),
        mailbox_id=t.mailbox_id,
        support_address_id=t.support_address_id,
        gmail_thread_id=t.gmail_thread_id,
        created_at=t.created_at,
        first_responded_at=t.first_responded_at,
        messages=[MessageOut.model_validate(m) for m in sorted([*messages, *notes], key=lambda m: m.id)],
        events=[EventOut.model_validate(e) for e in events],
        other_open_tickets=[LinkedTicket.model_validate(o) for o in other],
    )
    return detail


@router.patch("/tickets/{ticket_id}", response_model=TicketDetail)
def update_ticket(
    ticket_id: int, body: TicketPatch, db: Session = Depends(get_db), user: AppUser = Depends(current_user)
):
    t = _visible(db, user, ticket_id)
    data = body.model_dump(exclude_unset=True)

    if body.clear_assignee:
        _reassign(db, t, None, user)
    elif "assignee_id" in data and data["assignee_id"] is not None:
        _reassign(db, t, data["assignee_id"], user)

    if "status" in data and data["status"] != t.status:
        _set_status(db, t, data["status"], user)
    for field in ("priority", "queue", "tags"):
        if field in data and data[field] is not None:
            setattr(t, field, data[field])
    if "store_id" in data and data["store_id"] is not None:
        _change_store(db, t, data["store_id"], user)

    db.commit()
    return get_ticket(ticket_id, db, user)


def _reassign(db: Session, t: Ticket, new_assignee_id: int | None, user: AppUser) -> None:
    # Executives may only take a ticket themselves; managers/admins may assign to anyone.
    if new_assignee_id != user.id and user.role == "executive":
        raise ApiError(403, "forbidden", "Only a manager can assign tickets to other people")
    if new_assignee_id is not None:
        target = db.get(AppUser, new_assignee_id)
        if target is None or not target.is_active:
            raise ApiError(400, "bad_assignee", "That person cannot be assigned tickets")
    t.assignee_id = new_assignee_id
    db.add(TicketEvent(ticket_id=t.id, actor_user_id=user.id, type="assigned", data={"assignee_id": new_assignee_id}))


def _set_status(db: Session, t: Ticket, status: str, user: AppUser) -> None:
    from ..tickets import sla

    now = datetime.now(timezone.utc)
    prior = t.status
    t.status = status
    if status == "resolved":
        t.resolved_at = now
    elif status == "closed":
        t.closed_at = now
    elif status in ("open", "waiting_customer"):
        t.resolved_at = t.closed_at = None
    sla.after_status_change(db, t, new_status=status, prior_status=prior)
    db.add(TicketEvent(ticket_id=t.id, actor_user_id=user.id, type="status_changed", data={"status": status}))


def _change_store(db: Session, t: Ticket, store_id: int, user: AppUser) -> None:
    from ..models import Store

    if db.get(Store, store_id) is None:
        raise ApiError(400, "unknown_store", "That store does not exist")
    old = t.store_id
    t.store_id = store_id
    db.add(TicketEvent(ticket_id=t.id, actor_user_id=user.id, type="store_changed", data={"from": old, "to": store_id}))


@router.get("/tickets/{ticket_id}/suggestion", response_model=SuggestionOut | None)
def get_suggestion(ticket_id: int, db: Session = Depends(get_db), user: AppUser = Depends(current_user)):
    """The LLM draft for the newest customer message on this ticket.

    On-demand: if the ticket has no draft yet and the AI is enabled, opening it
    queues one here (the worker fills it in; the client polls while it is pending).
    New inbound mail already queues a draft at ingest, so this only backfills the
    tickets an agent actually opens.
    """
    from sqlalchemy.exc import IntegrityError

    from ..llm import is_enabled

    t = _visible(db, user, ticket_id)
    last_inbound = db.scalar(
        select(Message.id)
        .where(Message.ticket_id == t.id, Message.direction == "inbound")
        .order_by(Message.id.desc())
        .limit(1)
    )
    if last_inbound is None:
        return None
    existing = db.scalar(select(Suggestion).where(Suggestion.message_id == last_inbound))
    if existing is not None or not is_enabled(db):
        return existing
    suggestion = Suggestion(message_id=last_inbound, ticket_id=t.id, store_id=t.store_id, status="pending")
    db.add(suggestion)
    try:
        db.commit()
    except IntegrityError:  # a concurrent open queued it first
        db.rollback()
        return db.scalar(select(Suggestion).where(Suggestion.message_id == last_inbound))
    db.refresh(suggestion)
    return suggestion


@router.post("/tickets/{ticket_id}/suggestion/{suggestion_id}/rating", response_model=SuggestionOut)
def rate_suggestion(
    ticket_id: int, suggestion_id: int, body: RatingIn, db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
):
    t = _visible(db, user, ticket_id)
    s = db.get(Suggestion, suggestion_id)
    if s is None or s.ticket_id != t.id:
        raise ApiError(404, "not_found", "Suggestion not found")
    s.rating = body.rating or None
    db.commit()
    return s


@router.post("/tickets/{ticket_id}/notes", response_model=MessageOut, status_code=201)
def add_note(ticket_id: int, body: NoteIn, db: Session = Depends(get_db), user: AppUser = Depends(current_user)):
    t = _visible(db, user, ticket_id)
    note = Message(
        ticket_id=t.id, mailbox_id=t.mailbox_id, direction="note", body_text=body.body_text, author_user_id=user.id
    )
    db.add(note)
    db.add(TicketEvent(ticket_id=t.id, actor_user_id=user.id, type="note_added", data={}))
    db.commit()
    return MessageOut.model_validate(note)


@router.post("/tickets/{ticket_id}/replies", response_model=MessageOut, status_code=201)
def reply(
    ticket_id: int,
    body: ReplyIn,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    factory=Depends(gmail_factory),
):
    t = _visible(db, user, ticket_id)
    mailbox = db.get(Mailbox, t.mailbox_id)
    message = send_reply(
        db,
        t,
        user.id,
        body_text=body.body_text,
        cc=body.cc,
        client_request_id=body.client_request_id,
        last_seen_message_id=body.last_seen_message_id,
        resolve=body.then == "resolve",
        client=factory(mailbox),
    )
    return MessageOut.model_validate(message)


@router.post("/tickets/{ticket_id}/replies/{message_id}/retry", response_model=MessageOut)
def retry(
    ticket_id: int,
    message_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(current_user),
    factory=Depends(gmail_factory),
):
    t = _visible(db, user, ticket_id)
    message = db.get(Message, message_id)
    if message is None or message.ticket_id != t.id:
        raise ApiError(404, "not_found", "Reply not found")
    mailbox = db.get(Mailbox, t.mailbox_id)
    return MessageOut.model_validate(retry_reply(db, t, message, factory(mailbox)))


@router.get("/attachments/{attachment_id}")
def get_attachment(
    attachment_id: int, db: Session = Depends(get_db), user: AppUser = Depends(current_user), factory=Depends(gmail_factory)
):
    from ..models import Attachment

    att = db.get(Attachment, attachment_id)
    if att is None:
        raise ApiError(404, "not_found", "Attachment not found")
    message = db.get(Message, att.message_id)
    t = _visible(db, user, message.ticket_id)  # enforces store scope
    mailbox = db.get(Mailbox, t.mailbox_id)
    data = factory(mailbox).attachment_get(message.gmail_message_id, att.gmail_attachment_id)
    return Response(
        content=data,
        media_type=att.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{att.filename}"'},
    )
