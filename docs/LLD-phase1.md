# LLD: Phase 1 — "Never miss an email"

Low-level design for Phase 1 of the Rama Customer Support Desk. Scope: Gmail sync, reconciliation, tickets, stores, assignment, reply from the app, SLA alerts, manager board. LLM suggestions are Phase 2 and are only mentioned where Phase 1 must leave room for them.

Parent document: [HLD.md](HLD.md).

## 1. Refinements to the HLD

Decisions made while detailing the design. Each one is simpler or safer than the HLD wording.

| # | HLD said | LLD decides | Why |
|---|---|---|---|
| 1 | Store comes from the mailbox | Store comes from the **address** the email was delivered to | One Gmail mailbox can receive mail for several addresses (aliases). Mapping by address covers both real mailboxes and aliases. |
| 2 | Reconciliation searches Gmail for unlabelled mail | Reconciliation compares Gmail message IDs with the database | Does not depend on label search syntax, and also catches a row lost from the database. The `Desk/Ticketed` label stays as visible proof in Gmail. |
| 3 | Worker reads a PostgreSQL job table | Phase 1 worker is three timed loops, no job table | Nothing in Phase 1 needs a queue. The job table arrives in Phase 2 for LLM drafts. |
| 4 | Async back end | Plain synchronous Python; FastAPI runs handlers in its thread pool | 15 users and one Gmail call per mailbox per minute. Sync code is easier to write, test and debug. |
| 5 | Attachments in file storage | Attachments are streamed from Gmail on demand | Gmail already stores them. No file storage needed in Phase 1. |
| 6 | (not covered) | Gmail **Spam** is also synced, into a "Spam review" queue | Customer emails landing in spam are a real source of missed emails. |

## 2. Repository layout

```
backend/
  app/
    main.py            FastAPI app, routers, middleware
    config.py          settings from environment (pydantic-settings)
    db.py              SQLAlchemy engine + session
    models.py          all tables
    schemas.py         Pydantic request/response models
    auth.py            Google sign-in, session cookie, role checks
    api/               tickets.py, dashboard.py, admin.py, mailboxes.py
    gmail/             client.py (thin wrapper), sync.py, ingest.py, send.py, mime.py
    tickets/           service.py (state rules), assignment.py, sla.py
    alerts.py          email / Google Chat alerts
    worker.py          timed loops: sync, SLA, nightly reconcile
  alembic/             database migrations
  tests/
frontend/
  src/                 pages/, components/, api/ (generated client)
docker-compose.yml     api, worker, postgres, caddy
.env.example
docs/
```

Libraries: `fastapi`, `sqlalchemy` 2 + `psycopg`, `alembic`, `pydantic-settings`, `google-api-python-client` + `google-auth-oauthlib`, `authlib` (Google sign-in), `cryptography` (token encryption), `pytest`. Front end: React, TypeScript, Vite, React Router, TanStack Query, Mantine (UI components), `openapi-typescript` (typed client), DOMPurify.

## 3. Database schema

Conventions: `id` is a `bigint` identity primary key; all times are `timestamptz` in UTC; email addresses are `citext` (case-insensitive); status-like columns are `text` with a `CHECK` constraint (easier to migrate than PostgreSQL enums).

### 3.1 Stores, users, addresses

```
store
  id, name (unique), is_active
  signature_text            appended to every reply from this store
  created_at

app_user
  id, email (unique), name, google_sub (unique, null until first login)
  role                      admin | manager | executive
  is_active                 false = cannot log in
  is_available              executive toggles; false = receives no new tickets
  max_open_tickets          default 30
  last_assigned_at          for fair round-robin

user_store                  (user_id, store_id) primary key
                            no rows for a user = works on all stores

mailbox                     one connected Google account
  id, google_email (unique)
  refresh_token_enc         encrypted OAuth refresh token
  status                    pending | active | error | disabled
  last_history_id           Gmail sync cursor
  last_sync_at, last_sync_error
  desk_label_id             Gmail id of the Desk/Ticketed label

support_address             one address customers write to
  id, address (unique)
  mailbox_id                the mailbox that receives it
  store_id                  null until an admin maps it
  is_default                exactly one per mailbox; used when no address matches
  display_name              shown as the sender name on replies
```

Rule: a mailbox can only be switched to `active` when its default address has a store. Every inbound email therefore always resolves to a store.

