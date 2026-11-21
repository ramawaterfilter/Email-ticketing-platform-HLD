"""Knowledge base: documents the LLM draws on, tagged per store or shared (HLD 4.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..errors import ApiError
from ..llm import extract_text
from ..models import AppUser, KbDocument, Store
from ..schemas import KbDocumentFromUrl, KbDocumentIn, KbDocumentOut, KbDocumentPatch
from ..webfetch import fetch_url_text

router = APIRouter(tags=["kb"], dependencies=[Depends(require_role("admin"))])

MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB


def _out(d: KbDocument) -> KbDocumentOut:
    return KbDocumentOut(
        id=d.id,
        store_id=d.store_id,
        title=d.title,
        filename=d.filename,
        source_url=d.source_url,
        is_active=d.is_active,
        char_count=len(d.body_text or ""),
        created_at=d.created_at,
    )


def _check_store(db: Session, store_id: int | None) -> None:
    if store_id is not None and db.get(Store, store_id) is None:
        raise ApiError(400, "unknown_store", "That store does not exist")


@router.get("/kb", response_model=list[KbDocumentOut])
def list_docs(db: Session = Depends(get_db)):
    docs = db.scalars(select(KbDocument).order_by(KbDocument.store_id.nulls_first(), KbDocument.title)).all()
    return [_out(d) for d in docs]


@router.post("/kb", response_model=KbDocumentOut, status_code=201)
def create_doc(body: KbDocumentIn, db: Session = Depends(get_db), user: AppUser = Depends(require_role("admin"))):
    _check_store(db, body.store_id)
    if not body.body_text.strip():
        raise ApiError(400, "empty", "Provide document text, or use the file upload")
    doc = KbDocument(
        store_id=body.store_id, title=body.title, body_text=body.body_text, created_by=user.id
    )
    db.add(doc)
    db.commit()
    return _out(doc)


@router.post("/kb/upload", response_model=KbDocumentOut, status_code=201)
async def upload_doc(
    file: UploadFile = File(...),
    title: str = Form(...),
    store_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_role("admin")),
):
    _check_store(db, store_id)
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise ApiError(400, "too_large", "File is larger than 10 MB")
    text = extract_text(file.filename or "", file.content_type or "", data)
    if not text.strip():
        raise ApiError(400, "no_text", "No readable text could be extracted from this file")
    doc = KbDocument(
        store_id=store_id, title=title, filename=file.filename, body_text=text, created_by=user.id
    )
    db.add(doc)
    db.commit()
    return _out(doc)


@router.post("/kb/from-url", response_model=KbDocumentOut, status_code=201)
def import_from_url(body: KbDocumentFromUrl, db: Session = Depends(get_db), user: AppUser = Depends(require_role("admin"))):
    _check_store(db, body.store_id)
    title, text = fetch_url_text(body.url)
    doc = KbDocument(
        store_id=body.store_id,
        title=(body.title or title or body.url)[:200],
        source_url=body.url.strip(),
        body_text=text,
        created_by=user.id,
    )
    db.add(doc)
    db.commit()
    return _out(doc)


@router.post("/kb/{doc_id}/refresh", response_model=KbDocumentOut)
def refresh_from_url(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(KbDocument, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    if not doc.source_url:
        raise ApiError(400, "not_from_url", "This document was not imported from a URL")
    _, text = fetch_url_text(doc.source_url)
    doc.body_text = text
    db.commit()
    return _out(doc)


@router.patch("/kb/{doc_id}", response_model=KbDocumentOut)
def update_doc(doc_id: int, body: KbDocumentPatch, db: Session = Depends(get_db)):
    doc = db.get(KbDocument, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    data = body.model_dump(exclude_unset=True)
    if "store_id" in data:
        _check_store(db, data["store_id"])
    for field, value in data.items():
        setattr(doc, field, value)
    db.commit()
    return _out(doc)


@router.delete("/kb/{doc_id}", status_code=204)
def delete_doc(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(KbDocument, doc_id)
    if doc is not None:
        db.delete(doc)
        db.commit()
