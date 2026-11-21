"""Manager board: workload and anything overdue at a glance (LLD 6.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..models import OPEN_STATUSES, AppUser, Mailbox, Store, Suggestion, SyncRun, Ticket
from ..schemas import Count, Dashboard, MailboxHealth, Report, ReportStoreRow

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


@router.get("/reports", response_model=Report)
def reports(days: int = 30, store_id: int | None = None, db: Session = Depends(get_db)):
    days = min(max(days, 1), 365)  # clamp
    now = datetime.now(timezone.utc)
    frm = now - timedelta(days=days)

    customer = Ticket.queue == "customer"
    store = [Ticket.store_id == store_id] if store_id else []

    created = db.scalar(select(func.count()).where(customer, Ticket.created_at >= frm, *store)) or 0
    resolved = db.scalar(
        select(func.count()).where(customer, Ticket.resolved_at.is_not(None), Ticket.resolved_at >= frm, *store)
    ) or 0
    breached = db.scalar(
        select(func.count()).where(customer, Ticket.created_at >= frm, Ticket.reply_breached_at.is_not(None), *store)
    ) or 0
    # Wall-clock minutes between arrival and first human reply, median across the period.
    median_min = db.scalar(
        select(func.percentile_cont(0.5).within_group(
            func.extract("epoch", Ticket.first_responded_at - Ticket.created_at) / 60
        )).where(customer, Ticket.created_at >= frm, Ticket.first_responded_at.is_not(None), *store)
    )

    # Missed-email recoveries are mailbox-wide (no store dimension on sync runs).
    recoveries = db.scalar(
        select(func.coalesce(func.sum(SyncRun.recovered_count), 0)).where(SyncRun.started_at >= frm)
    ) or 0

    s_store = [Suggestion.store_id == store_id] if store_id else []
    ai_ready = db.scalar(
        select(func.count()).where(Suggestion.created_at >= frm, Suggestion.status == "ready", *s_store)
    ) or 0
    ai_used = db.scalar(
        select(func.count()).where(Suggestion.created_at >= frm, Suggestion.final_sent_text.is_not(None), *s_store)
    ) or 0
    ai_helpful = db.scalar(
        select(func.count()).where(Suggestion.created_at >= frm, Suggestion.rating == 1, *s_store)
    ) or 0
    ai_not_helpful = db.scalar(
        select(func.count()).where(Suggestion.created_at >= frm, Suggestion.rating == -1, *s_store)
    ) or 0

    by_store_rows = db.execute(
        select(
            Store.id,
            Store.name,
            func.count(Ticket.id).filter(Ticket.created_at >= frm),
            func.count(Ticket.id).filter(Ticket.resolved_at.is_not(None), Ticket.resolved_at >= frm),
        )
        .join(Ticket, (Ticket.store_id == Store.id) & customer)
        .group_by(Store.id, Store.name)
        .order_by(Store.name)
    ).all()

    return Report(
        from_date=frm,
        to_date=now,
        created=created,
        resolved=resolved,
        first_response_sla_pct=round(100 * (1 - breached / created), 1) if created else None,
        median_first_response_min=round(median_min, 1) if median_min is not None else None,
        recoveries=recoveries,
        ai_ready=ai_ready,
        ai_used=ai_used,
        ai_helpful=ai_helpful,
        ai_not_helpful=ai_not_helpful,
        by_store=[ReportStoreRow(id=sid, name=name, created=c, resolved=r) for sid, name, c, r in by_store_rows],
    )
