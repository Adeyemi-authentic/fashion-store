"""Turn each search query's labelling rule into the list of relevant products.

    uv run python -m scripts.label_search_queries                 # development set
    uv run python -m scripts.label_search_queries --set heldout   # held-out set

The rules in evals/search/queries.jsonl were written by hand, from what a shopper typing that
query would accept, before any search code existed. This script only applies them to the
seeded catalog's structured attributes (never to search output), so the labels can't drift
towards whatever the search happens to return. Only visible (active) products count.

The held-out queries (evals/search/heldout.txt) were written by the user, who hasn't seen the
search prompt; heldout.jsonl turns each "want" line into a rule. A query of kind "no-answer"
has no right product in the catalog: its rule must match nothing, and it is reported
separately rather than scored.

Rule fields (all optional, all must hold):
  categories / exclude_categories   product category slug
  types / exclude_types             attributes.type (e.g. "Wrap Dress")
  patterns                          attributes.pattern, the print name (e.g. "Lekki Sunset")
  genders                           attributes.gender
  occasions                         any of these in attributes.occasion
  colours_any                       any colour containing one of these words
  min_naira / max_naira             the product's "from" price
"""

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.catalog_data import SeedProduct, build_catalog, has_image

EVALS = Path(__file__).resolve().parents[2] / "evals" / "search"
# set name -> (queries, labels, results report)
SETS = {
    "dev": (EVALS / "queries.jsonl", EVALS / "labels.json", EVALS / "RESULTS.md"),
    "heldout": (
        EVALS / "heldout.jsonl",
        EVALS / "heldout_labels.json",
        EVALS / "HELDOUT_RESULTS.md",
    ),
}


def matches(p: SeedProduct, rule: dict[str, Any]) -> bool:
    a = p.attributes
    naira = p.base_price_kobo // 100
    checks = [
        p.category in rule.get("categories", [p.category]),
        p.category not in rule.get("exclude_categories", []),
        a["type"] in rule.get("types", [a["type"]]),
        a["type"] not in rule.get("exclude_types", []),
        a["pattern"] in rule.get("patterns", [a["pattern"]]),
        a["gender"] in rule.get("genders", [a["gender"]]),
        "occasions" not in rule or bool(set(rule["occasions"]) & set(a["occasion"])),
        "colours_any" not in rule
        or any(w.lower() in c.lower() for w in rule["colours_any"] for c in a["colours"]),
        naira >= rule.get("min_naira", 0),
        naira <= rule.get("max_naira", naira),
    ]
    return all(checks)


def load_queries(name: str = "dev") -> list[dict[str, Any]]:
    path = SETS[name][0]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default="dev", choices=SETS)
    args = parser.parse_args()
    labels_path = SETS[args.set][1]
    visible = [p for p in build_catalog() if has_image(p)]
    labels: dict[str, list[str]] = {}
    for q in load_queries(args.set):
        relevant = sorted(p.slug for p in visible if matches(p, q["rule"]))
        no_answer = q["kind"] == "no-answer"
        if no_answer and relevant:
            raise SystemExit(f"{q['id']} ({q['query']!r}) is no-answer but matches {relevant}")
        if not no_answer and not relevant:
            raise SystemExit(f"{q['id']} ({q['query']!r}) has no relevant product: fix the rule")
        labels[q["id"]] = relevant
        print(f"{q['id']} {len(relevant):3d}  {q['query']}")
    labels_path.write_text(json.dumps(labels, indent=1) + "\n")
    print(f"wrote {labels_path} ({len(labels)} queries)")


if __name__ == "__main__":
    main()
