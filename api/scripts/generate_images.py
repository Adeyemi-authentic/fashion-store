"""Generate the AI product images, with one shared style so all 150 look like one brand.

    uv run python -m scripts.generate_images                 # prompts only (no cost)
    uv run python -m scripts.generate_images --sample        # 3 images to approve the style
    uv run python -m scripts.generate_images --all           # everything not generated yet
    uv run python -m scripts.generate_images --only <slug>   # one product (add --force to redo)

Prompts are written to scripts/out/image_prompts.jsonl. Images go to
web/public/images/products/<slug>.webp, matching the URLs the seed stores. Existing files are
skipped unless --force, so an interrupted run can simply be started again.

Uses the OpenAI Images API with IMAGE_GEN_API_KEY and IMAGE_GEN_MODEL from ../.env.
"""

import argparse
import asyncio
import base64
import json
import random
import sys
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import REPO_ROOT
from scripts.catalog_data import SeedProduct, build_catalog

OUT_DIR = Path(__file__).parent / "out"
WEB_PUBLIC = REPO_ROOT / "web" / "public"
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"

# The shared art direction. Changing this changes every image, so it is versioned with the code.
STYLE = (
    "Editorial e-commerce fashion photograph for a contemporary Lagos fashion label. "
    "Soft natural daylight, warm neutral studio backdrop (sand / off-white), subtle shadow, "
    "full garment clearly visible, centred, portrait framing, true-to-life fabric texture "
    "and colour, no text, no logos, no watermark."
)
SIZE = "1024x1536"  # portrait; the storefront crops to 4:5
QUALITY = "medium"
CONCURRENCY = 3
MAX_ATTEMPTS = 4

# One womenswear, one menswear, one accessory: enough to judge the style before paying for 150.
SAMPLE_CATEGORIES = ("ankara", "agbada-kaftans", "accessories")


class ImageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    image_gen_api_key: str = ""
    image_gen_model: str = "gpt-image-2"


def build_prompt(p: SeedProduct) -> str:
    a = p.attributes
    wearer = {
        "women": "worn by a Nigerian woman model",
        "men": "worn by a Nigerian man model",
        "unisex": "worn by a Nigerian model",
    }[a["gender"]]
    if a["fit"] == "one size":  # accessories, scarves, gele sets, headwear
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


def image_path(p: SeedProduct) -> Path:
    return WEB_PUBLIC / p.image_url.lstrip("/")


class ImageError(Exception):
    pass


async def generate(client: httpx.AsyncClient, settings: ImageSettings, prompt: str) -> bytes:
    """One image as WebP bytes. Retries rate limits and server errors with backoff."""
    body = {
        "model": settings.image_gen_model,
        "prompt": prompt,
        "size": SIZE,
        "quality": QUALITY,
        "output_format": "webp",
        "output_compression": 85,
        "n": 1,
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = await client.post(OPENAI_IMAGES_URL, json=body)
        if resp.status_code == 200:
            return base64.b64decode(resp.json()["data"][0]["b64_json"])
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt == MAX_ATTEMPTS:
            try:
                message = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = resp.text[:300]
            raise ImageError(f"HTTP {resp.status_code}: {message}")
        await asyncio.sleep(2**attempt + random.random())
    raise AssertionError("unreachable")


def pick(catalog: list[SeedProduct], args: argparse.Namespace) -> list[SeedProduct]:
    if args.only:
        chosen = [p for p in catalog if p.slug in args.only]
        missing = set(args.only) - {p.slug for p in chosen}
        if missing:
            sys.exit(f"unknown slug(s): {', '.join(sorted(missing))}")
        return chosen
    if args.sample:
        return [next(p for p in catalog if p.category == c) for c in SAMPLE_CATEGORIES]
    return catalog


async def run(products: list[SeedProduct], settings: ImageSettings, force: bool) -> int:
    todo = [p for p in products if force or not image_path(p).exists()]
    print(
        f"{len(todo)} to generate ({len(products) - len(todo)} already exist) "
        f"with {settings.image_gen_model}, {SIZE}, {QUALITY}"
    )
    sem = asyncio.Semaphore(CONCURRENCY)
    failures = 0
    headers = {"Authorization": f"Bearer {settings.image_gen_api_key}"}

    async with httpx.AsyncClient(headers=headers, timeout=180) as client:

        async def one(p: SeedProduct) -> None:
            nonlocal failures
            async with sem:
                try:
                    data = await generate(client, settings, build_prompt(p))
                except (ImageError, httpx.HTTPError) as exc:
                    failures += 1
                    print(f"  FAILED {p.slug}: {exc}")
                    return
                path = image_path(p)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                print(f"  ok {p.slug} ({len(data) // 1024} KB)")

        await asyncio.gather(*(one(p) for p in todo))
    return failures


def write_prompts(catalog: list[SeedProduct]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    with (OUT_DIR / "image_prompts.jsonl").open("w") as f:
        for p in catalog:
            path = str(image_path(p).relative_to(REPO_ROOT))
            f.write(json.dumps({"slug": p.slug, "path": path, "prompt": build_prompt(p)}) + "\n")
    print(f"wrote {len(catalog)} prompts to {OUT_DIR / 'image_prompts.jsonl'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample", action="store_true", help="generate 3 style samples")
    mode.add_argument("--all", action="store_true", help="generate every missing image")
    mode.add_argument("--only", nargs="+", metavar="SLUG", help="generate these products")
    parser.add_argument("--force", action="store_true", help="overwrite existing images")
    args = parser.parse_args()

    catalog = build_catalog()
    write_prompts(catalog)
    if not (args.sample or args.all or args.only):
        return

    settings = ImageSettings()
    if not settings.image_gen_api_key:
        sys.exit("IMAGE_GEN_API_KEY is not set in .env")
    failures = asyncio.run(run(pick(catalog, args), settings, args.force))
    if failures:
        sys.exit(f"{failures} image(s) failed; re-run the same command to retry only those")


if __name__ == "__main__":
    main()
