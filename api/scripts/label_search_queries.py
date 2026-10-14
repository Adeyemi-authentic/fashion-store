"""Turn each search query's labelling rule into the list of relevant products.

    uv run python -m scripts.label_search_queries

The rules in evals/search/queries.jsonl were written by hand, from what a shopper typing that
query would accept, before any search code existed. This script only applies them to the
seeded catalog's structured attributes (never to search output), so the labels can't drift
towards whatever the search happens to return. Only visible (active) products count.

Rule fields (all optional, all must hold):
  categories / exclude_categories   product category slug
  types                             attributes.type (e.g. "Wrap Dress")
  genders                           attributes.gender
  occasions                         any of these in attributes.occasion
  colours_any                       any colour containing one of these words
  min_naira / max_naira             the product's "from" price
"""

import json
from pathlib import Path
from typing import Any

from scripts.catalog_data import SeedProduct, build_catalog, has_image

EVALS = Path(__file__).resolve().parents[2] / "evals" / "search"
QUERIES = EVALS / "queries.jsonl"
LABELS = EVALS / "labels.json"


def matches(p: SeedProduct, rule: dict[str, Any]) -> bool:
    a = p.attributes
    naira = p.base_price_kobo // 100
    checks = [
        p.category in rule.get("categories", [p.category]),
        p.category not in rule.get("exclude_categories", []),
        a["type"] in rule.get("types", [a["type"]]),
        a["gender"] in rule.get("genders", [a["gender"]]),
        "occasions" not in rule or bool(set(rule["occasions"]) & set(a["occasion"])),
        "colours_any" not in rule
        or any(w.lower() in c.lower() for w in rule["colours_any"] for c in a["colours"]),
        naira >= rule.get("min_naira", 0),
        naira <= rule.get("max_naira", naira),
    ]
    return all(checks)


def load_queries() -> list[dict[str, Any]]:
    return [json.loads(line) for line in QUERIES.read_text().splitlines() if line.strip()]


def main() -> None:
    visible = [p for p in build_catalog() if has_image(p)]
    labels: dict[str, list[str]] = {}
    for q in load_queries():
        relevant = sorted(p.slug for p in visible if matches(p, q["rule"]))
        if not relevant:
            raise SystemExit(f"{q['id']} ({q['query']!r}) has no relevant product: fix the rule")
        labels[q["id"]] = relevant
        print(f"{q['id']} {len(relevant):3d}  {q['query']}")
    LABELS.write_text(json.dumps(labels, indent=1) + "\n")
    print(f"wrote {LABELS} ({len(labels)} queries)")


if __name__ == "__main__":
    main()
