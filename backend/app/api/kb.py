"""Knowledge base: documents the LLM draws on, tagged per store or shared (HLD 4.5).

Each document is split into chunks and embedded for semantic retrieval; changing a
document re-chunks it (embeddings are filled in by the worker).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record as log_audit
from ..auth import current_user, require_role
from ..db import get_db
from ..errors import ApiError
from ..kb_index import reindex_all, reindex_document
from ..llm import extract_text
from ..models import AppUser, KbChunk, KbDocument, Store
from ..schemas import KbAskIn, KbAskOut, KbDocumentFromUrl, KbDocumentIn, KbDocumentOut, KbDocumentPatch
from ..webfetch import fetch_url_text

# Reads are open to any signed-in user; writes are guarded per-route with `admin`.
router = APIRouter(tags=["kb"], dependencies=[Depends(current_user)])
admin = require_role("admin")

# The policy assistant is read-only, so it lives outside the admin guards too.
ask_router = APIRouter(tags=["kb"])


@ask_router.post("/kb/ask", response_model=KbAskOut)
def ask(body: KbAskIn, db: Session = Depends(get_db), _: AppUser = Depends(current_user)):
    from ..llm import answer_kb_question

    try:
        answer, sources = answer_kb_question(db, body.question)
    except RuntimeError:
        raise ApiError(400, "not_configured", "The AI assistant is not configured yet.")
    except Exception as exc:  # noqa: BLE001 - surface provider errors to the user
        raise ApiError(502, "assistant_failed", f"The assistant could not answer: {str(exc)[:200]}")
    return KbAskOut(answer=answer, sources=sources)

MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB


def _out(d: KbDocument, chunks: int = 0, embedded: int = 0) -> KbDocumentOut:
    return KbDocumentOut(
        id=d.id,
        store_id=d.store_id,
        title=d.title,
        filename=d.filename,
        source_url=d.source_url,
        is_active=d.is_active,
        char_count=len(d.body_text or ""),
        chunks=chunks,
        embedded=embedded,
        created_at=d.created_at,
    )


def _count_for(db: Session, doc_id: int) -> tuple[int, int]:
    row = db.execute(
        select(func.count(), func.count(KbChunk.embedding)).where(KbChunk.document_id == doc_id)
    ).one()
    return int(row[0]), int(row[1])


def _check_store(db: Session, store_id: int | None) -> None:
    if store_id is not None and db.get(Store, store_id) is None:
        raise ApiError(400, "unknown_store", "That store does not exist")


@router.get("/kb", response_model=list[KbDocumentOut])
def list_docs(db: Session = Depends(get_db)):
    docs = db.scalars(select(KbDocument).order_by(KbDocument.store_id.nulls_first(), KbDocument.title)).all()
    counts = {
        did: (int(total), int(emb))
        for did, total, emb in db.execute(
            select(KbChunk.document_id, func.count(), func.count(KbChunk.embedding)).group_by(KbChunk.document_id)
        ).all()
    }
    return [_out(d, *counts.get(d.id, (0, 0))) for d in docs]


def _create(db: Session, doc: KbDocument, actor: AppUser) -> KbDocumentOut:
    db.add(doc)
    db.flush()
    # Embed on upload so the document is searchable immediately, without waiting for the worker.
    # If the embeddings provider is unavailable, this falls back to pending (the worker finishes it).
    reindex_document(db, doc, embed_now=True)
    log_audit(db, actor, "kb.create", target_type="kb_document", target_id=doc.id,
              summary=f"Added KB document “{doc.title}”")
    db.commit()
    return _out(doc, *_count_for(db, doc.id))


@router.post("/kb", response_model=KbDocumentOut, status_code=201)
def create_doc(body: KbDocumentIn, db: Session = Depends(get_db), user: AppUser = Depends(require_role("admin"))):
    _check_store(db, body.store_id)
    if not body.body_text.strip():
        raise ApiError(400, "empty", "Provide document text, or use the file upload")
    return _create(db, KbDocument(store_id=body.store_id, title=body.title, body_text=body.body_text, created_by=user.id), user)


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
    return _create(
        db, KbDocument(store_id=store_id, title=title, filename=file.filename, body_text=text, created_by=user.id), user
    )


@router.post("/kb/from-url", response_model=KbDocumentOut, status_code=201)
def import_from_url(body: KbDocumentFromUrl, db: Session = Depends(get_db), user: AppUser = Depends(require_role("admin"))):
    _check_store(db, body.store_id)
    title, text = fetch_url_text(body.url)
    return _create(
        db,
        KbDocument(
            store_id=body.store_id,
            title=(body.title or title or body.url)[:200],
            source_url=body.url.strip(),
            body_text=text,
            created_by=user.id,
        ),
        user,
    )


@router.post("/kb/reindex")
def reindex(db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    """Re-chunk every document (the worker then embeds the new chunks). Use after
    turning embeddings on, or to rebuild the index."""
    chunks = reindex_all(db)
    log_audit(db, actor, "kb.reindex", target_type="kb_document", summary=f"Reindexed the knowledge base ({chunks} chunks)")
    db.commit()
    return {"documents": db.scalar(select(func.count()).select_from(KbDocument)), "chunks": chunks}


@router.post("/kb/{doc_id}/refresh", response_model=KbDocumentOut)
def refresh_from_url(doc_id: int, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    doc = db.get(KbDocument, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    if not doc.source_url:
        raise ApiError(400, "not_from_url", "This document was not imported from a URL")
    _, text = fetch_url_text(doc.source_url)
    doc.body_text = text
    reindex_document(db, doc, embed_now=True)  # embed before swapping, so old vectors serve until new land
    log_audit(db, actor, "kb.refresh", target_type="kb_document", target_id=doc.id,
              summary=f"Refreshed KB document “{doc.title}” from its URL")
    db.commit()
    return _out(doc, *_count_for(db, doc.id))


@router.patch("/kb/{doc_id}", response_model=KbDocumentOut)
def update_doc(doc_id: int, body: KbDocumentPatch, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    doc = db.get(KbDocument, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    data = body.model_dump(exclude_unset=True)
    if "store_id" in data:
        _check_store(db, data["store_id"])
    for field, value in data.items():
        setattr(doc, field, value)
    db.flush()
    # Keep chunk metadata in sync without re-embedding (body is unchanged on a patch).
    for chunk in db.scalars(select(KbChunk).where(KbChunk.document_id == doc.id)):
        chunk.store_id = doc.store_id
        chunk.title = doc.title
    log_audit(db, actor, "kb.update", target_type="kb_document", target_id=doc.id,
              summary=f"Updated KB document “{doc.title}”", fields=sorted(data.keys()))
    db.commit()
    return _out(doc, *_count_for(db, doc.id))


@router.delete("/kb/{doc_id}", status_code=204)
def delete_doc(doc_id: int, db: Session = Depends(get_db), actor: AppUser = Depends(admin)):
    doc = db.get(KbDocument, doc_id)
    if doc is not None:
        log_audit(db, actor, "kb.delete", target_type="kb_document", target_id=doc.id,
                  summary=f"Deleted KB document “{doc.title}”")
        db.delete(doc)
        db.commit()
