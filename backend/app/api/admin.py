from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import current_user, require_role
from ..config import settings
from ..db import get_db
from ..errors import ApiError
from ..models import AppUser, Setting, SlaPolicy, Store
from ..schemas import (
    AlertSettings,
    BusinessHours,
    LlmSettingsIn,
    LlmSettingsOut,
    SlaPolicyIn,
    SlaPolicyOut,
    StoreIn,
    StoreOut,
    StorePatch,
    UserIn,
    UserOut,
    UserPatch,
)

router = APIRouter(tags=["admin"])
admin = require_role("admin")


def _commit(db: Session, code: str, detail: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, code, detail)


def _stores(db: Session, ids: list[int]) -> list[Store]:
    stores = db.scalars(select(Store).where(Store.id.in_(ids))).all() if ids else []
    if len(stores) != len(set(ids)):
        raise ApiError(400, "unknown_store", "One of the selected stores does not exist")
    return list(stores)


@router.get("/stores", response_model=list[StoreOut])
def list_stores(db: Session = Depends(get_db), _: AppUser = Depends(current_user)):
    return db.scalars(select(Store).order_by(Store.name)).all()


def _apply_shopify(store: Store, data: dict) -> None:
    """Encrypt and store a Shopify token; an explicit empty token clears the connection."""
    if "shopify_domain" in data:
        store.shopify_domain = (data.pop("shopify_domain") or "").strip() or None
    if "shopify_token" in data:
        from ..crypto import encrypt

        token = (data.pop("shopify_token") or "").strip()
        store.shopify_token_enc = encrypt(token) if token else None


@router.post("/stores", response_model=StoreOut, status_code=201, dependencies=[Depends(admin)])
def create_store(body: StoreIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    store = Store()
    _apply_shopify(store, data)
    for field, value in data.items():
        setattr(store, field, value)
    db.add(store)
    _commit(db, "store_exists", "A store with this name already exists")
    return store


@router.patch("/stores/{store_id}", response_model=StoreOut, dependencies=[Depends(admin)])
def update_store(store_id: int, body: StorePatch, db: Session = Depends(get_db)):
    store = db.get(Store, store_id)
    if store is None:
        raise ApiError(404, "not_found", "Store not found")
    data = body.model_dump(exclude_unset=True)
    _apply_shopify(store, data)
    for field, value in data.items():
        if value is not None:
            setattr(store, field, value)
    _commit(db, "store_exists", "A store with this name already exists")
    return store


@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_role("admin", "manager"))])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(AppUser).order_by(AppUser.email)).all()


@router.post("/users", response_model=UserOut, status_code=201, dependencies=[Depends(admin)])
def create_user(body: UserIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if email.rsplit("@", 1)[-1] not in settings.domains:
        raise ApiError(400, "domain_not_allowed", "This email is outside the allowed company domains")
    user = AppUser(
        email=email,
        name=body.name,
        role=body.role,
        max_open_tickets=body.max_open_tickets,
        stores=_stores(db, body.store_ids),
    )
    db.add(user)
    _commit(db, "user_exists", "This person has already been added")
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), me: AppUser = Depends(admin)):
    user = db.get(AppUser, user_id)
    if user is None:
        raise ApiError(404, "not_found", "User not found")
    data = body.model_dump(exclude_none=True)
    # An admin can never remove their own access, so at least one active admin always remains.
    if user.id == me.id and (data.get("role", "admin") != "admin" or data.get("is_active") is False):
        raise ApiError(400, "self_lockout", "You cannot remove your own admin access")
    if "store_ids" in data:
        user.stores = _stores(db, data.pop("store_ids"))
    for field, value in data.items():
        setattr(user, field, value)
    db.commit()
    return user


# ---- SLA policies and settings (milestone 4) ----


@router.get("/sla-policies", response_model=list[SlaPolicyOut], dependencies=[Depends(admin)])
def list_sla_policies(db: Session = Depends(get_db)):
    from ..tickets.sla import DEFAULT_POLICY

    rows = db.scalars(select(SlaPolicy).order_by(SlaPolicy.store_id.nulls_first())).all()
    if not any(p.store_id is None for p in rows):
        rows = [SlaPolicy(store_id=None, **DEFAULT_POLICY), *rows]  # surface the built-in default
    return rows


