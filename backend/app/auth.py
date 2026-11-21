from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, get_db
from .errors import ApiError
from .models import AppUser
from .schemas import DevLogin, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


class LoginRefused(Exception):
    """str(exc) is a fixed code shown on the login page: unverified | domain | not_allowed."""


def resolve_login(db: Session, *, email: str, domain: str, verified: bool, sub: str = "", name: str = "") -> AppUser:
    """The one sign-in rule: verified account, allowed Workspace domain, pre-approved active user."""
    email = email.strip().lower()
    if not verified or not email:
        raise LoginRefused("unverified")
    if domain.strip().lower() not in settings.domains:
        raise LoginRefused("domain")

    user = db.scalar(select(AppUser).where(AppUser.email == email))
    if user is None and email == settings.bootstrap_admin_email.strip().lower():
        # is_active set explicitly: column defaults are only filled in when the row is saved.
        user = AppUser(email=email, role="admin", is_active=True)
        db.add(user)
    if user is None or not user.is_active:
        raise LoginRefused("not_allowed")
    if sub and user.google_sub and user.google_sub != sub:
        # Same address, different Google account (deleted and recreated). An admin must re-add the person.
        raise LoginRefused("not_allowed")

    user.google_sub = user.google_sub or sub or None
    user.name = user.name or name
    db.commit()
    return user


def _start_session(request: Request, user: AppUser) -> None:
    request.session.clear()
    request.session["uid"] = user.id


@router.get("/google/login")
async def google_login(request: Request):
    return await oauth.google.authorize_redirect(request, f"{settings.public_url}/api/v1/auth/google/callback")


@router.get("/google/callback")
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/login?error=google")
    info = token.get("userinfo") or {}

    def login() -> AppUser:
        with SessionLocal() as db:
            return resolve_login(
                db,
                email=info.get("email", ""),
                domain=info.get("hd", ""),  # hosted domain: only present for Workspace accounts
                verified=bool(info.get("email_verified")),
                sub=info.get("sub", ""),
                name=info.get("name", ""),
            )

    try:
        user = await run_in_threadpool(login)
    except LoginRefused as refused:
        return RedirectResponse(f"/login?error={refused}")
    _start_session(request, user)
    return RedirectResponse("/")


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)


if settings.dev_login:

    @router.post("/dev-login", response_model=UserOut)
    def dev_login(body: DevLogin, request: Request, db: Session = Depends(get_db)):
        """Local development only: same rule as Google sign-in, without the Google step."""
        try:
            user = resolve_login(db, email=body.email, domain=body.email.rsplit("@", 1)[-1], verified=True)
        except LoginRefused as refused:
            raise ApiError(403, f"login_{refused}", "Sign-in refused")
        _start_session(request, user)
        return user


def current_user(request: Request, db: Session = Depends(get_db)) -> AppUser:
    # Re-read on every request, so deactivating someone takes effect immediately.
    uid = request.session.get("uid")
    user = db.get(AppUser, uid) if uid else None
    if user is None or not user.is_active:
        request.session.clear()
        raise ApiError(401, "not_signed_in", "Not signed in")
    return user


def require_role(*roles: str):
    def check(user: AppUser = Depends(current_user)) -> AppUser:
        if user.role not in roles:
            raise ApiError(403, "forbidden", "You do not have access to this")
        return user

    return check


def require_desk_header(request: Request) -> None:
    """Cross-site request protection: browsers will not add this header to a cross-site form post."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "desk":
        raise ApiError(403, "missing_header", "Missing X-Requested-With header")
