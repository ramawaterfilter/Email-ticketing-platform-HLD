"""Manager board: workload and anything overdue at a glance (LLD 6.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..models import OPEN_STATUSES, AppUser, Mailbox, Store, Ticket
from ..schemas import Count, Dashboard, MailboxHealth

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_role("manager", "admin"))])

DUE_SOON_MIN = 60  # a reply falling due within the next hour


@router.get("/dashboard", response_model=Dashboard)
def dashboard(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    open_customer = (Ticket.queue == "customer") & Ticket.status.in_(OPEN_STATUSES)

    unassigned = db.scalar(
        select(func.count()).where(open_customer, Ticket.assignee_id.is_(None))
    )
    breached = db.scalar(
        select(func.count()).where(
            open_customer, or_(Ticket.reply_breached_at.is_not(None), Ticket.resolution_breached_at.is_not(None))
        )
    )
    due_soon = db.scalar(
        select(func.count()).where(
            open_customer,
            Ticket.reply_breached_at.is_(None),
            Ticket.reply_due_at.is_not(None),
            Ticket.reply_due_at > now,
            Ticket.reply_due_at <= now + timedelta(minutes=DUE_SOON_MIN),
        )
    )

    by_exec = db.execute(
        select(AppUser.id, AppUser.name, AppUser.email, func.count(Ticket.id))
        .join(Ticket, Ticket.assignee_id == AppUser.id)
        .where(open_customer)
        .group_by(AppUser.id, AppUser.name, AppUser.email)
        .order_by(func.count(Ticket.id).desc())
    ).all()
    by_store = db.execute(
        select(Store.id, Store.name, func.count(Ticket.id))
        .join(Ticket, Ticket.store_id == Store.id)
        .where(open_customer)
        .group_by(Store.id, Store.name)
        .order_by(func.count(Ticket.id).desc())
    ).all()
    mailboxes = db.scalars(select(Mailbox).order_by(Mailbox.google_email)).all()

    return Dashboard(
        unassigned=unassigned,
        due_soon=due_soon,
        breached=breached,
        by_executive=[Count(id=uid, name=name or email, open_count=n) for uid, name, email, n in by_exec],
        by_store=[Count(id=sid, name=name, open_count=n) for sid, name, n in by_store],
        mailboxes=[MailboxHealth.model_validate(m) for m in mailboxes],
    )
