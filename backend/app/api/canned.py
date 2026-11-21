"""Canned responses: saved reply snippets executives insert into the editor (HLD Phase 3)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require_role
from ..db import get_db
from ..errors import ApiError
from ..models import AppUser, CannedResponse, Store
from ..schemas import CannedResponseIn, CannedResponseOut, CannedResponsePatch

router = APIRouter(tags=["canned"])
admin = require_role("admin")


def _check_store(db: Session, store_id: int | None) -> None:
    if store_id is not None and db.get(Store, store_id) is None:
        raise ApiError(400, "unknown_store", "That store does not exist")


# Any signed-in user may read them (executives insert them while replying).
@router.get("/canned", response_model=list[CannedResponseOut])
def list_canned(db: Session = Depends(get_db), _: AppUser = Depends(current_user)):
    return db.scalars(
        select(CannedResponse).order_by(CannedResponse.store_id.nulls_first(), CannedResponse.title)
    ).all()


@router.post("/canned", response_model=CannedResponseOut, status_code=201, dependencies=[Depends(admin)])
def create_canned(body: CannedResponseIn, db: Session = Depends(get_db), user: AppUser = Depends(admin)):
    _check_store(db, body.store_id)
    c = CannedResponse(
        store_id=body.store_id, title=body.title, body_text=body.body_text, created_by=user.id
    )
    db.add(c)
    db.commit()
    return c


@router.patch("/canned/{canned_id}", response_model=CannedResponseOut, dependencies=[Depends(admin)])
def update_canned(canned_id: int, body: CannedResponsePatch, db: Session = Depends(get_db)):
    c = db.get(CannedResponse, canned_id)
    if c is None:
        raise ApiError(404, "not_found", "Canned response not found")
    data = body.model_dump(exclude_unset=True)
    if "store_id" in data:
        _check_store(db, data["store_id"])
    for field, value in data.items():
        setattr(c, field, value)
    db.commit()
    return c


@router.delete("/canned/{canned_id}", status_code=204, dependencies=[Depends(admin)])
def delete_canned(canned_id: int, db: Session = Depends(get_db)):
    c = db.get(CannedResponse, canned_id)
    if c is not None:
        db.delete(c)
        db.commit()
