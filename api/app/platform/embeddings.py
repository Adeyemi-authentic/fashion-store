"""Text embeddings for search and "similar items".

`VoyageEmbedder` calls the Voyage API. `HashEmbedder` is a local stand-in (hashed words and
character trigrams) for tests and for running without a key: it puts texts that share words
near each other, so search behaves sensibly, but it knows no synonyms. Tests swap the
embedder through `_embedder`, the same way they swap the Paystack client.
"""

import hashlib
import logging
import math
import re
from typing import Literal, Protocol

import httpx

from app.config import get_settings

log = logging.getLogger("app.embeddings")

DIM = 1024  # products.embedding is vector(1024)
Kind = Literal["document", "query"]


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]: ...


class VoyageEmbedder:
    URL = "https://api.voyageai.com/v1/embeddings"
    BATCH = 64
    # A search request waits on the query embedding, so fail fast rather than hang.
    TIMEOUT = httpx.Timeout(10.0, connect=5.0)

    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=self.TIMEOUT
        )

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = texts[i : i + self.BATCH]
            try:
                resp = await self._client.post(
                    self.URL,
                    # input_type adds Voyage's own retrieval prefix: queries and documents
                    # are embedded slightly differently, which improves retrieval.
                    json={"input": batch, "model": self.model, "input_type": kind},
                )
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"voyage request failed: {exc}") from exc
            if resp.status_code != 200:
                raise EmbeddingError(f"voyage {resp.status_code}: {resp.text[:200]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return out


class HashEmbedder:
    model = "local-hash-v1"

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vec = [0.0] * DIM
        words = re.findall(r"[a-z0-9]+", text.lower())
        features = words + [w[i : i + 3] for w in words for i in range(max(1, len(w) - 2))]
        for f in features:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            vec[h % DIM] += 1.0 if (h >> 32) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        s = get_settings()
        if s.voyage_api_key:
            _embedder = VoyageEmbedder(s.voyage_api_key, s.embedding_model)
        else:
            log.warning("VOYAGE_API_KEY is not set: using the local hash embedder")
            _embedder = HashEmbedder()
    return _embedder
