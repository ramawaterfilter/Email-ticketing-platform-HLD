from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Sequence,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("admin", "manager", "executive")

# Ticket queues. A customer email that isn't an auto-reply/bounce/spam lands in "customer".
QUEUES = ("customer", "other", "spam")
# Ticket lifecycle; see LLD 5.1. Only tickets/service.py changes status.
STATUSES = ("new", "open", "waiting_customer", "resolved", "closed")
PRIORITIES = ("low", "normal", "high", "urgent")
OPEN_STATUSES = ("new", "open", "waiting_customer")  # counts toward an executive's workload


def _utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)

# No rows for a user = that user works on all stores.
user_store = Table(
    "user_store",
    Base.metadata,
    Column("user_id", ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
    Column("store_id", ForeignKey("store.id", ondelete="CASCADE"), primary_key=True),
)


class Store(Base):
    __tablename__ = "store"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(CITEXT, unique=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    signature_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Brand tone + reply rules fed to the LLM when it drafts replies for this store (Phase 2).
    ai_guidance: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Per-store Shopify Admin API connection, used to look up the customer's orders (Phase 2).
    shopify_domain: Mapped[str | None] = mapped_column(Text)  # e.g. my-store.myshopify.com
    shopify_token_enc: Mapped[str | None] = mapped_column(Text)  # encrypted Admin API access token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def shopify_connected(self) -> bool:
        return bool(self.shopify_domain and self.shopify_token_enc)


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("role in ('admin', 'manager', 'executive')", name="app_user_role_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text, default="", server_default="")
    google_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    role: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    is_available: Mapped[bool] = mapped_column(default=True, server_default="true")
    max_open_tickets: Mapped[int] = mapped_column(default=30, server_default="30")
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stores: Mapped[list[Store]] = relationship(secondary=user_store, lazy="selectin")

    @property
    def store_ids(self) -> list[int]:
        return [s.id for s in self.stores]


class Mailbox(Base):
    """One connected Google account. Its addresses may serve different stores."""

    __tablename__ = "mailbox"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'active', 'error', 'disabled')", name="mailbox_status_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    google_email: Mapped[str] = mapped_column(CITEXT, unique=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    last_history_id: Mapped[str | None] = mapped_column(Text)  # Gmail sync cursor
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    desk_label_id: Mapped[str | None] = mapped_column(Text)  # Gmail id of the Desk/Ticketed label
    health_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last stale-sync alert
    backfill_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")  # window imported on activation
    backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # set once backfill is done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportAddress(Base):
    """One address customers write to. The address decides the store (LLD 4.0)."""

    __tablename__ = "support_address"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    address: Mapped[str] = mapped_column(CITEXT, unique=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("store.id", ondelete="RESTRICT"))
    is_default: Mapped[bool] = mapped_column(default=False, server_default="false")  # used when no address matches
    display_name: Mapped[str] = mapped_column(Text, default="", server_default="")


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    name: Mapped[str] = mapped_column(Text, default="", server_default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ticket(Base):
    __tablename__ = "ticket"
    __table_args__ = (
        CheckConstraint("queue in ('customer', 'other', 'spam')", name="ticket_queue_check"),
        CheckConstraint(
            "status in ('new', 'open', 'waiting_customer', 'resolved', 'closed')", name="ticket_status_check"
        ),
        CheckConstraint("priority in ('low', 'normal', 'high', 'urgent')", name="ticket_priority_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Human-facing number, shown as #10234. Separate from the internal id.
    number: Mapped[int] = mapped_column(BigInteger, Sequence("ticket_number_seq", start=10000), unique=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id", ondelete="RESTRICT"), index=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id", ondelete="RESTRICT"))
    support_address_id: Mapped[int | None] = mapped_column(ForeignKey("support_address.id", ondelete="SET NULL"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="RESTRICT"))
    gmail_thread_id: Mapped[str] = mapped_column(Text, index=True)
    subject: Mapped[str] = mapped_column(Text, default="", server_default="")

    queue: Mapped[str] = mapped_column(Text, default="customer", server_default="customer")
    status: Mapped[str] = mapped_column(Text, default="new", server_default="new")
    priority: Mapped[str] = mapped_column(Text, default="normal", server_default="normal")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")

    # SLA bookkeeping (clocks are computed in milestone 4; fields live here now to avoid a reshape).
    awaiting_reply_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_remaining_min: Mapped[int | None] = mapped_column(Integer)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    delivery_failed: Mapped[bool] = mapped_column(default=False, server_default="false")
    previous_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("ticket.id", ondelete="SET NULL"))
    last_customer_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_agent_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow
    )

    # Read-only convenience joins (small tables); no schema impact.
    store: Mapped[Store] = relationship(lazy="joined")
    customer: Mapped[Customer] = relationship(lazy="joined")
    assignee: Mapped["AppUser | None"] = relationship(foreign_keys=[assignee_id], lazy="joined")


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        # The no-duplicates guarantee: the same Gmail message can never be ingested twice.
        UniqueConstraint("mailbox_id", "gmail_message_id", name="message_mailbox_gmail_uq"),
        CheckConstraint("direction in ('inbound', 'outbound', 'note')", name="message_direction_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id", ondelete="CASCADE"), index=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id", ondelete="RESTRICT"))
    direction: Mapped[str] = mapped_column(Text)

    gmail_message_id: Mapped[str | None] = mapped_column(Text)  # null for internal notes
    gmail_thread_id: Mapped[str | None] = mapped_column(Text)
    rfc_message_id: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references_header: Mapped[str | None] = mapped_column(Text)

    from_address: Mapped[str] = mapped_column(Text, default="", server_default="")
    from_name: Mapped[str] = mapped_column(Text, default="", server_default="")
    to_addresses: Mapped[list] = mapped_column(JSONB, default=list)
    cc_addresses: Mapped[list] = mapped_column(JSONB, default=list)

    subject: Mapped[str] = mapped_column(Text, default="", server_default="")
    body_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    body_html: Mapped[str] = mapped_column(Text, default="", server_default="")
    snippet: Mapped[str] = mapped_column(Text, default="", server_default="")
    headers: Mapped[dict] = mapped_column(JSONB, default=dict)

    is_auto: Mapped[bool] = mapped_column(default=False, server_default="false")
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    client_request_id: Mapped[str | None] = mapped_column(Text, unique=True)  # makes "Send" safe to retry
    send_status: Mapped[str | None] = mapped_column(Text)  # null | sending | sent | failed
    gmail_labelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attachments: Mapped[list["Attachment"]] = relationship(lazy="selectin", order_by="Attachment.id")


class Attachment(Base):
    __tablename__ = "attachment"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(Text, default="", server_default="")
    mime_type: Mapped[str] = mapped_column(Text, default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    gmail_attachment_id: Mapped[str | None] = mapped_column(Text)  # bytes fetched from Gmail on demand
    content_id: Mapped[str | None] = mapped_column(Text)  # for inline images


class TicketEvent(Base):
    """Append-only audit trail. actor_user_id null = system."""

    __tablename__ = "ticket_event"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncRun(Base):
    """One row per sync or reconciliation; the admin screen reads the latest per mailbox."""

    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)  # incremental | full | reconcile
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="ok", server_default="ok")  # ok | error
    messages_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    messages_ingested: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recovered_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class SlaPolicy(Base):
    """Response and resolution targets, in business minutes. store_id null = the default policy."""

    __tablename__ = "sla_policy"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"), unique=True)
    first_response_min: Mapped[int] = mapped_column(Integer, default=240, server_default="240")
    next_response_min: Mapped[int] = mapped_column(Integer, default=480, server_default="480")
    resolution_min: Mapped[int] = mapped_column(Integer, default=2880, server_default="2880")


class Setting(Base):
    """Small key/value store for singletons: business hours, alert recipients."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


class KbDocument(Base):
    """A knowledge-base document the LLM may draw on. store_id null = shared across all stores."""

    __tablename__ = "kb_document"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, default="", server_default="")  # extracted plain text
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CannedResponse(Base):
    """A saved reply snippet an executive can insert. store_id null = shared across all stores."""

    __tablename__ = "canned_response"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Suggestion(Base):
    """An LLM-drafted reply for one inbound customer message. The row doubles as the job:
    created `pending` at ingest, the worker fills it in and marks it `ready` or `failed`."""

    __tablename__ = "suggestion"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'ready', 'failed')", name="suggestion_status_check"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"), unique=True, index=True
    )  # one suggestion per inbound message
    ticket_id: Mapped[int] = mapped_column(ForeignKey("ticket.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("store.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending", index=True)

    model: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    suggested_reply: Mapped[str] = mapped_column(Text, default="", server_default="")
    missing_info: Mapped[list] = mapped_column(JSONB, default=list)
    documents_used: Mapped[list] = mapped_column(JSONB, default=list)  # [{id, title}] for traceability
    error: Mapped[str | None] = mapped_column(Text)

    rating: Mapped[int | None] = mapped_column(Integer)  # 1 = helpful, -1 = not helpful
    final_sent_text: Mapped[str | None] = mapped_column(Text)  # what the executive actually sent
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow
    )
