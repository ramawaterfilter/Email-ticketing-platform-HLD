# Rama Support Desk

Gmail → tickets → LLM-assisted replies. Design: [docs/HLD.md](docs/HLD.md), [docs/LLD-phase1.md](docs/LLD-phase1.md).

**Status:** Phase 1 complete (milestones 1–5). Done: sign-in, stores and people admin (M1); Gmail sync, ingestion, reconciliation, ticketing, auto-assignment, email→store mapping (M2); ticket inbox, ticket view, replies, internal notes, dark mode (M3); SLA clocks in business hours, escalation and sync-health alerts, manager board, SLA/alerts admin (M4); backfill on connect, sync-health endpoint, production config guard, backups and a deployment guide (M5). Deploy with **[docs/DEPLOY.md](docs/DEPLOY.md)**. Next (product Phase 2): LLM-suggested replies per store.

## Run locally

Needs Docker Desktop (running), Python 3.12 with `uv`, and Node 24.

```
copy .env.example .env                 # then fill DESK_SESSION_SECRET, domain and admin email
docker compose up -d db                # database

cd backend
uv venv && uv pip install -r requirements.txt
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --port 8000

cd frontend                            # in a second terminal
npm install
npm run dev                            # open http://localhost:5173

.venv\Scripts\python -m app.worker     # third terminal: Gmail sync + nightly reconciliation
```

The worker only does anything once a mailbox is connected and set to active. To connect a
real mailbox locally you also need the Google client libraries and a `DESK_TOKEN_KEY`:

```
cd backend
uv pip install google-api-python-client google-auth google-auth-oauthlib
.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put that key in `.env` as `DESK_TOKEN_KEY` (it encrypts stored Gmail tokens). In Docker these
libraries are already installed from `requirements.txt`.

With `DESK_DEV_LOGIN_ENABLED=true` the login page shows a "Development only" email box, so you can sign in without Google. The email must be the bootstrap admin or a person an admin has added.

## Tests

```
cd backend
.venv\Scripts\python -m pytest
```

Tests need the database container running and use their own database (`desk_test`).

## Google sign-in setup (once)

1. Google Cloud Console → create a project → **OAuth consent screen** → user type **Internal**.
2. **Credentials** → Create credentials → OAuth client ID → **Web application**.
3. Authorised redirect URI: `<DESK_PUBLIC_URL>/api/v1/auth/google/callback`
   (locally `http://localhost:5173/api/v1/auth/google/callback`).
4. Put the client ID and secret in `.env` as `DESK_GOOGLE_CLIENT_ID` and `DESK_GOOGLE_CLIENT_SECRET`.
5. The same OAuth client also connects support mailboxes. Add a second authorised redirect URI
   `<DESK_PUBLIC_URL>/api/v1/mailboxes/oauth/callback`, and enable the **Gmail API** for the project.
   An admin connects each mailbox from the **Email accounts** screen, then maps its addresses to stores.

## After changing the API

```
cd backend
.venv\Scripts\python -c "import json; from app.main import app; open('openapi.json','w').write(json.dumps(app.openapi(), indent=2)+'\n')"
cd ..\frontend
npm run gen:api
```

`backend/openapi.json` is the API contract; the React app's types are generated from it.

## Run everything in Docker (as on a server)

```
docker compose --profile app up -d --build
```

This runs the API, the background worker, PostgreSQL and a Caddy proxy (HTTPS + the React app).
For a real production deployment — server prep, the Google OAuth app, `.env`, backups, the uptime
monitor and the go-live checklist — follow **[docs/DEPLOY.md](docs/DEPLOY.md)**.

## Notes

- All settings are read as `DESK_*` on purpose, so a global `DATABASE_URL` from another project is never picked up.
- The database address uses `127.0.0.1`, not `localhost`: on Windows `localhost` can try IPv6 first and stall.
- TypeScript is pinned to version 5 because the API type generator does not support TypeScript 7 yet.
