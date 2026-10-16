"""Hybrid retrieval over the legal corpus.

Three rankers, fused with Reciprocal Rank Fusion (RRF):
  1. BM25 over title + summary + keywords (lexical: catches "stalking", "UPI", "acid")
  2. Dense embeddings (fastembed, BAAI/bge-small-en-v1.5, runs locally; semantic:
     "he says he'll wait near my college" ~ stalking without the word)
  3. A category prior from the Smart Shield verdict (sections tagged for that threat type)

If the embedding model can't load (offline first run), retrieval degrades to BM25 + prior.
"""

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app import config

log = logging.getLogger("kavach.complaint")

CORPUS_PATH = Path(__file__).with_name("legal_corpus.json")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60


@dataclass
class Candidate:
    section: dict
    fused: float = 0.0
    ranks: dict = field(default_factory=dict)  # ranker name -> 1-based rank


@lru_cache(maxsize=1)
def corpus() -> list[dict]:
    return json.loads(CORPUS_PATH.read_text())["sections"]


def by_id() -> dict[str, dict]:
    return {s["id"]: s for s in corpus()}


def _doc_text(s: dict) -> str:
    return " ".join([s["title"], s["summary"], " ".join(s["keywords"]), s["online_applicability"]])


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("a an the of to or and in on for by with is are be was any her his him she he it that this as at from".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(map(len, docs)) / len(docs)
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, d in zip(self.tf, self.docs):
            s = 0.0
            for t in query:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out


@lru_cache(maxsize=1)
def _bm25() -> BM25:
    return BM25([tokenize(_doc_text(s)) for s in corpus()])


_embedder = None
_doc_vectors = None
_embed_failed = False


def _dense_scores(query: str) -> list[float] | None:
    """Cosine similarity of the query to every section, or None if embeddings are unavailable."""
    global _embedder, _doc_vectors, _embed_failed
    if _embed_failed:
        return None
    try:
        if _embedder is None:
            import numpy as np
            from fastembed import TextEmbedding

            _embedder = TextEmbedding(EMBED_MODEL, cache_dir=str(config.DATA_DIR / "models"))
            vecs = np.array(list(_embedder.embed([_doc_text(s) for s in corpus()])))
            _doc_vectors = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        import numpy as np

        q = np.array(list(_embedder.query_embed(query)))[0]
        q = q / np.linalg.norm(q)
        return (_doc_vectors @ q).tolist()
    except Exception as exc:  # offline, model download blocked, etc.
        log.warning("dense retrieval unavailable, using BM25 + prior only: %s", exc)
        _embed_failed = True
        return None


def dense_available() -> bool:
    return not _embed_failed


def retrieve(query: str, category: str | None, k: int = 8) -> list[Candidate]:
    sections = corpus()
    cands = [Candidate(section=s) for s in sections]
    rankings: dict[str, list[float]] = {"bm25": _bm25().scores(tokenize(query))}

    dense = _dense_scores(query)
    if dense is not None:
        rankings["dense"] = dense

    if category and category != "safe":
        # Primary-tagged sections rank above secondary ones; untagged ones are left out of this list.
        rankings["prior"] = [
            (len(s["categories"]) - s["categories"].index(category)) if category in s["categories"] else 0.0
            for s in sections
        ]

    for name, scores in rankings.items():
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        for rank, i in enumerate(order, 1):
            if scores[i] <= 0:
                continue  # no signal from this ranker for this section
            cands[i].ranks[name] = rank
            cands[i].fused += 1 / (RRF_K + rank)

    cands.sort(key=lambda c: c.fused, reverse=True)
    return [c for c in cands if c.fused > 0][:k]
