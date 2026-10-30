import hashlib
import math
import re

import pytest
from fastapi.testclient import TestClient

from app import config, vectors
from app.complaint import retrieval
from app.vault import index, store
from app.vault.schemas import EvidenceIn

DIM = 128


def fake_embed(text: str) -> list[float]:
    """Hashed bag of words: same words -> close vectors. No model download in tests."""
    v = [0.0] * DIM
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(retrieval, "_embed_failed", False)
    calls = {"docs": 0}

    def embed_docs(texts):
        calls["docs"] += 1
        return [fake_embed(t) for t in texts]

    monkeypatch.setattr(vectors, "embed_docs", embed_docs)
    monkeypatch.setattr(vectors, "embed_query", fake_embed)
    return calls


def seal(conversation="unknown", texts=(), flagged=()) -> str:
    return store.save_capture(EvidenceIn(
        platform="WhatsApp",
        conversation=conversation,
        messages=[{"ref": str(i), "sender": "me" if t.startswith("me:") else "him", "text": t.removeprefix("me:"),
                   "time": f"21:{i:02d}", "flagged": i in flagged} for i, t in enumerate(texts)],
    ))["capture_id"]


# ---------- legal sections in the vector DB ----------

def test_legal_sections_are_indexed_once_and_used_for_dense_ranking(setup):
    cands = retrieval.retrieve("threatens to share private photos unless paid", None)
    assert any("dense" in c.ranks for c in cands)
    assert vectors.collection(retrieval.LAW_COLLECTION).count() == len(retrieval.corpus())
    retrieval.retrieve("someone follows me home", None)
    assert setup["docs"] == 1  # second query reuses the stored vectors


def test_legal_collection_rebuilds_when_it_no_longer_matches_the_corpus(setup):
    retrieval.retrieve("stalking", None)
    vectors.collection(retrieval.LAW_COLLECTION).upsert(ids=["OLD-1"], embeddings=[fake_embed("x")], metadatas=[{"fp": "x"}])
    retrieval.retrieve("stalking", None)
    col = vectors.collection(retrieval.LAW_COLLECTION)
    assert col.count() == len(retrieval.corpus()) and "OLD-1" not in col.get()["ids"]
    assert setup["docs"] == 2


# ---------- evidence search ----------

def test_search_finds_by_meaning_and_returns_the_sealed_text():
    cid = seal(texts=["hi", "REVA gate hatra kaaythini 5 ge", "send 10k or else"], flagged=(1, 2))
    assert index.index_capture(cid) == 3
    hits = index.search("he said he will wait for me near college", k=2)
    assert hits[0]["text"] == "REVA gate hatra kaaythini 5 ge"  # matched through the slang gloss
    assert hits[0]["verified"] and hits[0]["evidence_id"] == f"E{hits[0]['record_id']}"


def test_index_holds_masked_text_only():
    cid = seal(texts=["call me on 9845012345", "pay rahul.k07@okaxis"])
    index.index_capture(cid)
    docs = " ".join(vectors.collection(index.COLLECTION).get()["documents"])
    assert "9845012345" not in docs and "okaxis" not in docs
    assert "a phone number" in docs  # [PHONE_1] embedded as words
    assert index.search("phone number", k=1)[0]["text"] == "call me on 9845012345"  # real text back from the Vault


def test_filters_by_conversation_and_flag():
    index.index_capture(seal("rahul", ["send money now", "ok"], flagged=(0,)))
    index.index_capture(seal("vikram", ["send money today"]))
    assert {h["conversation"] for h in index.search("money", conversation="vikram")} == {"vikram"}
    assert [h["text"] for h in index.search("money", flagged_only=True)] == ["send money now"]


def test_search_catches_up_on_unindexed_captures_and_forgets_cleared_ones():
    seal(texts=["I know your house"])  # never indexed (e.g. saved before the index existed)
    assert index.search("house")[0]["text"] == "I know your house"
    store.clear()
    assert index.search("house") == []
    seal(texts=["new chat about coffee"])
    hits = index.search("house")  # the new record reuses row id 1
    assert [h["text"] for h in hits] == ["new chat about coffee"] and hits[0]["similarity"] < 0.1
    assert vectors.collection(index.COLLECTION).count() == 1  # the old message's vector is gone


def test_tampered_record_is_reported_unverified():
    cid = seal(texts=["first", "pay 10k or I post the photos", "last"])
    index.index_capture(cid)
    rec = store.demo_tamper(None, rehash=False)["record_id"]
    hit = next(h for h in index.search("photos", k=3) if h["record_id"] == rec)
    assert hit["verified"] is False


def test_api_indexes_on_save_and_searches():
    from app.main import app

    client = TestClient(app)
    body = {"platform": "Instagram", "conversation": "rahul_k_07",
            "messages": [{"ref": "1", "sender": "rahul", "text": "your papa gets the photos", "flagged": True}]}
    assert client.post("/evidence", json=body).status_code == 200
    assert vectors.collection(index.COLLECTION).count() == 1  # background task ran
    res = client.get("/evidence/search", params={"q": "threat to tell my parents"}).json()
    assert res["results"][0]["text"] == "your papa gets the photos"
    assert client.get("/evidence/search", params={"q": "  "}).status_code == 400
    client.delete("/evidence")
    assert vectors.collection(index.COLLECTION).count() == 0
