"""Vector database shared by the Complaint Assistant and the Evidence Vault.

With DATABASE_URL set (e.g. Supabase): pgvector, in the same Postgres database as
everything else. Without it: ChromaDB, embedded and on disk under DATA_DIR/chroma.
Both are used through the same small collection API (get / add / upsert / query /
delete / count). Vectors come from fastembed's BAAI/bge-small-en-v1.5, which runs
locally: no text is sent anywhere to be embedded.

Collections:
  legal_sections  the legal corpus, re-synced whenever legal_corpus.json changes
  evidence        one vector per sealed Vault message, PII-masked

If the embedding model can't load (offline first run), `available()` turns False
and callers fall back to their keyword paths.
"""

import json
import logging
from pathlib import Path

from app import config, database

log = logging.getLogger("kavach.vectors")

EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_embedder = None
_failed = False
_clients: dict[Path, object] = {}


def embedder():
    """The fastembed model, loaded once. Raises if it can't be loaded."""
    global _embedder, _failed
    if _failed:
        raise RuntimeError("embedding model unavailable")
    if _embedder is None:
        try:
            from fastembed import TextEmbedding

            _embedder = TextEmbedding(EMBED_MODEL, cache_dir=str(config.DATA_DIR / "models"))
        except Exception as exc:  # offline, model download blocked, etc.
            log.warning("embedding model unavailable: %s", exc)
            _failed = True
            raise
    return _embedder


def available() -> bool:
    try:
        embedder()
        return True
    except Exception:
        return False


def embed_docs(texts: list[str]) -> list[list[float]]:
    return [list(map(float, v)) for v in embedder().embed(texts)]


def embed_query(text: str) -> list[float]:
    return list(map(float, next(iter(embedder().query_embed(text)))))


def client():
    """One persistent Chroma client per data directory (tests point DATA_DIR at a temp dir)."""
    path = config.DATA_DIR / "chroma"
    if path not in _clients:
        import chromadb
        from chromadb.config import Settings

        path.mkdir(parents=True, exist_ok=True)
        _clients[path] = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    return _clients[path]


def collection(name: str):
    if database.is_postgres():
        return PgCollection(name)
    # Cosine distance, so similarity = 1 - distance.
    return client().get_or_create_collection(name, configuration={"hnsw": {"space": "cosine"}})


def drop(name: str) -> None:
    if database.is_postgres():
        database.run("DELETE FROM vectors WHERE collection=?", name)
        return
    try:
        client().delete_collection(name)
    except Exception:
        pass  # never created


# ---------- pgvector (Postgres) ----------

def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


class PgCollection:
    """The subset of a Chroma collection Kavach uses, stored in Postgres with pgvector.
    One table for all collections; the untyped `vector` column takes any embedding size,
    and collections are small enough that no index is needed."""

    _ready = False

    def __init__(self, name: str):
        self.name = name
        if not PgCollection._ready:
            with database.connect() as c:
                c.raw.execute("CREATE EXTENSION IF NOT EXISTS vector")
                c.raw.execute(
                    "CREATE TABLE IF NOT EXISTS vectors (collection TEXT NOT NULL, id TEXT NOT NULL, document TEXT,"
                    " embedding vector NOT NULL, metadata JSONB NOT NULL, PRIMARY KEY (collection, id))")
            PgCollection._ready = True

    def count(self) -> int:
        return database.one("SELECT COUNT(*) AS n FROM vectors WHERE collection=?", self.name)["n"]

    def get(self, ids=None, include=("documents", "metadatas")) -> dict:
        sql, args = "SELECT id, document, metadata FROM vectors WHERE collection=?", [self.name]
        if ids is not None:
            sql += " AND id = ANY(?)"
            args.append(list(ids))
        rows = database.rows(sql + " ORDER BY id", *args)
        out = {"ids": [r["id"] for r in rows]}
        if "documents" in include:
            out["documents"] = [r["document"] for r in rows]
        if "metadatas" in include:
            out["metadatas"] = [r["metadata"] for r in rows]
        return out

    def upsert(self, ids, embeddings, metadatas, documents=None) -> None:
        documents = documents or [None] * len(ids)
        with database.connect() as c:
            for i, d, e, m in zip(ids, documents, embeddings, metadatas):
                c.execute("INSERT INTO vectors (collection, id, document, embedding, metadata)"
                          " VALUES (?, ?, ?, ?::vector, ?::jsonb) ON CONFLICT (collection, id) DO UPDATE SET"
                          " document=excluded.document, embedding=excluded.embedding, metadata=excluded.metadata",
                          (self.name, i, d, _vec(e), json.dumps(m)))

    add = upsert

    def delete(self, ids) -> None:
        database.run("DELETE FROM vectors WHERE collection=? AND id = ANY(?)", self.name, list(ids))

    def query(self, query_embeddings, n_results=10, where=None) -> dict:
        """Cosine distance (pgvector's <=>), nearest first. `where`: {"field": value} or {"$and": [...]}."""
        conds = (where or {}).get("$and", [where] if where else [])
        sql = "SELECT id, embedding <=> ?::vector AS d FROM vectors WHERE collection=?"
        args = [_vec(query_embeddings[0]), self.name]
        for cond in conds:
            sql += " AND metadata @> ?::jsonb"
            args.append(json.dumps(cond))
        rows = database.rows(sql + " ORDER BY d LIMIT ?", *args, int(n_results))
        return {"ids": [[r["id"] for r in rows]], "distances": [[float(r["d"]) for r in rows]]}
