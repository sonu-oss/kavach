"""Vector database shared by the Complaint Assistant and the Evidence Vault.

ChromaDB, embedded and on disk under DATA_DIR/chroma (no server to run), with
vectors from fastembed's BAAI/bge-small-en-v1.5, which runs locally: no text is
sent anywhere to be embedded.

Collections:
  legal_sections  the legal corpus, re-synced whenever legal_corpus.json changes
  evidence        one vector per sealed Vault message, PII-masked

If the embedding model can't load (offline first run), `available()` turns False
and callers fall back to their keyword paths.
"""

import logging
from pathlib import Path

from app import config

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
    # Cosine distance, so similarity = 1 - distance.
    return client().get_or_create_collection(name, configuration={"hnsw": {"space": "cosine"}})


def drop(name: str) -> None:
    try:
        client().delete_collection(name)
    except Exception:
        pass  # never created
