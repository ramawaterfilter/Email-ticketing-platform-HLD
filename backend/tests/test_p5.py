"""Semantic (vector) KB retrieval: chunking, embedding, nearest-chunk retrieval, fallback."""

import app.kb_index as kb_index
from app.db import SessionLocal
from app.kb_index import chunk_text, embed_pending_chunks, reindex_document, retrieve
from app.llm import build_prompt
from app.models import KbChunk, KbDocument, Store

from test_m2 import make_store
from test_p2 import seed_ticket

VOCAB = ["refund", "return", "shipping", "delivery", "warranty", "replacement", "install", "filter"]


class FakeEmbedder:
    """Deterministic keyword-count embeddings so nearest-neighbour order is predictable."""

    model = "fake-embed"

    def embed(self, texts, input_type="passage"):
        out = []
        for t in texts:
            low = t.lower()
            out.append([0.01 + float(low.count(w)) for w in VOCAB])
        return out


def _use_fake_embedder(monkeypatch):
    monkeypatch.setattr(kb_index, "get_embedder", lambda _db: FakeEmbedder())


def test_chunk_text_splits_long_with_overlap():
    assert chunk_text("") == []
    assert chunk_text("short doc") == ["short doc"]
    big = "sentence. " * 400  # ~4000 chars
    chunks = chunk_text(big, size=1000, overlap=150)
    assert len(chunks) >= 4
    assert all(len(c) <= 1000 for c in chunks)


def test_reindex_and_embed_pending(monkeypatch):
    _use_fake_embedder(monkeypatch)
    with SessionLocal() as db:
        store = make_store(db)
        doc = KbDocument(store_id=store.id, title="Returns", body_text="We offer a refund and return within 30 days.")
        db.add(doc)
        db.flush()
        n = reindex_document(db, doc)
        db.commit()
        assert n >= 1
        assert embed_pending_chunks(db) == n  # the worker fills them in
        embedded = db.query(KbChunk).filter(KbChunk.embedding.is_not(None)).count()
        assert embedded == n
        assert embed_pending_chunks(db) == 0  # nothing left to do


def test_retrieve_returns_the_most_relevant_document(monkeypatch):
    _use_fake_embedder(monkeypatch)
    with SessionLocal() as db:
        store = make_store(db)
        refunds = KbDocument(store_id=store.id, title="Refunds", body_text="Customers can get a refund or return items.")
        install = KbDocument(store_id=store.id, title="Install", body_text="How to install the water filter cartridge.")
        db.add_all([refunds, install])
        db.flush()
        for d in (refunds, install):
            reindex_document(db, d)
        db.commit()
        embed_pending_chunks(db)

        passages, used = retrieve(db, store.id, "I want a refund for my return", FakeEmbedder())
        assert used, "expected at least one passage"
        assert used[0]["title"] == "Refunds"  # nearest to the refund query


def test_build_prompt_uses_semantic_when_embeddings_on(monkeypatch):
    _use_fake_embedder(monkeypatch)
    with SessionLocal() as db:
        store, _mailbox, ticket = seed_ticket(db)
        doc = KbDocument(store_id=store.id, title="Order policy", body_text="Orders ship within two days. Tracking is emailed.")
        db.add(doc)
        db.flush()
        reindex_document(db, doc)
        db.commit()
        embed_pending_chunks(db)

        system, _user, meta = build_prompt(db, ticket, store)
        assert meta["retrieval"] == "semantic"
        assert any(d["title"] == "Order policy" for d in meta["documents"])


def test_build_prompt_falls_back_to_whole_kb_when_off(monkeypatch):
    # No embedder configured -> get_embedder returns None -> whole-KB mode.
    monkeypatch.setattr(kb_index, "get_embedder", lambda _db: None)
    with SessionLocal() as db:
        store, _mailbox, ticket = seed_ticket(db)
        db.add(KbDocument(store_id=store.id, title="Returns", body_text="30 day returns."))
        db.commit()
        _system, _user, meta = build_prompt(db, ticket, store)
        assert meta["retrieval"] == "whole"
