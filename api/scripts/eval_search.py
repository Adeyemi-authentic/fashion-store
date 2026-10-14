"""Search eval: every search variant against the 50 labelled queries.

    uv run python -m scripts.eval_search            # writes evals/search/RESULTS.md

Runs on a throwaway local Postgres (pgserver), never on Neon: migrate, seed, embed the
catalog, then search. Embeddings use the real Voyage model from .env, cached on disk
(evals/search/.cache/, git-ignored), so a re-run costs nothing. The LLM step (when a variant
uses it) calls Claude for real; its answers are cached the same way unless --fresh-llm.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pgserver
from alembic import command

EVALS = Path(__file__).resolve().parents[2] / "evals" / "search"
CACHE = EVALS / ".cache"
K = 10


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    config: dict[str, Any] = field(default_factory=dict)


# Every variant ever compared stays here, so RESULTS.md always shows the full history of
# what was tried. Order: baselines first, then each tuning step.
VARIANTS = [
    Variant(
        "keyword",
        "Keyword only: every word must match (websearch_to_tsquery)",
        {"mode": "keyword", "keyword_mode": "and", "use_llm": False},
    ),
    Variant(
        "vector",
        "Vector only: Voyage embedding, cosine distance",
        {"mode": "vector", "use_llm": False},
    ),
    Variant(
        "hybrid",
        "Keyword (all words) + vector, merged with RRF (k=60)",
        {"mode": "hybrid", "keyword_mode": "and", "use_llm": False},
    ),
    Variant(
        "hybrid+llm",
        "hybrid, plus Claude turning the query into filters",
        {"mode": "hybrid", "keyword_mode": "and", "use_llm": True, "llm_min_words": 0},
    ),
    Variant(
        "keyword-or",
        "Keyword only, any word may match (ranking rewards matching more words)",
        {"mode": "keyword", "keyword_mode": "or", "use_llm": False},
    ),
    Variant(
        "hybrid-or",
        "Keyword (any word) + vector, RRF",
        {"mode": "hybrid", "keyword_mode": "or", "use_llm": False},
    ),
    Variant(
        "hybrid-or+llm",
        "hybrid-or, plus Claude turning the query into filters",
        {"mode": "hybrid", "keyword_mode": "or", "use_llm": True, "llm_min_words": 0},
    ),
    Variant(
        "hybrid+llm(3+ words)",
        "**What the storefront uses.** hybrid+llm, but the LLM only for queries of 3+ words "
        "or with a digit",
        {"mode": "hybrid", "keyword_mode": "and", "use_llm": True, "llm_min_words": 3},
    ),
]


class CachedParser:
    """Wraps the Claude query parser; remembers each answer on disk (by model, prompt
    version and query), and how long the real call took."""

    def __init__(self, inner: Any, model: str, prompt_version: str, fresh: bool) -> None:
        self.inner = inner
        self.path = CACHE / f"understood-{model}-{prompt_version}.json"
        self.data: dict[str, Any] = (
            json.loads(self.path.read_text()) if self.path.exists() and not fresh else {}
        )
        self.latencies_ms: list[float] = []

    async def parse(self, q: str, vocab: Any) -> Any:
        from dataclasses import asdict

        from app.modules.catalog.service import SearchFilters
        from app.modules.search.understand import Understood

        key = f"{vocab.fingerprint()}|{q}"
        if key not in self.data:
            t0 = time.perf_counter()
            u = await self.inner.parse(q, vocab)
            self.latencies_ms.append((time.perf_counter() - t0) * 1000)
            self.data[key] = None if u is None else {"text": u.text, **asdict(u.filters)}
            CACHE.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
        d = self.data[key]
        if d is None:
            return None
        f = {k: v for k, v in d.items() if k != "text"}
        return Understood(d["text"], SearchFilters(**{**f, "colours": tuple(f["colours"])}))


class CachedEmbedder:
    """Wraps the real embedder; remembers every vector on disk by (model, kind, text)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.model = inner.model
        self.path = CACHE / f"embeddings-{self.model}.json"
        self.data: dict[str, list[float]] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )
        self.calls = 0

    def _key(self, text: str, kind: str) -> str:
        return hashlib.sha256(f"{self.model}|{kind}|{text}".encode()).hexdigest()

    async def embed(self, texts: list[str], kind: Any) -> list[list[float]]:
        missing = [t for t in texts if self._key(t, kind) not in self.data]
        if missing:
            self.calls += 1
            for t, v in zip(missing, await self.inner.embed(missing, kind), strict=True):
                self.data[self._key(t, kind)] = v
            CACHE.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data))
        return [self.data[self._key(t, kind)] for t in texts]


def start_database() -> tuple[Any, str]:
    server = pgserver.get_server(tempfile.mkdtemp(prefix="flyfit-eval-"), cleanup_mode="delete")
    server.psql("CREATE DATABASE flyfit_eval;")
    socket_dir = server.get_uri().split("host=")[1]
    url = f"postgresql+psycopg://postgres@/flyfit_eval?host={urllib.parse.quote(socket_dir)}"
    return server, url


def metrics(ranked: list[str], relevant: set[str]) -> tuple[float, float]:
    top = ranked[:K]
    recall = len(set(top) & relevant) / min(K, len(relevant))
    first = next((i for i, slug in enumerate(ranked, start=1) if slug in relevant), None)
    return recall, (1.0 / first if first else 0.0)