@router.put("/sla-policies", response_model=SlaPolicyOut, dependencies=[Depends(admin)])
def upsert_sla_policy(body: SlaPolicyIn, db: Session = Depends(get_db)):
    if body.store_id is not None and db.get(Store, body.store_id) is None:
        raise ApiError(400, "unknown_store", "That store does not exist")
    policy = db.scalar(select(SlaPolicy).where(SlaPolicy.store_id.is_(body.store_id) if body.store_id is None
                                               else SlaPolicy.store_id == body.store_id))
    if policy is None:
        policy = SlaPolicy(store_id=body.store_id)
        db.add(policy)
    policy.first_response_min = body.first_response_min
    policy.next_response_min = body.next_response_min
    policy.resolution_min = body.resolution_min
    db.commit()
    return policy


def _get_setting(db: Session, key: str, default: dict) -> dict:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


def _put_setting(db: Session, key: str, value: dict) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


@router.get("/settings/business-hours", response_model=BusinessHours, dependencies=[Depends(admin)])
def get_business_hours(db: Session = Depends(get_db)):
    from ..tickets.sla import DEFAULT_BUSINESS_HOURS

    return _get_setting(db, "business_hours", DEFAULT_BUSINESS_HOURS)


@router.put("/settings/business-hours", response_model=BusinessHours, dependencies=[Depends(admin)])
def put_business_hours(body: BusinessHours, db: Session = Depends(get_db)):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(400, "bad_timezone", "Unknown timezone")
    _put_setting(db, "business_hours", body.model_dump())
    return body


@router.get("/settings/alerts", response_model=AlertSettings, dependencies=[Depends(admin)])
def get_alerts(db: Session = Depends(get_db)):
    return _get_setting(db, "alerts", {"manager_emails": [], "google_chat_webhook": None})


@router.put("/settings/alerts", response_model=AlertSettings, dependencies=[Depends(admin)])
def put_alerts(body: AlertSettings, db: Session = Depends(get_db)):
    _put_setting(db, "alerts", body.model_dump())
    return body


# ---- AI provider (LLM) settings (Phase 2) ----


@router.get("/settings/llm", response_model=LlmSettingsOut, dependencies=[Depends(admin)])
def get_llm(db: Session = Depends(get_db)):
    from ..llm import get_config

    row = db.get(Setting, "llm")
    v = row.value if row and row.value else {}
    cfg = get_config(db)
    return LlmSettingsOut(
        provider=v.get("provider") or (cfg.provider if cfg else None),
        model=v.get("model") or (cfg.model if cfg else None),
        base_url=v.get("base_url"),
        configured=cfg is not None,
        apply_priority=v.get("apply_priority", True),
    )


@router.put("/settings/llm", response_model=LlmSettingsOut, dependencies=[Depends(admin)])
def put_llm(body: LlmSettingsIn, db: Session = Depends(get_db)):
    from ..crypto import encrypt

    row = db.get(Setting, "llm")
    v = dict(row.value) if row and row.value else {}
    v["provider"] = body.provider
    v["model"] = (body.model or "").strip() or None
    v["base_url"] = (body.base_url or "").strip() or None
    if body.api_key is not None:  # None = keep existing; "" = clear
        key = body.api_key.strip()
        v["api_key_enc"] = encrypt(key) if key else None
    if body.apply_priority is not None:
        v["apply_priority"] = body.apply_priority
    _put_setting(db, "llm", v)
    return get_llm(db)


@router.post("/settings/llm/test", dependencies=[Depends(admin)])
def test_llm(db: Session = Depends(get_db)):
    from ..llm import build_suggester

    suggester = build_suggester(db)
    if suggester is None:
        raise ApiError(400, "not_configured", "Choose a provider and enter an API key first")
    try:
        result = suggester.suggest(
            system=[{"type": "text", "text": "You are a connection test for a support desk."}],
            user_text="A customer said: 'hello'. Draft a one-sentence friendly reply.",
        )
        return {"ok": True, "sample": result.suggested_reply[:160]}
    except Exception as exc:  # noqa: BLE001
        raise ApiError(400, "test_failed", f"The provider rejected the request: {str(exc)[:200]}")