### 3.2 Customers, tickets, messages

```
customer
  id, email (unique), name, first_seen_at

ticket
  id, number (unique sequence, shown as #10234)
  store_id, mailbox_id, support_address_id, customer_id
  gmail_thread_id
  subject
  queue                     customer | other | spam
  status                    new | open | waiting_customer | resolved | closed
  priority                  low | normal | high | urgent      (default normal)
  assignee_id               null = unassigned
  tags                      text[]
  -- SLA
  awaiting_reply_since      set when a customer message needs an answer
  reply_due_at              deadline for that answer; null when nothing is owed
  resolution_due_at
  resolution_remaining_min  saved while paused in waiting_customer
  first_responded_at, resolved_at, closed_at
  reply_warned_at, reply_breached_at, resolution_breached_at   (alert bookkeeping)
  -- flags
  delivery_failed           our reply bounced
  previous_ticket_id        set when a closed thread comes back as a new ticket
  last_customer_message_at, last_agent_message_at
  created_at, updated_at

  indexes: (mailbox_id, gmail_thread_id), (assignee_id, status),
           (store_id, status), (reply_due_at) where reply_due_at is not null,
           full-text index on subject

message
  id, ticket_id, mailbox_id
  direction                 inbound | outbound | note
  gmail_message_id, gmail_thread_id      null for notes
  rfc_message_id, in_reply_to, references_header
  from_address, from_name, to_addresses (jsonb), cc_addresses (jsonb)
  subject, body_text, body_html, snippet
  headers                   jsonb, only the headers used for classification
  is_auto                   auto-reply / bounce / bulk
  author_user_id            who sent or wrote it (null for inbound, or sent from Gmail directly)
  client_request_id         unique; makes "Send" safe to retry
  send_status               null | sending | sent | failed
  gmail_labelled_at         when Desk/Ticketed was applied
  sent_at, created_at

  UNIQUE (mailbox_id, gmail_message_id)      <- the no-duplicates guarantee

attachment
  id, message_id, filename, mime_type, size_bytes
  gmail_attachment_id       used to fetch the bytes from Gmail on demand
  content_id                for inline images
```

Raw email source is not stored: Gmail keeps the original and it can be re-fetched by `gmail_message_id`.

### 3.3 Configuration and history

```
sla_policy
  id, store_id (unique, null = default for all stores)
  first_response_min, next_response_min, resolution_min

setting                     key (primary key), value (jsonb)
  'business_hours'          { timezone, week: {mon: ["10:00","19:00"], ...}, holidays: [dates] }
  'alerts'                  { manager_emails: [...], google_chat_webhook: "..." }

ticket_event                audit trail, append-only
  id, ticket_id, actor_user_id (null = system), type, data (jsonb), created_at
  types: created, assigned, status_changed, store_changed, priority_changed,
         replied, note_added, reopened, sla_warned, sla_breached, delivery_failed

sync_run                    one row per sync or reconciliation
  id, mailbox_id, kind (incremental | full | reconcile)
  started_at, finished_at, status (ok | error)
  messages_seen, messages_ingested, recovered_count, error
```

## 4. Gmail integration

All Gmail calls go through `gmail/client.py`, a thin wrapper with about ten methods (`history_list`, `messages_list`, `message_get`, `message_send`, `message_add_label`, `attachment_get`, `label_ensure`, `profile_get`, `send_as_list`). Tests swap it for an in-memory fake.

### 4.1 Connecting a mailbox (admin, once per mailbox)

1. Admin clicks **Connect email** → Google consent screen (`access_type=offline`, `prompt=consent`, scope `gmail.modify`). The OAuth app is **Internal**, so only accounts in the company Workspace can be connected.
2. Callback: check the `state` value, exchange the code, encrypt and store the refresh token.
3. Ensure the label `Desk/Ticketed` exists; store its id.
4. Read the mailbox's send-as list → create one `support_address` row per address (primary marked `is_default`), all unmapped.
5. Read the mailbox profile → store its current `historyId` as the sync starting point.
6. Mailbox is `pending`. Admin maps the addresses to stores, picks a backfill window (default 7 days), then switches it to `active`.

**Backfill on activation:** threads from the chosen window are imported. A thread whose latest message is ours becomes a `resolved` ticket; a thread whose latest message is from the customer becomes an `open` ticket with the SLA clock starting now. These are exactly the emails that may already have been missed.

