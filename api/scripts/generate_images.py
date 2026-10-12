"""Build the AI image prompts for every product, with one shared style so the images match.

    uv run python -m scripts.generate_images            # writes prompts only (dry run)

Output: scripts/out/image_prompts.jsonl, one {"slug", "path", "prompt"} per product.
Images belong at web/public/images/products/<slug>.webp, matching the URLs the seed stores.

Generating the images themselves needs a provider and IMAGE_GEN_API_KEY. The provider is
still an open decision (see notes/phase-1.md); once chosen, its call goes in `generate()`.
"""

import json
from pathlib import Path

from scripts.catalog_data import SeedProduct, build_catalog

OUT_DIR = Path(__file__).parent / "out"

# The shared art direction. Changing this changes every image, so it is versioned with the code.
STYLE = (
    "Editorial e-commerce fashion photograph for a contemporary Lagos fashion label. "
    "Soft natural daylight, warm neutral studio backdrop (sand / off-white), subtle shadow, "
    "full garment clearly visible, centred, 4:5 portrait framing, true-to-life fabric texture "
    "and colour, no text, no logos, no watermark."
)


def build_prompt(p: SeedProduct) -> str:
    a = p.attributes
    wearer = {
        "women": "worn by a Nigerian woman model",
        "men": "worn by a Nigerian man model",
        "unisex": "worn by a Nigerian model",
    }[a["gender"]]
    if a["fit"] == "one size":  # accessories and headwear
        subject = f"{a['type']} in {a['colours'][0]}, made of {a['fabric']}, styled flat-lay"
        if p.category == "headwear":
            subject = (
                f"{a['type']} in {a['colours'][0]}, {a['fabric']}, {wearer}, head and shoulders"
            )
    else:
        subject = (
            f"{a['type']} in {a['colours'][0]}, {a['fabric']}, {a['fit']} fit, "
            f"'{a['pattern']}' pattern, {wearer}"
        )
    return f"{subject}. {STYLE}"


def generate(prompt: str, out_path: Path) -> None:
    raise NotImplementedError("image provider not chosen yet (open decision in notes/phase-1.md)")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    catalog = build_catalog()
    with (OUT_DIR / "image_prompts.jsonl").open("w") as f:
        for p in catalog:
            row = {"slug": p.slug, "path": f"web/public{p.image_url}", "prompt": build_prompt(p)}
            f.write(json.dumps(row) + "\n")
    print(f"wrote {len(catalog)} prompts to {OUT_DIR / 'image_prompts.jsonl'}")


if __name__ == "__main__":
    main()
