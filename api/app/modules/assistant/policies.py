"""The shop's policy pages (shipping, returns, size guide) and a small RAG over them.

The pages are markdown files next to this module: the storefront shows them at /help/<slug>
(through GET /policies/<slug>) and the assistant answers from the same text, so the two can
never disagree.

Retrieval: each "## " section is one chunk (with its page title, so "Refunds" is known to be
about returns). Chunks are embedded once per process with the same embedder as products;
a question is embedded and the closest sections are returned. About 25 chunks, so a plain
loop beats a vector index. The vectors are cached under a hash of the text, so editing a
page re-embeds it after a restart.
"""

import asyncio
import hashlib
import logging
import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from app.platform.embeddings import EmbeddingError, get_embedder

log = logging.getLogger("app.assistant.policies")

DIR = Path(__file__).parent / "policies"
SLUGS = ("shipping", "returns", "size-guide")


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    markdown: str


@dataclass(frozen=True)
class Chunk:
    slug: str
    page_title: str
    heading: str
    text: str

    @property
    def url(self) -> str:
        return f"/help/{self.slug}"


@cache
def pages() -> dict[str, Page]:
    out = {}
    for slug in SLUGS:
        md = (DIR / f"{slug}.md").read_text()
        title = md.splitlines()[0].removeprefix("# ").strip()
        out[slug] = Page(slug, title, md)
    return out


def chunks() -> list[Chunk]:
    out = []
    for page in pages().values():
        for part in re.split(r"^## ", page.markdown, flags=re.M)[1:]:
            heading, _, body = part.partition("\n")
            out.append(Chunk(page.slug, page.title, heading.strip(), body.strip()))
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


_vectors: dict[str, list[list[float]]] = {}
_lock = asyncio.Lock()


async def _chunk_vectors(all_chunks: list[Chunk]) -> list[list[float]]:
    embedder = get_embedder()
    texts = [f"{c.page_title}: {c.heading}\n{c.text}" for c in all_chunks]
    key = embedder.model + ":" + hashlib.sha256("\x00".join(texts).encode()).hexdigest()
    async with _lock:  # the first questions after a restart would otherwise all embed
        if key not in _vectors:
            _vectors.clear()
            _vectors[key] = await embedder.embed(texts, "document")
    return _vectors[key]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def keyword_search(question: str, k: int = 3) -> list[Chunk]:
    """Fallback when the embedding service is down: sections ranked by shared words."""
    q = _words(question)
    scored = sorted(
        chunks(), key=lambda c: -len(q & _words(f"{c.page_title} {c.heading} {c.text}"))
    )
    return scored[:k]


async def search(question: str, k: int = 3) -> list[Chunk]:
    all_chunks = chunks()
    try:
        vectors = await _chunk_vectors(all_chunks)
        q = (await get_embedder().embed([question], "query"))[0]
    except EmbeddingError as exc:
        log.warning("policy embedding failed; keyword fallback", extra={"error": str(exc)})
        return keyword_search(question, k)
    scored = sorted(zip(all_chunks, vectors, strict=True), key=lambda cv: -_cosine(q, cv[1]))
    return [c for c, _ in scored[:k]]
