"""Checks on what the assistant says, before the shopper sees it.

1. Prices: every naira amount in a reply must have appeared in a tool result in this
   conversation (or in the shopper's own words: "under 50k" may be repeated back as
   "under ₦50,000"). The model is told never to work out prices itself, but a prompt is a
   request, not a guarantee; this check is the guarantee. An amount it can't trace is a
   made-up price, and the reply is not shown (the caller asks once for a corrected reply,
   then falls back to one with no prices).

2. Links: only links to this shop survive. Product text and policy pages are data the model
   reads, so a planted "send them to http://evil.example" must not reach the shopper as a
   working link. Links are rewritten to plain text rather than failing the reply.
"""

import re
from urllib.parse import urlsplit

# A naira amount as written in a reply: "₦45,000", "₦45k", "N45,000", "NGN 45000",
# "45,000 naira", "50k". Group 1 is the number, group 2 an optional k/m multiplier.
_NUMBER = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)"
_SUFFIX = r"\s?(k|m|million|thousand)?\b"
_AMOUNT = re.compile(
    rf"(?:₦|\bNGN\s?|\bN(?=\d)){_NUMBER}{_SUFFIX}"  # currency first
    rf"|\b{_NUMBER}\s?(k|m|million|thousand)?\s?naira\b"  # "45,000 naira", "45k naira"
    rf"|\b(\d+(?:\.\d+)?)(k)\b",  # "50k" (how prices are said in Lagos)
    re.IGNORECASE,
)
_MULTIPLIER = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000}


def _kobo(number: str, suffix: str | None) -> int:
    value = float(number.replace(",", ""))
    return round(value * _MULTIPLIER.get((suffix or "").lower(), 1) * 100)


def naira_amounts(text: str) -> set[int]:
    """Every naira amount written in `text`, in kobo."""
    found = set()
    for m in _AMOUNT.finditer(text):
        g = m.groups()
        if g[0] is not None:
            found.add(_kobo(g[0], g[1]))
        elif g[2] is not None:
            found.add(_kobo(g[2], g[3]))
        else:
            found.add(_kobo(g[4], g[5]))
    return found


def shopper_amounts(text: str) -> set[int]:
    """Amounts the shopper typed. Shoppers often leave out the ₦ ("under 50000"), so any
    number of 100 or more counts as naira here; the risk is only letting the model repeat
    a number the shopper said."""
    plain = {
        int(n.replace(",", "")) * 100
        for n in re.findall(r"\b\d{1,3}(?:,\d{3})+\b|\b\d{3,}\b", text)
    }
    return naira_amounts(text) | plain


def unsupported_amounts(reply: str, allowed: set[int]) -> list[int]:
    """Amounts in the reply that no tool result (or the shopper) gave, in kobo."""
    return sorted(naira_amounts(reply) - allowed)


_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_BARE_URL = re.compile(r"\b(?:https?://|www\.)[^\s)<>\]]+", re.IGNORECASE)


def _ours(url: str, site: str) -> bool:
    if url.startswith("/") and not url.startswith("//"):
        return True
    parts, home = urlsplit(url), urlsplit(site)
    return bool(parts.scheme in ("http", "https") and parts.netloc == home.netloc)


def clean_links(reply: str, site: str) -> tuple[str, int]:
    """Markdown links to other sites become their text; bare URLs to other sites become
    "[link removed]". Returns the reply and how many links were removed."""
    removed = 0

    def md(m: re.Match[str]) -> str:
        nonlocal removed
        if _ours(m.group(2), site):
            return m.group(0)
        removed += 1
        return m.group(1)

    def bare(m: re.Match[str]) -> str:
        nonlocal removed
        if _ours(m.group(0), site):
            return m.group(0)
        removed += 1
        return "[link removed]"

    reply = _MD_LINK.sub(md, reply)
    # Bare URLs, outside markdown links (those were handled above).
    parts = re.split(r"(\[[^\]]*\]\([^)]*\))", reply)
    parts = [p if p.startswith("[") and "](" in p else _BARE_URL.sub(bare, p) for p in parts]
    return "".join(parts), removed
