"""Description pipeline eval: real Claude calls on the seeded catalog's facts.

    uv run python -m scripts.eval_descriptions [--limit N]   # writes evals/descriptions/RESULTS.md

1. Draft a description for every visible product: how many pass all checks first time,
   after the one retry, or not at all, and which checks fail.
2. Plant false claims in drafts that passed, one per kind, and see which layer catches
   each: the free rule checks, the Claude claim check, or neither.

No database needed: the facts come from scripts.catalog_data, as the seed would write them.
"""

import argparse
import asyncio
import random
import statistics
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.modules.catalog.descriptions import (
    DraftOutcome,
    Facts,
    Usage,
    judge_claims,
    rule_checks,
    write_description,
)
from app.platform.llm import get_llm
from scripts.catalog_data import build_catalog, has_image

OUT = Path(__file__).resolve().parents[2] / "evals" / "descriptions"
# claude-haiku-5-5 list prices, USD per million tokens (prompts under 100K tokens).
PRICE_IN, PRICE_OUT = 0.10, 0.50

# False claims to plant, each appended to a draft that passed. The literal ones are what
# the rule list was written for; the paraphrased ones are what only a model can catch.
PLANTED = {
    "material (literal)": "It is fully lined in silk.",
    "care (literal)": "It is machine washable.",
    "construction (literal)": "Every piece is handmade to order.",
    "fit (literal)": "It has a stretchy waistband that adjusts to you.",
    "property (paraphrase)": "The fabric keeps you cool on the hottest afternoons.",
    "detail (paraphrase)": "Two deep side pockets hold your phone and keys.",
    "lining (paraphrase)": "A soft inner layer makes it smooth against the skin.",
    "care (paraphrase)": "It is pre-washed, so it won't shrink.",
    "sizing advice": "It runs small, so order one size up.",
}


def facts_from_seed() -> list[Facts]:
    return [
        Facts(
            name=p.name,
            category=p.category,
            attributes=p.attributes,
            colours=sorted({v.colour for v in p.variants}),
            sizes=sorted({v.size for v in p.variants}),
        )
        for p in build_catalog()
        if has_image(p)
    ]


async def main(limit: int | None, concurrency: int, planted_on: int) -> None:
    client = get_llm()
    if client is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    model = get_settings().llm_model
    facts = facts_from_seed()[:limit]
    gate = asyncio.Semaphore(concurrency)

    async def one(f: Facts) -> tuple[Facts, DraftOutcome | None, float, str]:
        async with gate:
            t0 = time.perf_counter()
            try:
                outcome = await write_description(client, model, f)
                return f, outcome, time.perf_counter() - t0, ""
            except Exception as exc:  # noqa: BLE001 - recorded in the report
                return f, None, time.perf_counter() - t0, f"{type(exc).__name__}: {exc}"

    print(f"drafting {len(facts)} descriptions with {model}...")
    results = await asyncio.gather(*(one(f) for f in facts))
    ok = [(f, o, s) for f, o, s, err in results if o is not None]
    errors = [err for *_, err in results if err]
    usage = Usage()
    for _, o, _ in ok:
        usage.input_tokens += o.usage.input_tokens
        usage.output_tokens += o.usage.output_tokens
    first = sum(o.passed and o.attempts == 1 for _, o, _ in ok)
    second = sum(o.passed and o.attempts == 2 for _, o, _ in ok)
    failed = [o for _, o, _ in ok if not o.passed]
    fail_reasons = Counter(c.name for o in failed for c in o.checks if not c.passed)
    first_fail = Counter(name for _, o, _ in ok for name in (o.earlier_failures[:1] or [[]])[0])
    cost = (usage.input_tokens * PRICE_IN + usage.output_tokens * PRICE_OUT) / 1e6

    print(f"planting false claims in {planted_on} passing drafts...")
    passing = [(f, o) for f, o, _ in ok if o.passed]
    random.Random(7).shuffle(passing)
    caught: dict[str, Counter[str]] = {k: Counter() for k in PLANTED}
    judge_usage = Usage()

    async def plant(f: Facts, text: str, kind: str, claim: str) -> None:
        bad = f"{text} {claim}"
        if not all(c.passed for c in rule_checks(bad, f)):
            caught[kind]["rules"] += 1
            return
        async with gate:
            verdict = await judge_claims(client, model, bad, f, judge_usage)
        caught[kind]["claude" if not verdict.passed else "missed"] += 1

    await asyncio.gather(
        *(
            plant(f, o.text, kind, claim)
            for f, o in passing[:planted_on]
            for kind, claim in PLANTED.items()
        )
    )
    cost += (judge_usage.input_tokens * PRICE_IN + judge_usage.output_tokens * PRICE_OUT) / 1e6

    n = len(ok)
    lines = [
        "# Description pipeline eval",
        "",
        f"Generated by `uv run python -m scripts.eval_descriptions` (from `api/`) on "
        f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC with `{model}`. Real API calls; total cost "
        f"about ${cost:.3f} (list prices).",
        "",
        "## 1. Drafting every visible product",
        "",
        "| Products | Passed first time | Passed after 1 retry | Still failing | API errors |",
        "|---|---|---|---|---|",
        f"| {len(facts)} | {first} ({first / n:.0%}) | {second} ({second / n:.0%}) "
        f"| {len(failed)} ({len(failed) / n:.0%}) | {len(errors)} |",
        "",
        f"Per product: {usage.input_tokens / n:.0f} input + {usage.output_tokens / n:.0f} "
        f"output tokens on average; p50 {statistics.median(s for *_, s in ok):.1f} s "
        f"(draft + checks + any retry), {concurrency} at a time.",
        "",
        "Why first attempts failed: "
        + (", ".join(f"{k} ({v})" for k, v in first_fail.most_common()) or "none")
        + ".",
        "",
        "Checks still failing after the retry: "
        + (", ".join(f"{k} ({v})" for k, v in fail_reasons.most_common()) or "none")
        + ".",
        "",
        "## 2. Planted false claims: which layer catches them",
        "",
        f"Each claim was added to {min(planted_on, len(passing))} drafts that had passed.",
        "",
        "| Planted claim | Rule checks | Claude claim check | Missed |",
        "|---|---|---|---|",
    ]
    for kind, claim in PLANTED.items():
        c = caught[kind]
        lines.append(f'| {kind}: "{claim}" | {c["rules"]} | {c["claude"]} | {c["missed"]} |')
    lines += ["", "## Samples (first 5 that passed)", ""]
    for f, o in passing[:5]:
        lines += [f"**{f.name}** ({f.category}, {o.attempts} attempt(s))", "", f"> {o.text}", ""]
    if failed:
        lines += ["## Drafts still failing", ""]
        for o in failed[:5]:
            bad = "; ".join(f"{c.name}: {c.detail}" for c in o.checks if not c.passed)
            lines += [f"- {bad}", f"  > {o.text}"]
    if errors:
        lines += ["", "## API errors", ""] + [f"- {e}" for e in errors[:5]]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT / 'RESULTS.md'} (cost ~${cost:.3f})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--planted-on", type=int, default=20)
    a = parser.parse_args()
    asyncio.run(main(a.limit, a.concurrency, a.planted_on))
