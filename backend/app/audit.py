"""Central audit log for critical, system-wide changes.

Call `record(...)` from a mutation endpoint before the commit; the caller's
commit persists it. Never pass secrets (API keys, tokens) in the data kwargs.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AppUser, AuditLog


def record(
    db: Session,
    actor: AppUser | None,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object | None = None,
    summary: str = "",
    **data: object,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            action=action,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            summary=summary,
            data=data or {},
        )
    )