### 4.2 Incremental sync (worker, every 60 seconds per active mailbox)

```
take PostgreSQL advisory lock for this mailbox      (skip if another worker holds it)
start sync_run

ids = history_list(start=last_history_id, types=[messageAdded])   # all pages
for each id not already in message table:
    msg = message_get(id, format=full)        # 404 = deleted meanwhile → skip
    skip if labelled DRAFT or CHAT
    ingest(msg)                               # one DB transaction, see 4.4
    message_add_label(id, Desk/Ticketed) ; set gmail_labelled_at

save last_history_id = newest historyId, last_sync_at = now     # only after all ingested
finish sync_run
```

Crash safety: the cursor moves only after every message is committed. If the worker dies mid-run, the next run repeats the same range and the unique key `(mailbox_id, gmail_message_id)` drops the repeats.

Error handling:

| Gmail answer | Action |
|---|---|
| 404 on `history_list` (cursor too old) | Run a **full sync** (4.3 logic over 14 days), then reset the cursor |
| 401 / `invalid_grant` (access revoked) | `status = error`, alert admins immediately, stop syncing this mailbox |
| 429 / 5xx / network | Record `last_sync_error`, retry on the next tick with backoff up to 5 minutes |
| No successful sync for 10 minutes | Alert admins ("support@… is not syncing") |

### 4.3 Reconciliation (nightly at 02:00, and on demand from the admin screen)

```
for each active mailbox:
    cursor_before = profile_get().historyId
    gmail_ids = messages_list("newer_than:14d")  +  messages_list("in:spam newer_than:14d")
    missing   = gmail_ids − ids in message table
    for each missing id: fetch, ingest, label
    re-apply the label to messages where gmail_labelled_at is null
    write sync_run(kind=reconcile, recovered_count=len(missing))

if any recovered_count > 0: alert managers with the list of recovered tickets
```

A healthy night ends with `recovered_count = 0` for every mailbox. The admin screen shows the last result per mailbox.

### 4.4 Ingesting one message

Runs inside a single database transaction.

**Step 1 — direction.** Gmail label `SENT` present → `outbound`. Otherwise `inbound`.

**Step 2 — store.** Take the first address from `Delivered-To`, then `To`, then `Cc` that matches a `support_address`; otherwise use the mailbox's default address. The store is that address's store.

**Step 3 — classify (inbound only).**

