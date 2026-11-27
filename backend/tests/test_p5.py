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


def test_reindex_inline_embeds_and_keeps_vectors_when_unchanged(monkeypatch):
    _use_fake_embedder(monkeypatch)
    with SessionLocal() as db:
        store = make_store(db)
        doc = KbDocument(store_id=store.id, title="Returns", body_text="Refund and return within 30 days.")
        db.add(doc)
        db.flush()
        reindex_document(db, doc, embed_now=True)  # zero-downtime: embed before commit
        db.commit()
        assert embed_pending_chunks(db) == 0  # already embedded inline, nothing pending
        ids_before = sorted(c.id for c in db.query(KbChunk).filter(KbChunk.document_id == doc.id))
        assert ids_before and all(
            c.embedding is not None for c in db.query(KbChunk).filter(KbChunk.document_id == doc.id)
        )
        # Unchanged refresh must preserve the exact rows (no delete/re-embed gap).
        reindex_document(db, doc, embed_now=True)
        db.commit()
        ids_after = sorted(c.id for c in db.query(KbChunk).filter(KbChunk.document_id == doc.id))
        assert ids_after == ids_before


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


def test_answer_kb_question_grounds_in_retrieved_docs(monkeypatch):
    import app.llm as llm

    _use_fake_embedder(monkeypatch)
    # Don't call a real LLM: echo the passages so we can assert what was retrieved.
    captured = {}

    def fake_chat(cfg, system_text, user_text):
        captured.update(system=system_text, question=user_text)
        return f"ANSWER using: {system_text[-200:]}"

    monkeypatch.setattr(llm, "_chat_text", fake_chat)
    monkeypatch.setattr(llm, "get_config", lambda _db: llm.LlmConfig("openai", "m", None, "k"))
    with SessionLocal() as db:
        store = make_store(db)
        refunds = KbDocument(store_id=store.id, title="Refunds", body_text="Customers can get a refund or return items.")
        shipping = KbDocument(store_id=None, title="Shipping", body_text="Orders ship in two days with tracking.")
        db.add_all([refunds, shipping])
        db.flush()
        for d in (refunds, shipping):
            reindex_document(db, d)
        db.commit()
        embed_pending_chunks(db)

        answer, sources = llm.answer_kb_question(db, "how do I get a refund?")
        assert answer.startswith("ANSWER using:")
        assert any(s["title"] == "Refunds" for s in sources)  # cross-store search found the nearest doc
        assert captured["question"] == "how do I get a refund?"
        instructions, passages = captured["system"].split("POLICY AND REFERENCE PASSAGES:\n", 1)
        assert "plain-text bullet points starting with •, normally 1–5 bullets" in instructions
        assert "Needs clarification:" in instructions
        assert "question is ambiguous" in instructions
        assert "required information is missing" in instructions
        assert "passages conflict" in instructions
        assert "Ask one focused clarification question" in instructions
        assert "Never invent policies, prices, dates, timeframes, or promises" in instructions
        assert "Customers can get a refund or return items." in passages


def test_build_prompt_falls_back_to_whole_kb_when_off(monkeypatch):
    # No embedder configured -> get_embedder returns None -> whole-KB mode.
    monkeypatch.setattr(kb_index, "get_embedder", lambda _db: None)
    with SessionLocal() as db:
        store, _mailbox, ticket = seed_ticket(db)
        db.add(KbDocument(store_id=store.id, title="Returns", body_text="30 day returns."))
        db.commit()
        _system, _user, meta = build_prompt(db, ticket, store)
        assert meta["retrieval"] == "whole"
