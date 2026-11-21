"""SLA clocks in business hours (LLD 5.3).

Two clocks per ticket, both measured in business minutes:
  - reply clock: a customer is waiting for an answer.
  - resolution clock: runs from creation, pauses while waiting on the customer.

The business-minutes maths is the critical path, so it lives in small pure functions
with their own tests (test_sla.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Setting, SlaPolicy, Ticket

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Sensible starting point; the admin edits this on the SLA screen.
DEFAULT_BUSINESS_HOURS = {
    "timezone": "Asia/Kolkata",
    "week": {d: [["10:00", "19:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat")},
    "holidays": [],
}
DEFAULT_POLICY = {"first_response_min": 240, "next_response_min": 480, "resolution_min": 2880}


@dataclass
class BusinessHours:
    tz: ZoneInfo
    # weekday index (0=Mon) -> list of (open, close) time pairs, sorted, non-overlapping
    windows: dict[int, list[tuple[time, time]]]
    holidays: set[str]  # ISO dates with no business hours

    def windows_for(self, d: date) -> list[tuple[time, time]]:
        if d.isoformat() in self.holidays:
            return []
        return self.windows.get(d.weekday(), [])


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _load_hours(raw: dict) -> BusinessHours:
    windows: dict[int, list[tuple[time, time]]] = {}
    for name, pairs in raw.get("week", {}).items():
        if name in WEEKDAYS:
            idx = WEEKDAYS.index(name)
            windows[idx] = sorted((_parse_time(a), _parse_time(b)) for a, b in pairs)
    return BusinessHours(
        tz=ZoneInfo(raw.get("timezone", "UTC")),
        windows=windows,
        holidays=set(raw.get("holidays", [])),
    )


def business_hours(db: Session) -> BusinessHours:
    row = db.get(Setting, "business_hours")
    return _load_hours(row.value if row and row.value else DEFAULT_BUSINESS_HOURS)


def policy_for(db: Session, store_id: int) -> SlaPolicy:
    store_policy = db.scalar(select(SlaPolicy).where(SlaPolicy.store_id == store_id))
    if store_policy:
        return store_policy
    default = db.scalar(select(SlaPolicy).where(SlaPolicy.store_id.is_(None)))
    return default or SlaPolicy(store_id=None, **DEFAULT_POLICY)


def add_business_minutes(start: datetime, minutes: int, bh: BusinessHours) -> datetime:
    """The UTC time that is `minutes` of business time after `start`."""
    cur = start.astimezone(bh.tz)
    remaining = float(minutes)
    for _ in range(100_000):  # guard against a misconfigured (fully-closed) week
        if remaining <= 0:
            break
        for open_t, close_t in bh.windows_for(cur.date()):
            open_dt = datetime.combine(cur.date(), open_t, bh.tz)
            close_dt = datetime.combine(cur.date(), close_t, bh.tz)
            if cur < open_dt:
                cur = open_dt
            if cur >= close_dt:
                continue
            avail = (close_dt - cur).total_seconds() / 60
            if remaining <= avail:
                return (cur + timedelta(minutes=remaining)).astimezone(timezone.utc)
            remaining -= avail
            cur = close_dt
        cur = datetime.combine(cur.date() + timedelta(days=1), time(0, 0), bh.tz)
    return cur.astimezone(timezone.utc)


def business_minutes_between(start: datetime, end: datetime, bh: BusinessHours) -> float:
    """Business minutes from `start` to `end` (0 if end <= start)."""
    if end <= start:
        return 0.0
    cur = start.astimezone(bh.tz)
    end = end.astimezone(bh.tz)
    total = 0.0
    for _ in range(100_000):
        if cur.date() > end.date():
            break
        for open_t, close_t in bh.windows_for(cur.date()):
            open_dt = datetime.combine(cur.date(), open_t, bh.tz)
            close_dt = datetime.combine(cur.date(), close_t, bh.tz)
            window_start = max(cur, open_dt)
            window_end = min(end, close_dt)
            if window_end > window_start:
                total += (window_end - window_start).total_seconds() / 60
        cur = datetime.combine(cur.date() + timedelta(days=1), time(0, 0), bh.tz)
    return total


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- clock transitions, called from ingest / reply / status changes ----


def after_customer_message(db: Session, ticket: Ticket, *, new_ticket: bool, prior_status: str | None) -> None:
    """A customer message needs an answer: (re)start the reply clock, resume resolution."""
    if ticket.queue != "customer":
        return
    now = _now()
    bh = business_hours(db)
    pol = policy_for(db, ticket.store_id)

    if new_ticket:
        ticket.resolution_due_at = add_business_minutes(ticket.created_at or now, pol.resolution_min, bh)
        ticket.resolution_remaining_min = None
    elif prior_status in ("resolved", "closed"):
        ticket.resolution_due_at = add_business_minutes(now, pol.resolution_min, bh)  # fresh cycle
        ticket.resolution_remaining_min = None
    elif ticket.resolution_remaining_min is not None:  # was paused in waiting_customer
        ticket.resolution_due_at = add_business_minutes(now, ticket.resolution_remaining_min, bh)
        ticket.resolution_remaining_min = None

    if ticket.awaiting_reply_since is None:
        ticket.awaiting_reply_since = now
    target = pol.first_response_min if ticket.first_responded_at is None else pol.next_response_min
    ticket.reply_due_at = add_business_minutes(ticket.awaiting_reply_since, target, bh)
    ticket.reply_warned_at = None
    ticket.reply_breached_at = None


def after_agent_reply(db: Session, ticket: Ticket, *, resolved: bool) -> None:
    now = _now()
    bh = business_hours(db)
    ticket.awaiting_reply_since = None
    ticket.reply_due_at = None
    ticket.reply_warned_at = None
    if resolved:
        ticket.resolution_due_at = None
        ticket.resolution_remaining_min = None
    elif ticket.resolution_due_at is not None:  # pause: bank the remaining business minutes
        ticket.resolution_remaining_min = max(0, round(business_minutes_between(now, ticket.resolution_due_at, bh)))
        ticket.resolution_due_at = None


def after_status_change(db: Session, ticket: Ticket, *, new_status: str, prior_status: str) -> None:
    if new_status in ("resolved", "closed"):
        ticket.reply_due_at = None
        ticket.awaiting_reply_since = None
        ticket.resolution_due_at = None
        ticket.resolution_remaining_min = None
    elif new_status in ("open", "waiting_customer") and prior_status in ("resolved", "closed"):
        bh = business_hours(db)
        pol = policy_for(db, ticket.store_id)
        ticket.resolution_due_at = add_business_minutes(_now(), pol.resolution_min, bh)
        ticket.resolution_remaining_min = None
