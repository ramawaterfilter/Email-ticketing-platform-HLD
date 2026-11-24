"""Auto-assignment (LLD 5.2): round-robin to the least-loaded eligible executive."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import OPEN_STATUSES, AppUser, Setting, Ticket, user_store


def pick_assignee(db: Session, ticket: Ticket) -> AppUser | None:
    """Pick an executive per the admin auto-assignment settings.

    Settings (stored under "assignment"): `enabled` (off = ticket stays unassigned),
    and `strategy`: "least_loaded" (fewest open tickets wins) or "round_robin" (whoever
    was assigned longest ago, rotating). Both respect each person's open-ticket cap,
    availability, and store membership. Candidate rows are locked so two tickets arriving
    together are not handed to the same person.
    """
    cfg = db.get(Setting, "assignment")
    conf = cfg.value if cfg and cfg.value else {}
    if not conf.get("enabled", True):
        return None
    strategy = conf.get("strategy", "least_loaded")
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
    if strategy == "round_robin":
        # Rotate strictly by who was assigned longest ago (still capacity-capped); load breaks ties.
        return min(eligible, key=lambda u: (u.last_assigned_at or _EPOCH, load(u)))
    # least_loaded: fewest open tickets first; who was assigned longest ago breaks ties.
    return min(eligible, key=lambda u: (load(u), u.last_assigned_at or _EPOCH))


from datetime import datetime, timezone  # noqa: E402

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
