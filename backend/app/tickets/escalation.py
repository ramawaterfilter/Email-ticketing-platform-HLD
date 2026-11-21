"""SLA escalation and mailbox-health checks (LLD 5.4). Run every minute by the worker.

Pure over (db, now, notify) so tests drive it with a frozen clock and a recording notifier.
Each alert fires once, tracked by a timestamp column or a ticket_event.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import alerts
from ..models import Mailbox, Ticket, TicketEvent
from . import sla

WARN_WITHIN_MIN = 30
UNASSIGNED_AFTER_MIN = 15
AUTOCLOSE_AFTER_DAYS = 7
STALE_SYNC_MIN = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(db: Session, ticket: Ticket, type_: str, **data) -> None:
    db.add(TicketEvent(ticket_id=ticket.id, actor_user_id=None, type=type_, data=data))


def _has_event(db: Session, ticket_id: int, type_: str) -> bool:
    return db.scalar(
        select(TicketEvent.id).where(TicketEvent.ticket_id == ticket_id, TicketEvent.type == type_).limit(1)
    ) is not None


def escalate(db: Session, now: datetime | None = None, notify=alerts.notify) -> None:
    now = now or _now()
    managers = alerts.manager_recipients(db)
    bh = sla.business_hours(db)

    _reply_clocks(db, now, bh, managers, notify)
    _resolution_clocks(db, now, managers, notify)
    _unassigned(db, now, managers, notify)
    _auto_close(db, now)
    db.commit()


def _reply_clocks(db, now, bh, managers, notify) -> None:
    tickets = db.scalars(
        select(Ticket).where(
            Ticket.queue == "customer",
            Ticket.status.in_(("new", "open")),
            Ticket.reply_due_at.is_not(None),
        )
    ).all()
    for t in tickets:
        if t.reply_breached_at is None and now >= t.reply_due_at:
            t.reply_breached_at = now
            _event(db, t, "sla_breached", clock="reply")
            notify(db, managers, f"SLA breach: reply overdue on #{t.number}", _line(t))
        elif (
            t.reply_warned_at is None
            and t.assignee is not None
            and now < t.reply_due_at
            and sla.business_minutes_between(now, t.reply_due_at, bh) <= WARN_WITHIN_MIN
        ):
            t.reply_warned_at = now
            _event(db, t, "sla_warned", clock="reply")
            notify(db, [t.assignee.email], f"Reply due soon on #{t.number}", _line(t))


def _resolution_clocks(db, now, managers, notify) -> None:
    tickets = db.scalars(
        select(Ticket).where(
            Ticket.status.in_(("new", "open")),
            Ticket.resolution_due_at.is_not(None),
            Ticket.resolution_breached_at.is_(None),
        )
    ).all()
    for t in tickets:
        if now >= t.resolution_due_at:
            t.resolution_breached_at = now
            _event(db, t, "sla_breached", clock="resolution")
            notify(db, managers, f"SLA breach: resolution overdue on #{t.number}", _line(t))


def _unassigned(db, now, managers, notify) -> None:
    cutoff = now - timedelta(minutes=UNASSIGNED_AFTER_MIN)
    tickets = db.scalars(
        select(Ticket).where(
            Ticket.queue == "customer",
            Ticket.assignee_id.is_(None),
            Ticket.status.in_(("new", "open")),
            Ticket.created_at <= cutoff,
        )
    ).all()
    for t in tickets:
        if not _has_event(db, t.id, "unassigned_alerted"):
            _event(db, t, "unassigned_alerted")
            notify(db, managers, f"Unassigned ticket #{t.number} needs an owner", _line(t))


def _auto_close(db, now) -> None:
    cutoff = now - timedelta(days=AUTOCLOSE_AFTER_DAYS)
    tickets = db.scalars(
        select(Ticket).where(Ticket.status == "resolved", Ticket.resolved_at <= cutoff)
    ).all()
    for t in tickets:
        t.status = "closed"
        t.closed_at = now
        _event(db, t, "status_changed", status="closed", auto=True)


def check_mailbox_health(db: Session, now: datetime | None = None, notify=alerts.notify) -> None:
    now = now or _now()
    cutoff = now - timedelta(minutes=STALE_SYNC_MIN)
    admins = alerts.admin_recipients(db)
    stale = db.scalars(
        select(Mailbox).where(
            Mailbox.status == "active",
            Mailbox.health_alerted_at.is_(None),
            (Mailbox.last_sync_at.is_(None)) | (Mailbox.last_sync_at < cutoff),
        )
    ).all()
    for mb in stale:
        # A mailbox only just connected hasn't missed a sync yet.
        if mb.last_sync_at is None and mb.created_at > cutoff:
            continue
        mb.health_alerted_at = now
        notify(db, admins, f"Mailbox not syncing: {mb.google_email}", "No successful sync in over 10 minutes.")
    db.commit()


def _line(t: Ticket) -> str:
    return f"#{t.number} · {t.subject or '(no subject)'} · {t.store.name} · {t.customer.email}"
