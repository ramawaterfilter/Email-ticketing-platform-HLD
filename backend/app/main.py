from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .api import admin, dashboard, kb, mailboxes, tickets
from .config import settings
from .db import get_db
from .errors import ApiError
from .models import AppUser, Mailbox
from .schemas import MePatch, UserOut

DEV = settings.environment == "dev"

# Fail fast on an unsafe production configuration rather than booting insecurely.
if settings.environment == "prod":
    _problems = []
    if not settings.token_key:
        _problems.append("DESK_TOKEN_KEY is required (it encrypts Gmail tokens)")
    if len(settings.session_secret) < 32:
        _problems.append("DESK_SESSION_SECRET must be at least 32 characters")
    if settings.dev_login_enabled:
        _problems.append("DESK_DEV_LOGIN_ENABLED must be false in production")
    if _problems:
        raise RuntimeError("Unsafe production config: " + "; ".join(_problems))

STALE_SYNC = timedelta(minutes=10)

app = FastAPI(
    title="Rama Support Desk",
    version="0.1.0",
    # API docs are a development aid; they are not served in production.
    openapi_url="/api/v1/openapi.json" if DEV else None,
    docs_url="/api/v1/docs" if DEV else None,
    redoc_url=None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="desk_session",
    max_age=12 * 3600,
    same_site="lax",
    https_only=not DEV,
)


@app.exception_handler(ApiError)
def api_error(_: Request, exc: ApiError):
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status)


api = APIRouter(prefix="/api/v1", dependencies=[Depends(auth.require_desk_header)])


@api.get("/healthz", tags=["monitoring"])
def healthz(db: Session = Depends(get_db)):
    db.execute(text("select 1"))
    return {"ok": True}


@api.get("/health/sync", tags=["monitoring"])
def health_sync(db: Session = Depends(get_db)):
    """503 if any active mailbox hasn't synced in 10 minutes. Point an uptime monitor here,
    so an alert fires even if the whole server (and its own alerting) is down."""
    cutoff = datetime.now(timezone.utc) - STALE_SYNC
    stale = db.scalar(
        select(func.count())
        .select_from(Mailbox)
        .where(
            Mailbox.status == "active",
            or_(
                (Mailbox.last_sync_at.is_(None)) & (Mailbox.created_at < cutoff),
                Mailbox.last_sync_at < cutoff,
            ),
        )
    )
    if stale:
        return JSONResponse({"ok": False, "stale_mailboxes": stale}, status_code=503)
    return {"ok": True}


@api.get("/me", response_model=UserOut, tags=["session"])
def me(user: AppUser = Depends(auth.current_user)):
    return user


@api.patch("/me", response_model=UserOut, tags=["session"])
def update_me(body: MePatch, user: AppUser = Depends(auth.current_user), db: Session = Depends(get_db)):
    user.is_available = body.is_available
    db.commit()
    return user


api.include_router(auth.router)
api.include_router(admin.router)
api.include_router(mailboxes.router)
api.include_router(tickets.router)
api.include_router(dashboard.router)
api.include_router(kb.router)
app.include_router(api)