async def run(variants: list[Variant], fresh_llm: bool) -> dict[str, Any]:
    from app.config import get_settings
    from app.db import dispose_engine, get_sessionmaker
    from app.modules.search import service as search
    from app.modules.search import understand
    from app.modules.search.embeddings import refresh_embeddings
    from app.platform import embeddings
    from scripts.label_search_queries import LABELS, load_queries
    from scripts.seed_catalog import seed

    s = get_settings()
    if not s.voyage_api_key:
        raise SystemExit("VOYAGE_API_KEY is not set: the eval needs the real embedding model")
    embedder = CachedEmbedder(embeddings.get_embedder())
    embeddings._embedder = embedder  # type: ignore[assignment]
    claude = understand.get_parser()
    if claude is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set: the LLM variants need it")
    parser = CachedParser(claude, s.llm_model, understand.PROMPT_VERSION, fresh_llm)
    understand._parser = parser  # type: ignore[assignment]

    queries = load_queries()
    labels = json.loads(LABELS.read_text())
    results: dict[str, Any] = {}
    async with get_sessionmaker()() as session:
        seeded = await seed(session)
        await refresh_embeddings(session)
        await session.commit()
        # All query embeddings up front, so the timings below are the search itself.
        await embedder.embed([q["query"] for q in queries], "query")
        for v in variants:
            config = search.SearchConfig(**v.config)
            rows = []
            for q in queries:
                # Time every query as a first-time search (answers come from disk caches).
                search.query_vectors.clear()
                understand.understood_cache.clear()
                t0 = time.perf_counter()
                result = await search.search(session, q["query"], limit=50, config=config)
                ms = (time.perf_counter() - t0) * 1000
                ranked = [c.slug for c in result.items]
                recall, rr = metrics(ranked, set(labels[q["id"]]))
                rows.append(
                    {
                        **q,
                        "recall": recall,
                        "rr": rr,
                        "ms": ms,
                        "top": ranked[:K],
                        "relevant": labels[q["id"]],
                        "n_results": len(ranked),
                        "filters": str(result.filters),
                        "relaxed": result.relaxed,
                    }
                )
            results[v.name] = rows
            print(f"{v.name:28s} recall@10 {statistics.mean(r['recall'] for r in rows):.3f}")
    await dispose_engine()
    return {
        "results": results,
        "active": seeded.active,
        "embedding_model": embedder.model,
        "llm_model": s.llm_model,
        "llm_ms": parser.latencies_ms,
    }


def write_report(variants: list[Variant], out: dict[str, Any]) -> None:
    res = out["results"]
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    kinds = sorted({r["kind"] for r in next(iter(res.values()))})
    lines = [
        "# Search eval results",
        "",
        f"Generated by `uv run python -m scripts.eval_search` (from `api/`) on "
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC at commit `{sha}`. "
        f"{len(next(iter(res.values())))} queries, {out['active']} visible products, "
        f"embeddings `{out['embedding_model']}`, LLM `{out['llm_model']}`. "
        "Metric definitions are in README.md. Latency is measured on a local database "
        "with the embedding cache cleared before each query (embeddings themselves come "
        "from the disk cache, so Voyage's network time is not included).",
        "",
        "| Variant | recall@10 | MRR | zero results | p50 ms | What it is |",
        "|---|---|---|---|---|---|",
    ]
    for v in variants:
        rows = res[v.name]
        lines.append(
            f"| `{v.name}` | {statistics.mean(r['recall'] for r in rows):.3f} "
            f"| {statistics.mean(r['rr'] for r in rows):.3f} "
            f"| {sum(r['n_results'] == 0 for r in rows)} "
            f"| {statistics.median(r['ms'] for r in rows):.0f} | {v.description} |"
        )
    if out["llm_ms"]:
        ms = sorted(out["llm_ms"])
        lines += [
            "",
            f"Live Claude calls in this run: {len(ms)}, p50 "
            f"{statistics.median(ms):.0f} ms, p95 {ms[int(0.95 * (len(ms) - 1))]:.0f} ms "
            "(from Lagos; the search runs this call alongside the query embedding).",
        ]
    header = "| Variant | " + " | ".join(kinds) + " |"
    lines += ["", "## recall@10 by kind of query", "", header, "|---" * (len(kinds) + 1) + "|"]
    for v in variants:
        by = [statistics.mean(r["recall"] for r in res[v.name] if r["kind"] == k) for k in kinds]
        lines.append(f"| `{v.name}` | " + " | ".join(f"{x:.2f}" for x in by) + " |")
    best = variants[-1].name
    lines += ["", f"## Queries below 1.0 in `{best}` (the last variant)", ""]
    lines += ["| Query | recall@10 | Missed (relevant, not in top 10) | Filters applied |"]
    lines += ["|---|---|---|---|"]
    for r in res[best]:
        if r["recall"] < 1.0:
            missed = [s for s in r["relevant"] if s not in r["top"]]
            shown = ", ".join(missed[:4]) + (
                f" (+{len(missed) - 4} more)" if len(missed) > 4 else ""
            )
            applied = r["filters"].replace("SearchFilters", "") + (" (relaxed)" * r["relaxed"])
            lines.append(f"| {r['query']} | {r['recall']:.2f} | {shown} | `{applied}` |")
    (EVALS / "RESULTS.md").write_text("\n".join(lines) + "\n")
    (CACHE / "last_run.json").write_text(json.dumps(res, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="variant names to run (default: all)")
    parser.add_argument("--fresh-llm", action="store_true", help="ignore cached LLM answers")
    args = parser.parse_args()
    variants = [v for v in VARIANTS if not args.only or v.name in args.only]

    server, url = start_database()
    try:
        os.environ["DATABASE_URL"] = url
        os.environ["DATABASE_URL_DIRECT"] = url
        from app.config import get_settings
        from tests.conftest import alembic_config

        get_settings.cache_clear()
        command.upgrade(alembic_config(url), "head")
        out = asyncio.run(run(variants, args.fresh_llm))
        write_report(variants, out)
        print(f"wrote {EVALS / 'RESULTS.md'}")
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
