"""The Claude client, shared by every module that calls the LLM.

None when ANTHROPIC_API_KEY is empty: callers must work without it (search then runs
without query understanding). Tests always run without a key and install fakes.
"""

from anthropic import AsyncAnthropic

from app.config import get_settings

_client: AsyncAnthropic | None = None


def get_llm() -> AsyncAnthropic | None:
    global _client
    key = get_settings().anthropic_api_key
    if not key:
        return None
    if _client is None:
        # One retry: a search waits on this call, so a slow API must give up quickly and
        # the search carries on without filters (callers add their own overall timeout).
        _client = AsyncAnthropic(api_key=key, max_retries=1, timeout=20.0)
    return _client
