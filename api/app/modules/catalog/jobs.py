"""Catalog job handlers."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.catalog.descriptions import write_description
from app.modules.catalog.drafts import facts_for, save_draft
from app.platform.llm import get_llm
from app.platform.worker import Job, PermanentJobError, handler


@handler("draft_description")
async def draft_description(session: AsyncSession, job: Job) -> None:
    """Draft a description for one product and store it for review. At least once is fine:
    a second run replaces the open draft with a new one (and costs a few more tokens)."""
    client = get_llm()
    if client is None:
        raise PermanentJobError("ANTHROPIC_API_KEY is not set")
    try:
        product_id = uuid.UUID(str(job.payload["product_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"bad payload: {job.payload}") from exc
    facts = await facts_for(session, product_id)
    if facts is None:
        raise PermanentJobError(f"product {product_id} not found")
    # End the read transaction before the LLM calls (several seconds), so the job doesn't
    # hold a database connection idle in a transaction. Nothing was written yet.
    await session.rollback()
    model = get_settings().llm_model
    outcome = await write_description(client, model, facts)
    await save_draft(session, product_id, outcome, model)
