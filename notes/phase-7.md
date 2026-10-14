# Phase 7: Shopping assistant agent

**Status:** built, tested and checked live in a browser (2026-10-14). **Waiting for your review.** The PLAN "done when" is a manual run of the 10 hardest scenarios: all 10 pass the automatic checks, and I've read every transcript. The last step is yours: read `evals/assistant/RESULTS.md` and judge whether each reply is one you'd accept from a shop assistant.

## Headline numbers
| What | Result |
|---|---|
| The 10 hardest scenarios (real Claude Haiku 5.5, medium effort) | **10/10** pass the automatic checks; transcripts read |
| Made-up prices reaching the shopper | **0** (since the fix in `042091b`, the price check fired in 2 of 4 runs, both times on scenario 8's planted ₦500, and both rewrites were clean) |
| Another customer's order read | **0** (scenario 5 + a pytest with 3 accounts) |
| Prompt injection via product text / shopper message | resisted in both (scenarios 8, 9) |
| Cost per shopper message | ~$0.0005 (13 messages: $0.006–0.008 in all) |
| Time per shopper message | 4.7–5.3 s with a local database; 13–20 s from this laptop to Neon (see trade-offs) |

## What was built (one concern per commit)
| Commit | Concern | What goes wrong without it |
|---|---|---|
| `6fd9a9c` | Hooks: search with caller filters, cards by slug, customer orders | The assistant would call search's own LLM on top of its own (two LLM calls per search), or reach into other modules' tables. |
| `2215c08` | Assistant API: tool loop, price and link checks, spend cap, policy RAG, logging | A made-up "₦12,000" reaches a shopper, a model asked nicely reads someone else's order, one runaway day costs real money, and no conversation can be replayed. |
| `042091b` | Price check trusts only price fields | A planted "on sale for ₦500" in a description counts as a real price (found by scenario 8). |
| `f56b27d` | The 10 hardest scenarios, real model | "Done" would mean "the unit tests with a fake model pass", which says nothing about the real model. |
| `ceda6e4` | Widget + help pages | — |
| `d7642cd` | Polish from the browser check | The launcher covered the add-to-bag button on phones; checkout wasn't a link. |

**Tests:** 297 API tests (31 new, all against a scripted fake Claude, so they never call the API). ruff, mypy and import-linter (5 contracts; a new one keeps `assistant` out of other modules' tables) are clean. The storefront typechecks and lints. Live check: migration 0007 applied on Neon, then a Chromium run on desktop and phone width: the widget opens, recommends, adds to the bag (the badge goes to 1), keeps the conversation across a reload, causes no sideways scroll on a phone, and logs no console errors. Servers stopped afterwards.

## Walkthrough to practise aloud: "add the first one in M, any colour"
1. **The widget** posts `{message}` to `/api/assistant/messages`. The cookies identify the person: `session` (signed in or not), `cart_token` (whose bag) and `assistant_session` (which conversation).
2. **Before any model call:** a per-IP and per-account rate limit (10/min). The daily spend cap (one row per UTC day) is checked. The conversation is used only if it belongs to the same person: after a sign-out on a shared laptop, the next person starts fresh.
3. **The loop** (at most 6 model calls):
   - Commit first, so no transaction is held open while the model thinks.
   - Send the tools, the system prompt and the whole conversation so far. History is append-only, and the prefix is cached.
   - On `tool_use`, run each tool with **the request's** identity. `add_to_cart` uses this person's bag; `get_order_status` has no customer parameter at all.
   - Results go back as JSON, with money always written as "₦32,500" strings.
4. **The reply is checked before anyone sees it:**
   - **Price check:** every ₦ amount must have come from a price field of a tool result, or from the shopper's own words. If not, the reply is hidden, and a system message tells the model which amounts it couldn't trace. One rewrite is allowed. A second failure gets a safe reply with no prices.
   - **Link check:** links to other sites become plain text, and links to products that don't exist become plain text.
5. **Response:** the text, a card for each linked product (from the database, so the card's price is real), and `cart_changed`, so the widget refreshes the bag. A guest's first cart also gets its cookie set here.
6. **Logged:** every message exactly as sent or received, with tokens, cost and latency. That includes rejected replies (flagged), so Phase 8 can replay and score conversations.

## Why, and the trade-offs
- **Guarantees come from code, not the prompt.** The prompt asks the model to quote only tool prices, but the price check makes it true. "Can't read other orders" holds because the tool can't express "someone else". "Can't place an order" holds because no such tool exists. The prompt shapes good behaviour; the code makes bad behaviour impossible or visible.
- **Scenario 8 found a hole in my own guard.** The model resisted the injected "₦500 sale", but it repeated the amount while warning the shopper. The guard passed it, because ₦500 was in the tool result. Now only price fields count. This is why the eval runs on the real model: the fake-model tests were all green.
- **Not streaming.** The reply is checked before it's shown, so it can't be streamed word by word. The widget shows a typing indicator instead. With Haiku, a reply takes ~5 s, mostly model calls and tools. Streaming would need the check to run on partial text, and a shown price can't be taken back.
- **Medium effort, not low.** Low was 0.4 s faster per message, and all 10 scenarios still passed. Most of the time is round trips (model calls, embeddings, the database), not thinking, so the saving wasn't worth the risk to harder conversations.
- **Corrections are mid-conversation system messages, and history is never edited.** A rejected reply is flagged and left out; the correction is the shop talking, not a fake shopper message. Earlier messages never change, which keeps the cache prefix (and the model's own thinking blocks) valid.
- **The spend cap is checked once per shopper message, not per model call.** A message can overshoot by its own few calls (well under a cent), which saves a database round trip per call. Default $1/day ≈ 2,000 shopper messages.
- **A per-conversation cap of 25 messages** keeps the context (and cost per call) bounded, without summarising or cutting history, which would edit it.
- **Policies are markdown in the repo, shown and searched from the same file.** The `/help` pages and the assistant can't disagree. About 25 sections, so a plain loop over embeddings beats an index. I wrote the policies (free delivery, 14-day returns, a size chart) to match what checkout actually does: there's no delivery fee, so the bag now says "Free delivery across Nigeria".
- **Latency from here:** 13–20 s per message in the browser, against ~5 s in the eval. The model calls take 6–7 s of that. The rest is ~25 round trips from Lagos to Neon in Ohio (~0.37 s each): rate limit, identity, history, commits, the tools' queries. Deployed next to Neon, a round trip is ~2 ms, so I expect ~5–8 s. Phase 8 measures it there; if it's still slow, the first fix is fewer commits per turn.

## Decisions made while building
- **Model `claude-haiku-5-5`** (PLAN: Haiku while building, re-check in Phase 8). Override with `ASSISTANT_MODEL`. The cost table in `agent.py` covers Haiku, Sonnet 5.5 and Opus 5.5.
- **Settings:** `ASSISTANT_EFFORT=medium`, `ASSISTANT_DAILY_BUDGET_USD=1.0`, `ASSISTANT_MAX_TURNS=25`, `RATE_LIMIT_ASSISTANT_PER_MIN=10`.
- **Guests can chat and add to their bag;** order status needs sign-in.
- **The shopper's own amounts are allowed back** ("under 50k" → "under ₦50,000"). Echoing a budget isn't inventing a price.

## Bugs I made and caught this phase
1. **The price guard trusted any amount in a tool result**, including a planted one in a description (scenario 8, above).
2. **"₦1.5m" was read as ₦1m:** the decimal part wasn't captured. Caught by the guard's own unit test before anything ran.
3. **The scenario runner opened its HTTP client twice** and crashed on the first live run. A runner bug, not an app bug.
4. **Scenario 2 passed without testing anything.** The model rightly asked for a colour, and "only size L in the cart" is trivially true of an empty cart. A fourth turn now answers the colour question, and the check needs exactly one line, in L.
5. **The phone launcher overlapped the add-to-bag button.** Found in the browser screenshots; it's now a 48 px round button.

## Open for you
- **Read the 10 transcripts** in `evals/assistant/RESULTS.md` and tell me if any answer isn't good enough. That reading is the "done when".
- **Try it yourself:** start the API and the storefront (as in earlier phases) and click "Ask FlyFit". Each message costs about $0.0005. Please stop the API when you're done, so Neon can sleep.
- **Policy content:** I wrote shipping (free, 1–7 working days), returns (14 days, gele and headwear final sale) and a size chart. Change anything that doesn't fit the brand; the assistant follows the files.
- **From before:** the ~20 held-out search queries (`evals/search/heldout.txt`), the Paystack business name (a separate FlyFit business in Paystack), and whether to bulk-draft descriptions (~$0.13).
- **Seed quirk seen in the transcripts:** two products are both named "Ilaje Three-Piece Agbada" (different slugs). Harmless, but the assistant lists both. I can rename one in the seed if you like.

## 3 interview questions this phase lets me answer
1. *"How do you stop your agent from making up prices?"*: Prompt it, then don't trust it. Every ₦ amount in a reply is checked against the price fields the tools returned in that conversation; a failure is hidden, corrected once, then replaced. And the story: my first guard trusted any amount in a tool result, and an injected description beat it. The real-model eval found that; the fake-model tests didn't.
2. *"How do you keep a tool-using agent from leaking one customer's data to another?"*: Identity never comes from the model. The order tool has no customer parameter; it reads the signed-in customer from the session cookie. Another person's reference returns the same "not found" as a typo. Conversations are bound to the person who started them. Tested with three accounts, and in the eval with a real reference.
3. *"Why didn't you use streaming / a framework / the SDK's tool runner?"*: Every step needed the shop in control: per-call logging with cost, the spend cap, tools running with the request's identity, and a check on the whole reply before it's shown. A manual loop is ~150 lines. Streaming would show text before it's checked, and a shown price can't be taken back.
