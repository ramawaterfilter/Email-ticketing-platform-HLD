from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["admin", "manager", "executive"]
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class In(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StoreOut(Out):
    id: int
    name: str
    is_active: bool
    signature_text: str
    ai_guidance: str
    shopify_domain: str | None
    shopify_connected: bool


class StoreIn(In):
    name: str = Field(min_length=1, max_length=100)
    signature_text: str = Field("", max_length=5000)
    ai_guidance: str = Field("", max_length=20000)
    shopify_domain: str | None = Field(None, max_length=255)
    shopify_token: str | None = Field(None, max_length=500)  # write-only; stored encrypted


class StorePatch(In):
    name: str | None = Field(None, min_length=1, max_length=100)
    signature_text: str | None = Field(None, max_length=5000)
    ai_guidance: str | None = Field(None, max_length=20000)
    is_active: bool | None = None
    shopify_domain: str | None = Field(None, max_length=255)
    shopify_token: str | None = Field(None, max_length=500)  # write-only; "" clears the connection


class UserOut(Out):
    id: int
    email: str
    name: str
    role: Role
    is_active: bool
    is_available: bool
    max_open_tickets: int
    store_ids: list[int]


class UserIn(In):
    email: str = Field(pattern=EMAIL, max_length=254)
    name: str = Field("", max_length=100)
    role: Role
    store_ids: list[int] = []
    max_open_tickets: int = Field(30, ge=1, le=500)


class UserPatch(In):
    name: str | None = Field(None, max_length=100)
    role: Role | None = None
    is_active: bool | None = None
    is_available: bool | None = None  # admins can mark an executive available/unavailable for auto-assignment
    store_ids: list[int] | None = None
    max_open_tickets: int | None = Field(None, ge=1, le=500)


class MePatch(In):
    is_available: bool


class DevLogin(In):
    email: str = Field(pattern=EMAIL, max_length=254)


# ---- Mailboxes & support addresses (milestone 2) ----


class SyncRunOut(Out):
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    messages_seen: int
    messages_ingested: int
    recovered_count: int
    error: str | None


class SupportAddressOut(Out):
    id: int
    address: str
    mailbox_id: int
    store_id: int | None
    is_default: bool
    display_name: str


class MailboxOut(Out):
    id: int
    google_email: str
    status: str
    last_sync_at: datetime | None
    last_sync_error: str | None


class MailboxDetail(MailboxOut):
    addresses: list[SupportAddressOut]
    last_sync_run: SyncRunOut | None
    last_reconcile: SyncRunOut | None


class SupportAddressPatch(In):
    store_id: int | None = None
    display_name: str | None = Field(None, max_length=200)


class MailboxPatch(In):
    status: Literal["active", "disabled"] | None = None
    backfill_days: int | None = Field(None, ge=1, le=90)


# ---- Tickets (milestone 3) ----

Status = Literal["new", "open", "waiting_customer", "resolved", "closed"]
Priority = Literal["low", "normal", "high", "urgent"]


class AttachmentOut(Out):
    id: int
    filename: str
    mime_type: str
    size_bytes: int


class MessageOut(Out):
    id: int
    direction: str
    from_address: str
    from_name: str
    to_addresses: list
    cc_addresses: list
    subject: str
    body_text: str
    body_html: str
    snippet: str
    is_auto: bool
    author_user_id: int | None
    send_status: str | None
    created_at: datetime
    sent_at: datetime | None
    attachments: list[AttachmentOut]


class EventOut(Out):
    id: int
    type: str
    data: dict
    actor_user_id: int | None
    created_at: datetime


class TicketListItem(Out):
    id: int
    number: int
    subject: str
    queue: str
    status: str
    priority: str
    store_id: int
    store_name: str
    assignee_id: int | None
    assignee_name: str | None
    customer_email: str
    customer_name: str
    tags: list[str]
    delivery_failed: bool
    breached: bool
    awaiting_reply_since: datetime | None
    reply_due_at: datetime | None
    last_customer_message_at: datetime | None
    updated_at: datetime


class TicketList(Out):
    items: list[TicketListItem]
    next_cursor: str | None


class LinkedTicket(Out):
    id: int
    number: int
    subject: str
    status: str


class TicketDetail(TicketListItem):
    mailbox_id: int
    support_address_id: int | None
    gmail_thread_id: str
    created_at: datetime
    first_responded_at: datetime | None
    messages: list[MessageOut]
    events: list[EventOut]
    other_open_tickets: list[LinkedTicket]


class TicketPatch(In):
    status: Status | None = None
    priority: Priority | None = None
    assignee_id: int | None = Field(None)  # null clears the assignee
    store_id: int | None = None
    queue: Literal["customer", "other", "spam"] | None = None
    tags: list[str] | None = None
    # Distinguishes "set assignee to null" from "don't touch assignee" (both arrive as None).
    clear_assignee: bool = False


class ReplyIn(In):
    body_text: str = Field(min_length=1, max_length=50000)
    cc: list[str] = Field(default_factory=list, max_length=20)
    client_request_id: str = Field(min_length=8, max_length=100)
    last_seen_message_id: int | None = None
    then: Literal["wait", "resolve"] = "wait"


class NoteIn(In):
    body_text: str = Field(min_length=1, max_length=50000)


# ---- SLA, settings, dashboard (milestone 4) ----


class SlaPolicyOut(Out):
    store_id: int | None
    first_response_min: int
    next_response_min: int
    resolution_min: int


class SlaPolicyIn(In):
    store_id: int | None = None
    first_response_min: int = Field(ge=5, le=100000)
    next_response_min: int = Field(ge=5, le=100000)
    resolution_min: int = Field(ge=5, le=1000000)


class BusinessHours(In):
    # timezone name, per-day [["10:00","19:00"], ...] windows, and ISO holiday dates
    timezone: str = Field(min_length=1, max_length=64)
    week: dict[str, list[list[str]]]
    holidays: list[str] = Field(default_factory=list)


class AlertSettings(In):
    manager_emails: list[str] = Field(default_factory=list, max_length=50)
    google_chat_webhook: str | None = Field(None, max_length=1000)


class Count(Out):
    id: int | None
    name: str
    open_count: int


class MailboxHealth(Out):
    id: int
    google_email: str
    status: str
    last_sync_at: datetime | None
    last_sync_error: str | None


class Dashboard(Out):
    unassigned: int
    due_soon: int
    breached: int
    by_executive: list[Count]
    by_store: list[Count]
    mailboxes: list[MailboxHealth]


# ---- Knowledge base & suggestions (Phase 2) ----


class KbDocumentOut(Out):
    id: int
    store_id: int | None
    title: str
    filename: str | None
    source_url: str | None
    is_active: bool
    char_count: int
    chunks: int = 0
    embedded: int = 0
    created_at: datetime


class KbDocumentIn(In):
    title: str = Field(min_length=1, max_length=200)
    store_id: int | None = None  # null = shared across all stores
    body_text: str = Field("", max_length=500000)  # for a pasted article; file upload sets this instead


class KbDocumentPatch(In):
    title: str | None = Field(None, min_length=1, max_length=200)
    store_id: int | None = None
    is_active: bool | None = None


class KbAskIn(In):
    question: str = Field(min_length=1, max_length=2000)


class KbAskOut(Out):
    answer: str
    sources: list  # [{id, title}] of documents the answer drew on


class KbDocumentFromUrl(In):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(None, min_length=1, max_length=200)  # defaults to the page title
    store_id: int | None = None  # null = shared across all stores


class SuggestionOut(Out):
    id: int
    message_id: int
    status: str
    category: str | None
    priority: str | None
    sentiment: str | None
    language: str | None
    summary: str
    suggested_reply: str
    missing_info: list
    documents_used: list
    error: str | None
    rating: int | None


class RatingIn(In):
    rating: int = Field(ge=-1, le=1)  # 1 helpful, -1 not helpful, 0 clears


class CannedResponseOut(Out):
    id: int
    store_id: int | None
    title: str
    body_text: str
    is_active: bool


class CannedResponseIn(In):
    title: str = Field(min_length=1, max_length=200)
    store_id: int | None = None  # null = shared across all stores
    body_text: str = Field(min_length=1, max_length=20000)


class CannedResponsePatch(In):
    title: str | None = Field(None, min_length=1, max_length=200)
    store_id: int | None = None
    body_text: str | None = Field(None, min_length=1, max_length=20000)
    is_active: bool | None = None


# ---- Reports (Phase 3) ----


class ReportStoreRow(Out):
    id: int
    name: str
    created: int
    resolved: int


class Report(Out):
    from_date: datetime
    to_date: datetime
    created: int
    resolved: int
    first_response_sla_pct: float | None  # % of created tickets not breached; null when none
    median_first_response_min: float | None  # wall-clock, not business minutes
    recoveries: int  # missed emails recovered by reconciliation (mailbox-wide)
    ai_ready: int
    ai_used: int
    ai_helpful: int
    ai_not_helpful: int
    by_store: list[ReportStoreRow]


# ---- Auto-assignment settings + executive performance ----


class AssignmentSettings(In):
    enabled: bool = True  # turn auto-assignment on/off
    strategy: Literal["least_loaded", "round_robin"] = "least_loaded"


class ExecPerformanceRow(Out):
    id: int
    name: str
    email: str
    assigned: int   # tickets assigned to them, created in range
    resolved: int   # tickets they resolved in range
    open: int       # tickets currently open and assigned to them
    reopened: int   # tickets of theirs reopened in range


class ExecPerformance(Out):
    from_date: datetime
    to_date: datetime
    rows: list[ExecPerformanceRow]


class AuditLogOut(Out):
    id: int
    actor_user_id: int | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    summary: str
    data: dict
    created_at: datetime


class LlmSettingsOut(Out):
    provider: str | None
    model: str | None
    base_url: str | None
    configured: bool
    apply_priority: bool
    embedding_model: str | None  # set = semantic KB retrieval is on
    embedding_base_url: str | None
    embedding_configured: bool  # a dedicated embedding key is saved


class LlmSettingsIn(In):
    provider: Literal["anthropic", "openai"]
    model: str | None = Field(None, max_length=100)
    base_url: str | None = Field(None, max_length=300)  # for OpenAI-compatible endpoints
    api_key: str | None = Field(None, max_length=500)  # omit to keep the saved key; "" clears it
    apply_priority: bool | None = None  # let the AI set ticket priority; omit to keep current
    embedding_model: str | None = Field(None, max_length=100)  # "" turns semantic retrieval off
    embedding_base_url: str | None = Field(None, max_length=300)  # optional separate embeddings endpoint
    embedding_api_key: str | None = Field(None, max_length=500)  # optional; omit to keep, "" clears
