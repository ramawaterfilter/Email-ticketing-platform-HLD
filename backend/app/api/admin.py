from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import record as log_audit
from ..auth import current_user, require_role
from ..config import settings
from ..db import get_db
from ..errors import ApiError
from ..models import AppUser, AuditLog, Setting, SlaPolicy, Store
from ..schemas import (
    AlertSettings,
    AssignmentSettings,
    AuditLogOut,
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


@router.post("/stores", response_model=StoreOut, status_code=201)
def create_store(body: StoreIn, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    data = body.model_dump()
    store = Store()
    _apply_shopify(store, data)
    for field, value in data.items():
        setattr(store, field, value)
    db.add(store)
    _commit(db, "store_exists", "A store with this name already exists")
    log_audit(db, actor, "store.create", target_type="store", target_id=store.id, summary=f"Created store “{store.name}”")
    db.commit()
    return store


@router.patch("/stores/{store_id}", response_model=StoreOut)
def update_store(store_id: int, body: StorePatch, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    store = db.get(Store, store_id)
    if store is None:
        raise ApiError(404, "not_found", "Store not found")
    data = body.model_dump(exclude_unset=True)
    fields = sorted(data.keys())  # field names only, never values (e.g. no shopify_token value)
    _apply_shopify(store, data)
    for field, value in data.items():
        if value is not None:
            setattr(store, field, value)
    log_audit(db, actor, "store.update", target_type="store", target_id=store.id, summary=f"Updated store “{store.name}”", fields=fields)
    _commit(db, "store_exists", "A store with this name already exists")
    return store


@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_role("admin", "manager"))])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(AppUser).order_by(AppUser.email)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
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
    log_audit(db, actor, "user.create", target_type="user", target_id=user.id, summary=f"Added {user.email} as {user.role}", role=user.role)
    db.commit()
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
    fields = sorted(data.keys())
    if "store_ids" in data:
        user.stores = _stores(db, data.pop("store_ids"))
    for field, value in data.items():
        setattr(user, field, value)
    log_audit(db, me, "user.update", target_type="user", target_id=user.id, summary=f"Updated {user.email}", fields=fields)
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


@router.put("/sla-policies", response_model=SlaPolicyOut)
def upsert_sla_policy(body: SlaPolicyIn, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
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
    scope = f"store {body.store_id}" if body.store_id else "default"
    log_audit(db, actor, "sla.update", target_type="sla_policy", target_id=body.store_id,
              summary=f"Updated SLA policy ({scope})",
              first_response_min=body.first_response_min, next_response_min=body.next_response_min,
              resolution_min=body.resolution_min)
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


@router.put("/settings/business-hours", response_model=BusinessHours)
def put_business_hours(body: BusinessHours, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(400, "bad_timezone", "Unknown timezone")
    log_audit(db, actor, "settings.business_hours", target_type="setting", target_id="business_hours",
              summary="Updated business hours", timezone=body.timezone)
    _put_setting(db, "business_hours", body.model_dump())
    return body


@router.get("/settings/alerts", response_model=AlertSettings, dependencies=[Depends(admin)])
def get_alerts(db: Session = Depends(get_db)):
    return _get_setting(db, "alerts", {"manager_emails": [], "google_chat_webhook": None})


DEFAULT_ASSIGNMENT = {"enabled": True, "strategy": "least_loaded"}


@router.get("/settings/assignment", response_model=AssignmentSettings, dependencies=[Depends(admin)])
def get_assignment(db: Session = Depends(get_db)):
    return _get_setting(db, "assignment", DEFAULT_ASSIGNMENT)


@router.put("/settings/assignment", response_model=AssignmentSettings)
def put_assignment(body: AssignmentSettings, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    log_audit(db, actor, "settings.assignment", target_type="setting", target_id="assignment",
              summary=f"Auto-assignment {'on' if body.enabled else 'off'} ({body.strategy})", **body.model_dump())
    _put_setting(db, "assignment", body.model_dump())
    return body


@router.put("/settings/alerts", response_model=AlertSettings)
def put_alerts(body: AlertSettings, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    log_audit(db, actor, "settings.alerts", target_type="setting", target_id="alerts",
              summary="Updated alert settings", manager_emails=len(body.manager_emails),
              google_chat_webhook_set=bool(body.google_chat_webhook))
    _put_setting(db, "alerts", body.model_dump())
    return body


# ---- Audit log (critical-change history) ----


@router.get("/audit", response_model=list[AuditLogOut], dependencies=[Depends(admin)])
def list_audit(db: Session = Depends(get_db), limit: int = 200, action: str | None = None):
    q = select(AuditLog, AppUser.email).join(AppUser, AuditLog.actor_user_id == AppUser.id, isouter=True)
    if action:
        q = q.where(AuditLog.action == action)
    rows = db.execute(q.order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 500))).all()
    return [
        AuditLogOut(
            id=log.id, actor_user_id=log.actor_user_id, actor_email=email, action=log.action,
            target_type=log.target_type, target_id=log.target_id, summary=log.summary,
            data=log.data or {}, created_at=log.created_at,
        )
        for log, email in rows
    ]


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
        embedding_model=v.get("embedding_model") or None,
        embedding_base_url=v.get("embedding_base_url") or None,
        embedding_configured=bool(v.get("embedding_api_key_enc")),
    )


@router.put("/settings/llm", response_model=LlmSettingsOut)
def put_llm(body: LlmSettingsIn, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
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
    if body.embedding_model is not None:  # "" turns semantic retrieval off
        v["embedding_model"] = body.embedding_model.strip() or None
    if body.embedding_base_url is not None:
        v["embedding_base_url"] = body.embedding_base_url.strip() or None
    if body.embedding_api_key is not None:  # None = keep; "" = clear
        key = body.embedding_api_key.strip()
        v["embedding_api_key_enc"] = encrypt(key) if key else None
    # Never log key values — only whether they changed.
    log_audit(db, actor, "llm.settings", target_type="setting", target_id="llm",
              summary="Updated AI provider settings", provider=v.get("provider"), model=v.get("model"),
              embedding_model=v.get("embedding_model"), api_key_changed=body.api_key is not None,
              embedding_key_changed=body.embedding_api_key is not None)
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