| Signal | Result |
|---|---|
| Gmail label `SPAM` | `queue = spam` |
| From `mailer-daemon@` / `postmaster@`, or content type `multipart/report` | Bounce. If the thread has a ticket: attach, set `delivery_failed`, reopen, alert the assignee. Otherwise `queue = other` |
| `Auto-Submitted` other than `no`, `X-Autoreply`, `Precedence: bulk/junk/list/auto_reply`, `List-Id`, `List-Unsubscribe` | `is_auto = true`. New thread → `queue = other`. Existing ticket (e.g. customer's out-of-office) → attach without reopening or restarting the SLA |
| None of the above | `queue = customer` |

Nothing is discarded. `other` and `spam` tickets are unassigned and have no SLA, and one click ("This is a customer") moves them to the customer queue and assigns them.

**Step 4 — find or create the ticket.** Look up the newest ticket with the same `(mailbox_id, gmail_thread_id)`:

| Found | Action |
|---|---|
| None | Create ticket, auto-assign (5.2), start SLA (5.3) |
| Open ticket | Attach the message |
| `waiting_customer` or `resolved` | Attach, status → `open`, start reply clock |
| `closed` within 30 days | Attach, reopen, give back to the previous assignee if available, else auto-assign |
| `closed` more than 30 days ago | Create a new ticket with `previous_ticket_id` set |

**Step 5 — outbound messages not sent through the app** (an executive replied straight from Gmail): attach to the ticket, clear the reply clock, set `first_responded_at` if empty, status → `waiting_customer`. The ticket stays accurate even when someone bypasses the app.

**Step 6 — save** the customer (upsert by email), the message, attachment metadata and a `ticket_event`.

### 4.5 Sending a reply

```
1. Reject if a newer message arrived since the executive opened the ticket   → 409, UI shows it
2. If client_request_id already exists → return that message (safe retry, no second email)
3. Insert message row: direction=outbound, send_status=sending ; commit
4. Build MIME: From = support address the customer wrote to (display name = store),
   To = customer (Reply-To if present), Subject "Re: …",
   In-Reply-To / References from the thread, header X-Desk-Request-Id,
   body + store signature, attachments
5. message_send(raw, threadId)
6. Success → send_status=sent, save gmail_message_id, update ticket, add label, write event
   Failure → send_status=failed, show the error with a Retry button
```

Two protections against a customer receiving the same reply twice:

- The Send button carries a `client_request_id` generated when the reply box opens, so a double-click or retry cannot create a second email.
- If Gmail accepted the email but our database update failed, the next sync sees the sent message, matches it by `X-Desk-Request-Id` and marks the row `sent`. A row stuck in `sending` cannot be retried until a sync has confirmed the email is not in Gmail.

After a successful send the ticket moves to `waiting_customer` (or `resolved` if the executive chose "Send and resolve"), the reply clock is cleared and the resolution clock is paused.

## 5. Ticket rules

### 5.1 Status transitions

```
            customer email            executive replies             executive resolves
  (none) ───────────────► new ─► open ───────────────► waiting_customer ───────────► resolved
                                   ▲                         │                          │
                                   └──── customer replies ───┴──────────────────────────┤
                                                                                        │ 7 days, no reply
                                                                                        ▼
                                                                                     closed
```

- `new` → `open` when an executive first opens or is assigned the ticket.
- `resolved` → `closed` automatically after 7 days without a customer reply (worker loop).
- All transitions go through `tickets/service.py`, which is the only code allowed to change `status`, and which writes the `ticket_event`.

### 5.2 Auto-assignment

```
eligible = executives where is_active and is_available
           and (no user_store rows  or  ticket.store in their stores)
           and open ticket count < max_open_tickets
pick     = fewest open tickets ; tie → oldest last_assigned_at
none eligible → ticket stays unassigned and appears on the manager board
```

Runs in the ingest transaction with the candidate rows locked, so two tickets arriving together are not both given to the same person by accident. Managers can reassign at any time; executives can take an unassigned ticket ("Assign to me").

### 5.3 SLA clocks

Two clocks per ticket, both counted in **business minutes** using the `business_hours` setting (timezone, weekly hours, holidays).

| Clock | Starts | Stops | Target from `sla_policy` |
|---|---|---|---|
| Reply | A customer message arrives and none is already pending | Executive replies, or ticket resolved | `first_response_min` for the first message, `next_response_min` afterwards |
| Resolution | Ticket created | Ticket resolved. Paused in `waiting_customer` (remaining minutes saved, restored on reopen) | `resolution_min` |

The policy used is the store's own if it has one, otherwise the default. `sla.py` has one core function, `add_business_minutes(start, minutes) → datetime`, with unit tests for evenings, weekends and holidays.

Proposed defaults, editable in settings: first reply 4 business hours, follow-up reply 8, resolution 48.

### 5.4 Escalation (worker, every minute)

| Condition | Action | Sent once, tracked by |
|---|---|---|
| Reply due within 30 business minutes | Email the assignee | `reply_warned_at` |
| Reply deadline passed | Email + Google Chat to managers; ticket shown red | `reply_breached_at` |
| Resolution deadline passed | Email managers | `resolution_breached_at` |
| Unassigned customer ticket older than 15 minutes | Email managers | event `unassigned_alerted` |

Alert emails are sent through the Gmail API from one designated mailbox.

## 6. REST API

Base path `/api/v1`. JSON in and out. Session cookie authentication. FastAPI generates the OpenAPI document from these routes; that document is the contract, and the React client types are generated from it. The table below is the intended contract to build against.

Conventions: lists use cursor pagination (`?cursor=…&limit=50` → `{items, next_cursor}`); errors are `{ "detail": "...", "code": "..." }`; `409` means the ticket changed since it was loaded; every write requires the header `X-Requested-With: desk`.

### 6.1 Session

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/auth/google/login` | anyone | Redirect to Google sign-in |
| GET | `/auth/google/callback` | anyone | Finish sign-in, set session cookie |
| POST | `/auth/logout` | user | Clear session |
| GET | `/me` | user | Current user, role, stores |
| PATCH | `/me` | user | `{is_available}` |

### 6.2 Tickets

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/tickets` | user | List. Filters: `view` (mine, unassigned, all), `queue`, `status`, `store_id`, `assignee_id`, `sla` (due_soon, breached), `q` (subject, customer email, ticket number) |
| GET | `/tickets/{id}` | user | Ticket, messages, events, and the customer's other open tickets |
| PATCH | `/tickets/{id}` | user | Any of `status`, `priority`, `assignee_id`, `store_id`, `queue`, `tags` |
| POST | `/tickets/{id}/replies` | user | `{body_text, cc, attachment_ids, client_request_id, last_seen_message_id, then: "wait" or "resolve"}` → sends email (4.5) |
| POST | `/tickets/{id}/replies/{message_id}/retry` | user | Retry a failed send |
| POST | `/tickets/{id}/notes` | user | `{body_text}` internal note, never emailed |
| POST | `/uploads` | user | Upload a file to attach to a reply → `{attachment_id}` (kept 24 hours) |
| GET | `/attachments/{id}` | user | Stream the file from Gmail |

Executives see tickets of their stores only; an executive with no store restriction, managers and admins see all. `assignee_id` to another person requires manager or admin; "assign to me" is open to executives.

### 6.3 Manager

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/dashboard` | manager, admin | Counts: unassigned, due soon, breached, open by executive, open by store, last sync per mailbox |

### 6.4 Admin

| Method | Path | Purpose |
|---|---|---|
| GET, POST | `/stores` | List, create |
| PATCH | `/stores/{id}` | Name, signature, active |
| GET, POST | `/users` | List; add a person by email with a role (they can then sign in) |
| PATCH | `/users/{id}` | Role, active, stores, max open tickets |
| GET | `/mailboxes` | List with status, last sync, last reconciliation result |
| GET | `/mailboxes/connect` | Start Google consent for a new mailbox |
| GET | `/mailboxes/oauth/callback` | Finish connecting (4.1) |
| PATCH | `/mailboxes/{id}` | `{status: active or disabled, backfill_days}` |
| POST | `/mailboxes/{id}/reconcile` | Run reconciliation now |
| GET | `/mailboxes/{id}/sync-runs` | Recent sync history |
| GET | `/support-addresses` | All addresses with their store |
| PATCH | `/support-addresses/{id}` | `{store_id, display_name}` — the email → store mapping |
| GET, PUT | `/sla-policies` | Default and per-store targets |
| GET, PUT | `/settings/business-hours` | Timezone, weekly hours, holidays |
| GET, PUT | `/settings/alerts` | Manager emails, Google Chat webhook |

### 6.5 Monitoring (no login, no customer data)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | API and database are up |
| GET | `/health/sync` | `200` if every active mailbox synced in the last 10 minutes, else `503`. Pointed at by an external uptime monitor, so an alert still fires if the whole server is down |

## 7. React app

### 7.1 Screens

| Route | Who | What it shows |
|---|---|---|
| `/login` | anyone | "Sign in with Google" |
| `/tickets` | all | Inbox. Tabs: **Mine**, **Unassigned**, **All**, **Other**, **Spam review**. Filters: store, status, SLA. Search box. Each row: ticket number, customer, subject, store badge, assignee, time left on the reply clock (green / amber / red) |
| `/tickets/:id` | all | Left: the email thread, internal notes inline, reply box at the bottom. Right: customer details and their other tickets, ticket fields (status, priority, store, assignee, tags), activity log |
| `/board` | manager, admin | Tiles: unassigned, due soon, breached. Table of open tickets per executive and per store. Mailbox sync health |
| `/admin/stores` | admin | Stores and their signatures |
| `/admin/emails` | admin | Connect a mailbox; table of every address with a **store dropdown**; sync status; last reconciliation result; "Run check now" |
| `/admin/users` | admin | People, roles, stores, ticket cap |
| `/admin/sla` | admin | SLA targets, business hours, holidays, alert recipients |

### 7.2 Behaviour

- **Data:** TanStack Query. The inbox refetches every 20 seconds and on window focus; an open ticket every 15 seconds.
- **Reply box:** plain text area in Phase 1 (rich text only if executives ask for it). A `client_request_id` is created when the box opens. Unsent text is kept in `localStorage` per ticket so a refresh does not lose a half-written reply.
- **New message while replying:** the API returns `409`; the app shows the new message above the reply box and asks the executive to review before sending.
- **Showing customer email HTML:** cleaned with DOMPurify and rendered in a sandboxed `<iframe>` with scripts disabled. Remote images are blocked until the executive clicks "Show images" (stops tracking pixels).
- **Phase 2 space:** the ticket view reserves a panel above the reply box for the suggested answer and the list of documents it used.

## 8. Security

| Area | Design |
|---|---|
| Sign-in | Google sign-in. Accepted only if the Google account's domain is in the allowed list **and** the email already exists in `app_user` as active. The first admin comes from the `BOOTSTRAP_ADMIN_EMAIL` setting |
| Session | Signed cookie, `HttpOnly`, `Secure`, `SameSite=Lax`, 12-hour lifetime. The user's `is_active` and role are re-read from the database on every request, so removing someone takes effect immediately |
| Cross-site requests | Same-origin only; writes need the `X-Requested-With` header |
| Roles | Checked in the API on every route (table in section 6); the React app only hides buttons |
| Store scope | Ticket queries always include the user's store filter in the SQL, in one shared function |
| Gmail tokens | Encrypted with a key from the environment before being stored; never returned by any API |
| Email content | Treated as untrusted: sanitised for display, never executed, never used to build SQL or shell commands |
| Audit | Every send, assignment, status and store change writes a `ticket_event` with the actor |
| Secrets | In `.env` on the server, not in the repository; `.env.example` lists the names only |

## 9. Worker

One process, three loops, plain Python:

| Loop | Interval | Does |
|---|---|---|
| Sync | 60 seconds | Incremental sync for each active mailbox (4.2); sync-health alert |
| SLA | 60 seconds | Escalation checks (5.4); auto-close resolved tickets after 7 days |
| Nightly | 02:00 | Reconciliation (4.3); delete expired uploads |

Each loop catches and logs its own errors so one failing mailbox never stops the others. A single worker instance is enough; the per-mailbox advisory lock keeps a second instance harmless if one is started by mistake.

## 10. Tests

Run with `pytest` against a real PostgreSQL (Docker) and the in-memory fake Gmail client. These map to the HLD acceptance checks.

| Test | Proves |
|---|---|
| Ingest the same message twice | One row, one ticket |
| Worker stops after ingesting 3 of 10 messages, then reruns | All 10 present, no duplicates, cursor correct |
| `history_list` returns 404 | Full sync runs and recovers everything |
| Message exists in Gmail but not in the database | Reconciliation ingests it and reports `recovered_count = 1` |
| Token revoked | Mailbox goes to `error`, alert sent, other mailboxes keep syncing |
| Email to an alias mapped to store B | Ticket belongs to store B |
| Reply on resolved / closed (10 days) / closed (40 days) ticket | Reopen / reopen / new linked ticket |
| Auto-reply and bounce | Correct queue; bounce sets `delivery_failed` |
| Two tickets arrive together | Assigned to two different executives |
| `add_business_minutes` across evening, weekend, holiday | Correct deadlines |
| Send with the same `client_request_id` twice | One email sent |
| Executive of store A requests a store B ticket | `404` |

## 11. Build order

| Milestone | Delivers | Can be checked by |
|---|---|---|
| M1 Skeleton | Repo, Docker Compose, database migrations, Google sign-in, stores and users admin | Signing in and creating a store |
| M2 Gmail core | Connect mailbox, address → store mapping, sync, ingest, reconciliation, tests | Sending a test email and seeing the ticket row; sync history on the admin screen |
| M3 Ticket desk | Inbox, ticket view, reply, notes, assignment | Answering a real email end to end from the app |
| M4 SLA and board | SLA clocks, escalation alerts, manager board, sync-health alerts | Letting a test ticket breach and receiving the alert |
| M5 Go live | Backfill, backups, uptime monitor, deployment, run the HLD acceptance checks | Team switches from Gmail to the desk |

## 12. Open questions

These affect M2 and should be answered before it starts.

1. **Are all store email addresses in the same Google Workspace account?** An Internal Google app can only connect mailboxes of one Workspace. Separate Workspace accounts would need one Google app per account.
2. **What is each store address: a real mailbox with its own login, an alias of another mailbox, or a Google Group?** Mailboxes and aliases are covered by this design. A Google Group has no mailbox the Gmail API can read, so its mail would have to be delivered to a real mailbox.
3. **Business hours, timezone, holidays and SLA targets** (defaults in 5.3 are placeholders).
4. **Alert channel:** email only, or also a Google Chat space?

