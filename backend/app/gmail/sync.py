"""Incremental sync and the nightly reconciliation safety net (LLD 4.2, 4.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Mailbox, Message, SyncRun
from .client import AuthRevoked, GmailClient, HistoryExpired
from .ingest import ingest
from .mime import parse_message

# Gmail's own labels for drafts and chats: never tickets.
SKIP_LABELS = {"DRAFT", "CHAT"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_queries() -> list[str]:
    days = settings.reconcile_window_days
    return [f"newer_than:{days}d", f"in:spam newer_than:{days}d"]


def _ingest_id(db: Session, mailbox: Mailbox, client: GmailClient, mid: str) -> bool:
    """Fetch, ingest and label one message. Returns True if it was newly ingested."""
    msg = client.message_get(mid)
    if SKIP_LABELS & set(msg.get("labelIds", [])):
        return False
    parsed = parse_message(msg)
    message = ingest(db, mailbox, parsed)
    db.commit()  # commit before labelling, so a label failure can't lose the message

    if message.gmail_labelled_at is None and mailbox.desk_label_id:
        try:
            client.message_add_label(mid, mailbox.desk_label_id)
            message.gmail_labelled_at = _now()
            db.commit()
        except Exception:  # noqa: BLE001 - labelling is best-effort; reconcile re-applies it
            db.rollback()
    return True


def sync_mailbox(db: Session, mailbox: Mailbox, client: GmailClient) -> SyncRun:
    """One incremental pass. The cursor only advances after every message is committed."""
    run = SyncRun(mailbox_id=mailbox.id, kind="incremental")
    db.add(run)
    db.commit()
    try:
        if mailbox.last_history_id:
            try:
                ids = client.history_list(mailbox.last_history_id)
            except HistoryExpired:
                return _full_sync(db, mailbox, client, run)
        else:
            ids = _distinct(client, _window_queries())

        for mid in ids:
            run.messages_seen += 1
            if _already(db, mailbox, mid):
                continue
            if _ingest_id(db, mailbox, client, mid):
                run.messages_ingested += 1

        mailbox.last_history_id = str(client.profile_get()["historyId"])
        mailbox.last_sync_at = _now()
        mailbox.last_sync_error = None
        mailbox.health_alerted_at = None  # a fresh success re-arms the stale-sync alert
        _finish(db, run, "ok")
    except AuthRevoked as exc:
        mailbox.status = "error"
        mailbox.last_sync_error = f"access revoked: {exc}"
        _finish(db, run, "error", str(exc))
    except Exception as exc:  # noqa: BLE001 - record and let the next tick retry
        mailbox.last_sync_error = str(exc)
        _finish(db, run, "error", str(exc))
    return run


def _full_sync(db: Session, mailbox: Mailbox, client: GmailClient, run: SyncRun) -> SyncRun:
    run.kind = "full"
    for mid in _distinct(client, _window_queries()):
        run.messages_seen += 1
        if _already(db, mailbox, mid):
            continue
        if _ingest_id(db, mailbox, client, mid):
            run.messages_ingested += 1
    mailbox.last_history_id = str(client.profile_get()["historyId"])
    mailbox.last_sync_at = _now()
    mailbox.last_sync_error = None
    _finish(db, run, "ok")
    return run


def backfill_mailbox(db: Session, mailbox: Mailbox, client: GmailClient) -> SyncRun:
    """One-time import of recent mail when a mailbox is first activated (LLD 4.1).

    These are exactly the emails that may already have been missed before the desk existed.
    """
    days = mailbox.backfill_days or 7
    run = SyncRun(mailbox_id=mailbox.id, kind="full")
    db.add(run)
    db.commit()
    try:
        for mid in _distinct(client, [f"newer_than:{days}d", f"in:spam newer_than:{days}d"]):
            run.messages_seen += 1
            if _already(db, mailbox, mid):
                continue
            if _ingest_id(db, mailbox, client, mid):
                run.messages_ingested += 1
        mailbox.backfilled_at = _now()
        _finish(db, run, "ok")
    except AuthRevoked as exc:
        mailbox.status = "error"
        _finish(db, run, "error", str(exc))
    except Exception as exc:  # noqa: BLE001
        _finish(db, run, "error", str(exc))
    return run


def reconcile_mailbox(db: Session, mailbox: Mailbox, client: GmailClient) -> SyncRun:
    """The safety net: anything in Gmail's window but not in the database is ingested."""
    run = SyncRun(mailbox_id=mailbox.id, kind="reconcile")
    db.add(run)
    db.commit()
    try:
        gmail_ids = set(_distinct(client, _window_queries()))
        run.messages_seen = len(gmail_ids)
        known = set(
            db.scalars(
                select(Message.gmail_message_id).where(
                    Message.mailbox_id == mailbox.id, Message.gmail_message_id.in_(gmail_ids)
                )
            ).all()
        )
        for mid in gmail_ids - known:
            if _ingest_id(db, mailbox, client, mid):
                run.recovered_count += 1
                run.messages_ingested += 1
        _relabel_unlabelled(db, mailbox, client)
        _finish(db, run, "ok")
    except AuthRevoked as exc:
        mailbox.status = "error"
        _finish(db, run, "error", str(exc))
    except Exception as exc:  # noqa: BLE001
        _finish(db, run, "error", str(exc))
    return run


def _relabel_unlabelled(db: Session, mailbox: Mailbox, client: GmailClient) -> None:
    if not mailbox.desk_label_id:
        return
    rows = db.scalars(
        select(Message).where(
            Message.mailbox_id == mailbox.id,
            Message.gmail_message_id.is_not(None),
            Message.gmail_labelled_at.is_(None),
        )
    ).all()
    for m in rows:
        try:
            client.message_add_label(m.gmail_message_id, mailbox.desk_label_id)
            m.gmail_labelled_at = _now()
        except Exception:  # noqa: BLE001
            continue
    db.commit()


def _distinct(client: GmailClient, queries: list[str]) -> list[str]:
    ids: list[str] = []
    for q in queries:
        ids.extend(client.messages_list(q))
    return list(dict.fromkeys(ids))


def _already(db: Session, mailbox: Mailbox, mid: str) -> bool:
    return db.scalar(
        select(Message.id).where(Message.mailbox_id == mailbox.id, Message.gmail_message_id == mid)
    ) is not None


def _finish(db: Session, run: SyncRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = _now()
    db.commit()
