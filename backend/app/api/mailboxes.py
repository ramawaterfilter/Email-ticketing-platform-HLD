"""Admin: connect Google mailboxes and map each support address to a store (LLD 6.4)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..config import settings
from ..db import get_db
from ..errors import ApiError
from ..gmail.client import DESK_LABEL, SCOPES, client_for
from ..models import Mailbox, Store, SupportAddress, SyncRun
from ..schemas import MailboxDetail, MailboxOut, MailboxPatch, SupportAddressOut, SupportAddressPatch

router = APIRouter(tags=["mailboxes"], dependencies=[Depends(require_role("admin"))])

REDIRECT_PATH = "/api/v1/mailboxes/oauth/callback"

import os as _os

# Google always expands the gmail.modify request with openid/email/profile, so the granted
# scope never matches what we asked for. Relax oauthlib's strict scope check in every mode
# (http and https) — otherwise the mailbox OAuth callback fails with "Scope has changed".
_os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
# Local development runs over http://localhost; allow oauthlib's non-https callback there only.
if settings.public_url.startswith("http://"):
    _os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


def _last_run(db: Session, mailbox_id: int, kind: str) -> SyncRun | None:
    q = select(SyncRun).where(SyncRun.mailbox_id == mailbox_id).order_by(SyncRun.id.desc())
    if kind == "sync":
        q = q.where(SyncRun.kind.in_(("incremental", "full")))
    else:
        q = q.where(SyncRun.kind == "reconcile")
    return db.scalar(q.limit(1))


@router.get("/mailboxes", response_model=list[MailboxOut])
def list_mailboxes(db: Session = Depends(get_db)):
    return db.scalars(select(Mailbox).order_by(Mailbox.google_email)).all()


# Static paths must be declared before the dynamic /mailboxes/{mailbox_id} route,
# otherwise "connect" gets matched as a mailbox id.
@router.get("/mailboxes/connect")
def connect(request: Request):
    flow = _flow()
    state = secrets.token_urlsafe(24)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true", state=state)
    request.session["mailbox_oauth_state"] = state
    # Carry the PKCE verifier to the callback, which builds a fresh Flow object.
    request.session["mailbox_code_verifier"] = flow.code_verifier
    return RedirectResponse(url)


@router.get("/mailboxes/{mailbox_id}", response_model=MailboxDetail)
def get_mailbox(mailbox_id: int, db: Session = Depends(get_db)):
    mailbox = db.get(Mailbox, mailbox_id)
    if mailbox is None:
        raise ApiError(404, "not_found", "Mailbox not found")
    addresses = db.scalars(
        select(SupportAddress).where(SupportAddress.mailbox_id == mailbox_id).order_by(SupportAddress.address)
    ).all()
    return MailboxDetail(
        **MailboxOut.model_validate(mailbox).model_dump(),
        addresses=[SupportAddressOut.model_validate(a) for a in addresses],
        last_sync_run=_last_run(db, mailbox_id, "sync"),
        last_reconcile=_last_run(db, mailbox_id, "reconcile"),
    )


@router.patch("/mailboxes/{mailbox_id}", response_model=MailboxOut)
def update_mailbox(mailbox_id: int, body: MailboxPatch, db: Session = Depends(get_db)):
    mailbox = db.get(Mailbox, mailbox_id)
    if mailbox is None:
        raise ApiError(404, "not_found", "Mailbox not found")

    if body.backfill_days is not None:
        mailbox.backfill_days = body.backfill_days
    if body.status == "active":
        default = db.scalar(
            select(SupportAddress).where(
                SupportAddress.mailbox_id == mailbox_id, SupportAddress.is_default.is_(True)
            )
        )
        # Guarantee every future ticket has a store: don't switch on until the fallback is mapped.
        if default is None or default.store_id is None:
            raise ApiError(400, "default_unmapped", "Map the mailbox's default address to a store first")
        mailbox.status = "active"
        # The worker backfills recent mail on the next tick (backfilled_at is still null).
    elif body.status == "disabled":
        mailbox.status = "disabled"
    db.commit()
    return mailbox


@router.post("/mailboxes/{mailbox_id}/reconcile", response_model=MailboxOut)
def reconcile_now(mailbox_id: int, db: Session = Depends(get_db)):
    from ..gmail.sync import reconcile_mailbox

    mailbox = db.get(Mailbox, mailbox_id)
    if mailbox is None:
        raise ApiError(404, "not_found", "Mailbox not found")
    # ponytail: runs inline; for a huge mailbox move this to the worker.
    reconcile_mailbox(db, mailbox, client_for(mailbox))
    return mailbox


@router.get("/support-addresses", response_model=list[SupportAddressOut])
def list_addresses(db: Session = Depends(get_db)):
    return db.scalars(select(SupportAddress).order_by(SupportAddress.address)).all()


@router.patch("/support-addresses/{address_id}", response_model=SupportAddressOut)
def update_address(address_id: int, body: SupportAddressPatch, db: Session = Depends(get_db)):
    address = db.get(SupportAddress, address_id)
    if address is None:
        raise ApiError(404, "not_found", "Address not found")
    data = body.model_dump(exclude_unset=True)
    if "store_id" in data and data["store_id"] is not None and db.get(Store, data["store_id"]) is None:
        raise ApiError(400, "unknown_store", "That store does not exist")
    for field, value in data.items():
        setattr(address, field, value)
    db.commit()
    return address


# ---- Google OAuth to connect a mailbox (needs Google credentials configured) ----


def _flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    if not settings.google_client_id:
        raise ApiError(400, "google_not_configured", "Set DESK_GOOGLE_CLIENT_ID / _SECRET to connect mailboxes")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = f"{settings.public_url}{REDIRECT_PATH}"
    return flow



@router.get("/mailboxes/oauth/callback")
def oauth_callback(request: Request, db: Session = Depends(get_db)):
    from ..crypto import encrypt

    if request.query_params.get("state") != request.session.pop("mailbox_oauth_state", None):
        raise ApiError(400, "bad_state", "Sign-in state did not match; please try again")
    flow = _flow(state=request.query_params.get("state"))
    flow.code_verifier = request.session.pop("mailbox_code_verifier", None)
    # Behind a TLS-terminating proxy (Caddy) the callback reaches us over plain http, but
    # oauthlib refuses a non-https authorization_response. Restore the real external scheme.
    auth_response = str(request.url)
    if settings.public_url.startswith("https://") and auth_response.startswith("http://"):
        auth_response = "https://" + auth_response[len("http://") :]
    flow.fetch_token(authorization_response=auth_response)
    client = _client_from_credentials(flow.credentials)

    profile = client.profile_get()
    email = profile["emailAddress"].lower()
    mailbox = db.scalar(select(Mailbox).where(Mailbox.google_email == email)) or Mailbox(google_email=email)
    mailbox.refresh_token_enc = encrypt(flow.credentials.refresh_token)
    mailbox.status = "pending"
    mailbox.desk_label_id = client.label_ensure(DESK_LABEL)
    mailbox.last_history_id = str(profile["historyId"])
    db.add(mailbox)
    db.flush()
    _sync_addresses(db, mailbox, client)
    db.commit()
    return RedirectResponse("/admin/emails")


def _client_from_credentials(credentials):
    from googleapiclient.discovery import build

    from ..gmail.client import RealGmailClient

    return RealGmailClient(build("gmail", "v1", credentials=credentials, cache_discovery=False))


def _sync_addresses(db: Session, mailbox: Mailbox, client) -> None:
    """One support_address per Gmail send-as address, all unmapped until an admin assigns a store."""
    existing = {
        a.address: a
        for a in db.scalars(select(SupportAddress).where(SupportAddress.mailbox_id == mailbox.id))
    }
    for entry in client.send_as_list():
        addr = entry["sendAsEmail"].lower()
        if addr in existing:
            continue
        db.add(
            SupportAddress(
                address=addr,
                mailbox_id=mailbox.id,
                is_default=bool(entry.get("isPrimary")),
                display_name=entry.get("displayName", ""),
            )
        )
