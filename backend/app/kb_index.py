"""Semantic (vector) retrieval for the knowledge base.

Documents are split into chunks; each chunk is embedded with an OpenAI-compatible
embeddings model and stored in pgvector. At draft time we embed the incoming email
and fetch the nearest chunks, so only the relevant passages enter the prompt — which
scales to a large document library instead of dumping every document.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import delete, select, true
from sqlalchemy.orm import Session

from .models import KbChunk, KbDocument, Setting

log = logging.getLogger("kb_index")

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
EMBED_BATCH = 64
RETRIEVE_K = 10
RETRIEVE_CHARS = 12_000  # budget for retrieved passages in the prompt


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    step = max(size - overlap, 1)
    while start < len(text):
        chunk = text[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
        start += step
    return chunks


# ---- embeddings config (reuses the chat LLM's key + base URL) ----


@dataclass
class EmbedConfig:
    base_url: str | None
    api_key: str
    model: str


def get_embed_config(db: Session) -> EmbedConfig | None:
    """Embedding credentials. Embeddings can use their own endpoint/key (e.g. OpenAI)
    while chat uses another provider; if none is set, they reuse the chat provider's."""
    row = db.get(Setting, "llm")
    v = row.value if row and row.value else {}
    model = (v.get("embedding_model") or "").strip()
    if not model:
        return None
    base_url = (v.get("embedding_base_url") or "").strip() or None
    if v.get("embedding_api_key_enc"):
        from .crypto import decrypt

        return EmbedConfig(base_url, decrypt(v["embedding_api_key_enc"]), model)
    from .llm import get_config  # fall back to the chat provider's key + endpoint

    cfg = get_config(db)
    if cfg is None:
        return None
    return EmbedConfig(base_url or cfg.base_url, cfg.api_key, model)


def embeddings_enabled(db: Session) -> bool:
    return get_embed_config(db) is not None


class Embedder:
    def __init__(self, cfg: EmbedConfig):
        from openai import OpenAI

        self._client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None)
        self.model = cfg.model
        # NVIDIA NIM embedding models require an input_type (query vs passage); OpenAI etc. do not.
        self._nvidia = "nvidia" in (cfg.base_url or "").lower() or cfg.model.lower().startswith("nvidia/")

    def embed(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        extra = {"extra_body": {"input_type": input_type, "truncate": "END"}} if self._nvidia else {}
        resp = self._client.embeddings.create(model=self.model, input=texts, **extra)
        return [d.embedding for d in resp.data]


def get_embedder(db: Session) -> Embedder | None:
    cfg = get_embed_config(db)
    return Embedder(cfg) if cfg else None


# ---- indexing ----


def reindex_document(db: Session, doc: KbDocument, embed_now: bool = False) -> int:
    """Re-chunk a document. Caller commits.

    Zero-downtime: if the text is unchanged and already embedded, the existing
    chunks are kept (only scope/title re-synced) so a no-op refresh never drops
    the embeddings. When the text changed and embed_now is set, the new chunks
    are embedded *before* the old ones are deleted in the same transaction, so
    retrieval keeps serving the old vectors until the new ones are committed.
    """
    new_bodies = chunk_text(doc.body_text or "")
    existing = db.scalars(
        select(KbChunk).where(KbChunk.document_id == doc.id).order_by(KbChunk.ordinal)
    ).all()
    if existing and [c.body for c in existing] == new_bodies and all(c.embedding is not None for c in existing):
        for c in existing:  # content identical: keep vectors, just sync metadata
            c.store_id, c.title = doc.store_id, doc.title
        return len(existing)

    vectors: list[list[float]] | None = None
    model: str | None = None
    if embed_now and new_bodies:
        embedder = get_embedder(db)
        if embedder is not None:
            try:
                vectors = [v for i in range(0, len(new_bodies), EMBED_BATCH)
                           for v in embedder.embed(new_bodies[i : i + EMBED_BATCH])]
                model = embedder.model
            except Exception:  # noqa: BLE001 - provider down; fall back to worker, accept a brief gap
                log.exception("inline embedding failed; leaving chunks for the worker")
                vectors = None

    db.execute(delete(KbChunk).where(KbChunk.document_id == doc.id))
    for i, body in enumerate(new_bodies):
        db.add(KbChunk(
            document_id=doc.id, store_id=doc.store_id, title=doc.title, ordinal=i, body=body,
            embedding=(vectors[i] if vectors else None), model=(model if vectors else None),
        ))
    return len(new_bodies)


def reindex_all(db: Session) -> int:
    docs = db.scalars(select(KbDocument)).all()
    total = 0
    for doc in docs:
        total += reindex_document(db, doc)
    db.commit()
    return total


def embed_pending_chunks(db: Session, limit: int = 200) -> int:
    """Embed chunks that have no vector yet. Returns how many were embedded this call."""
    embedder = get_embedder(db)
    if embedder is None:
        return 0
    pending = db.scalars(
        select(KbChunk).where(KbChunk.embedding.is_(None)).order_by(KbChunk.id).limit(limit)
    ).all()
    if not pending:
        return 0
    done = 0
    for i in range(0, len(pending), EMBED_BATCH):
        batch = pending[i : i + EMBED_BATCH]
        try:
            vectors = embedder.embed([c.body for c in batch])
            for chunk, vector in zip(batch, vectors):
                chunk.embedding = vector
                chunk.model = embedder.model
            db.commit()
        except Exception:  # noqa: BLE001 - provider down, or chunks re-indexed mid-flight; retry next tick
            log.exception("embedding batch failed")
            db.rollback()
            break
        done += len(batch)
    return done


# ---- retrieval ----


def retrieve(
    db: Session, store_id: int | None, query: str, embedder: Embedder, k: int = RETRIEVE_K, budget: int = RETRIEVE_CHARS
) -> tuple[str, list[dict]]:
    """Return (passages_text, documents_used) for the chunks nearest to the query.

    store_id scopes to that store's docs plus shared ones; pass None to search
    the whole knowledge base (every store + shared), e.g. for the policy helper.
    """
    query_vec = embedder.embed([query], input_type="query")[0]
    scope_filter = (
        true() if store_id is None else ((KbChunk.store_id == store_id) | (KbChunk.store_id.is_(None)))
    )
    rows = db.scalars(
        select(KbChunk)
        .where(scope_filter, KbChunk.embedding.is_not(None))
        .order_by(KbChunk.embedding.cosine_distance(query_vec))
        .limit(k)
    ).all()
    parts: list[str] = []
    used: dict[int, str] = {}
    left = budget
    for chunk in rows:
        if left <= 0:
            break
        body = chunk.body[:left]
        scope = "shared" if chunk.store_id is None else "store"
        parts.append(f"--- {chunk.title} ({scope}) ---\n{body}")
        used.setdefault(chunk.document_id, chunk.title)
        left -= len(body)
    return "\n\n".join(parts), [{"id": did, "title": title} for did, title in used.items()]
