"""Auto-assignment (LLD 5.2): round-robin to the least-loaded eligible executive."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import OPEN_STATUSES, AppUser, Ticket, user_store


def pick_assignee(db: Session, ticket: Ticket) -> AppUser | None:
    """Fewest open tickets wins; ties break on who was assigned longest ago.

    Candidate rows are locked so two tickets arriving together are not both handed to
    the same person. Returns None (ticket stays unassigned) when no one is eligible.
    """
    # Executives restricted to stores: a row in user_store means "only these stores".
    restricted = select(user_store.c.user_id).where(user_store.c.store_id == ticket.store_id)
    unrestricted = select(user_store.c.user_id)

    candidates = db.scalars(
        select(AppUser)
        .where(
            AppUser.role == "executive",
            AppUser.is_active.is_(True),
            AppUser.is_available.is_(True),
            AppUser.id.in_(restricted) | AppUser.id.notin_(unrestricted),
        )
        .with_for_update()
    ).all()
    if not candidates:
        return None

    open_counts = dict(
        db.execute(
            select(Ticket.assignee_id, func.count())
            .where(Ticket.assignee_id.in_([u.id for u in candidates]), Ticket.status.in_(OPEN_STATUSES))
            .group_by(Ticket.assignee_id)
        ).all()
    )

    def load(user: AppUser) -> int:
        return open_counts.get(user.id, 0)

    eligible = [u for u in candidates if load(u) < u.max_open_tickets]
    if not eligible:
        return None
    # Oldest last_assigned_at first; never-assigned (None) sorts oldest of all.
    return min(eligible, key=lambda u: (load(u), u.last_assigned_at or _EPOCH))


from datetime import datetime, timezone  # noqa: E402

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
