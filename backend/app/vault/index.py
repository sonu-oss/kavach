"""Search the Evidence Vault by meaning, through the `evidence` vector collection.

"every time he mentioned my college" finds "kaaythini outside REVA gate" though
the word never appears. SQLite stays the sealed source of truth: the index only
holds a PII-masked copy of each message for embedding, and every hit is read
back from the hash-chained record, so a result always shows what is sealed.
"""

import logging
import re

from app import vectors
from app.auth import context
from app.shield.pii import Masker
from app.vault import store

log = logging.getLogger("kavach.vault")

COLLECTION = "evidence"


def _collection_name() -> str:
    """One collection per account, so search (and its clean-up of stale vectors) never crosses accounts."""
    user = context.current()
    return f"{COLLECTION}_{user}" if user else COLLECTION

# The embedding model only knows English, so romanised Hindi / Kannada slang (the same glossary the
# Smart Shield prompt uses) gets its English meaning appended before embedding.
_GLOSS = {
    r"kaay?thini|kaaytini": "I will wait for you",
    r"nodide": "I saw you",
    r"haakidde": "you were wearing",
    r"hinde barthini": "I will follow you",
    r"ninna mane gottu": "I know your house",
    r"mane|ghar": "house home",
    r"hatra|paas": "near",
    r"haakthini|daal dunga": "I will post it",
    r"kalisthini|bhej dunga": "I will send it",
    r"viral kar dunga": "I will make it go viral",
    r"kolthini|jaan se maar dunga|maar dunga": "I will kill you",
    r"hodithini": "I will beat you",
    r"nodkothini|dekh lunga|bataunga|batata hoon": "I will teach you a lesson",
    r"bidalla": "I will not leave you",
    r"illandre|warna": "or else",
    r"bhej de|bhej do|kalsu": "send",
    r"paise|paisa|upi|gpay|phonepe|paytm|\d+k": "money payment",
    r"amma|appa|papa|mummy": "parents family",
}
_GLOSS_RE = [(re.compile(rf"\b(?:{p})\b", re.IGNORECASE), m) for p, m in _GLOSS.items()]


_TOKEN_WORDS = {"PHONE": "a phone number", "EMAIL": "an email address", "UPI": "a UPI payment id",
                "AADHAAR": "an Aadhaar number", "ADDRESS": "a home address", "PINCODE": "a pin code"}
_TOKEN_RE = re.compile(r"\[(" + "|".join(_TOKEN_WORDS) + r")_\d+\]")


def _with_gloss(text: str) -> str:
    """Masked text in words the model knows: [PHONE_1] -> "a phone number", slang -> its meaning."""
    text = _TOKEN_RE.sub(lambda m: _TOKEN_WORDS[m.group(1)], text)
    meanings = [m for p, m in _GLOSS_RE if p.search(text)]
    return f"{text} (meaning: {'; '.join(meanings)})" if meanings else text


def _messages(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["kind"] == "message" and (r["message_text"] or "").strip()]


def _add(rows: list[dict]) -> int:
    rows = _messages(rows)
    if not rows:
        return 0
    masker = Masker()  # one per batch, so a number repeated across messages keeps one token
    docs = [_with_gloss(masker.mask(r["message_text"])) for r in rows]
    vectors.collection(_collection_name()).upsert(
        ids=[r["hash"] for r in rows],  # the seal hash: unique, and row ids are reused after a clear
        documents=docs,
        embeddings=vectors.embed_docs(docs),
        metadatas=[
            {
                "capture_id": r["capture_id"],
                "conversation": r["conversation"],
                "platform": r["platform"],
                "sender": r["sender"] or "",
                "flagged": bool(r["flagged"]),
            }
            for r in rows
        ],
    )
    return len(rows)


def index_capture(capture_id: str) -> int:
    """Embed the messages of one sealed capture. Best effort: the Vault never depends on it."""
    try:
        return _add([r for r in store.all_rows() if r["capture_id"] == capture_id])
    except Exception as exc:
        log.warning("evidence not indexed (search will catch up later): %s", exc)
        return 0


def _sync(rows: list[dict]) -> None:
    """Index any sealed message the vector DB is missing (a vault from before the index, a failed add)."""
    col = vectors.collection(_collection_name())
    have = set(col.get(include=[])["ids"])
    sealed = {r["hash"] for r in _messages(rows)}
    stale = have - sealed
    if stale:
        col.delete(ids=list(stale))
    missing = [r for r in rows if r["hash"] not in have]
    if _messages(missing):
        _add(missing)


def search(query: str, k: int = 5, conversation: str | None = None, flagged_only: bool = False) -> list[dict]:
    """The k sealed messages closest in meaning to `query`, best first."""
    rows = store.all_rows()
    if not _messages(rows):
        return []
    _sync(rows)
    by_hash = {r["hash"]: r for r in rows}

    where = [{"conversation": conversation}] if conversation else []
    if flagged_only:
        where.append({"flagged": True})
    res = vectors.collection(_collection_name()).query(
        query_embeddings=[vectors.embed_query(query)],
        n_results=min(k, len(_messages(rows))),
        where=(where[0] if len(where) == 1 else {"$and": where}) if where else None,
    )
    status = {r["id"]: r["status"] for r in store.verify(rows)["records"]}
    hits = []
    for key, dist in zip(res["ids"][0], res["distances"][0]):
        row = by_hash.get(key)
        if row is None:
            continue
        hits.append({
            "record_id": row["id"],
            "evidence_id": f"E{row['id']}",
            "capture_id": row["capture_id"],
            "conversation": row["conversation"],
            "platform": row["platform"],
            "sender": row["sender"],
            "time": row["message_time"],
            "text": row["message_text"],
            "flagged": bool(row["flagged"]),
            "similarity": round(1 - dist, 3),
            "verified": status.get(row["id"]) == "ok",
        })
    return hits


def clear() -> None:
    vectors.drop(_collection_name())
