"""Background worker: timed loops that sync mailboxes and run the nightly safety net.

Phase 1 has no job queue - just loops. SLA/escalation loops arrive in milestone 4.
Run with:  python -m app.worker
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import SessionLocal
from .gmail.client import client_for
from .gmail.sync import backfill_mailbox, reconcile_mailbox, sync_mailbox
from .models import Mailbox

log = logging.getLogger("worker")

SYNC_EVERY_SECONDS = 60
RECONCILE_AT = dtime(2, 0)  # 02:00 local to the server
# A Postgres advisory-lock namespace, so a mailbox is never synced by two workers at once.
LOCK_NAMESPACE = 4711


def _active_mailboxes(db: Session) -> list[Mailbox]:
    return list(db.scalars(select(Mailbox).where(Mailbox.status == "active")))


def _with_lock(db: Session, mailbox_id: int, fn):
    """Run fn under a per-mailbox advisory lock. Returns fn()'s result, or None if skipped."""
    locked = db.scalar(text("select pg_try_advisory_lock(:ns, :id)"), {"ns": LOCK_NAMESPACE, "id": mailbox_id})
    if not locked:
        return None  # another worker holds it; skip this tick
    try:
        return fn()
    finally:
        db.execute(text("select pg_advisory_unlock(:ns, :id)"), {"ns": LOCK_NAMESPACE, "id": mailbox_id})


def _sync_or_backfill(db, mailbox, client) -> None:
    # A freshly activated mailbox imports its recent history once, then syncs normally.
    if mailbox.backfilled_at is None:
        backfill_mailbox(db, mailbox, client)
    sync_mailbox(db, mailbox, client)


def run_sync_once(factory=client_for) -> None:
    with SessionLocal() as db:
        for mailbox in _active_mailboxes(db):
            try:
                _with_lock(db, mailbox.id, lambda m=mailbox: _sync_or_backfill(db, m, factory(m)))
            except Exception:  # noqa: BLE001 - one bad mailbox must not stop the others
                log.exception("sync failed for mailbox %s", mailbox.id)
                db.rollback()


def run_sla_once() -> None:
    from .tickets.escalation import check_mailbox_health, escalate

    with SessionLocal() as db:
        try:
            escalate(db)
            check_mailbox_health(db)
        except Exception:  # noqa: BLE001
            log.exception("sla/escalation pass failed")
            db.rollback()


def run_reconcile_once(factory=client_for) -> int:
    recovered = 0
    with SessionLocal() as db:
        for mailbox in _active_mailboxes(db):
            try:
                run = _with_lock(db, mailbox.id, lambda m=mailbox: reconcile_mailbox(db, m, factory(m)))
                if run is not None:
                    recovered += run.recovered_count
            except Exception:  # noqa: BLE001
                log.exception("reconcile failed for mailbox %s", mailbox.id)
                db.rollback()
    return recovered


def main() -> None:  # pragma: no cover - the loop itself is not unit-tested
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log.info("worker started")
    last_reconcile_date = None
    while True:
        run_sync_once()
        run_sla_once()  # SLA warnings/breaches, unassigned alerts, auto-close, sync health
        now = datetime.now(timezone.utc)
        if now.time() >= RECONCILE_AT and last_reconcile_date != now.date():
            log.info("nightly reconciliation")
            run_reconcile_once()
            last_reconcile_date = now.date()
        time.sleep(SYNC_EVERY_SECONDS)


if __name__ == "__main__":
    main()
