"""Search job handlers."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.search.embeddings import refresh_embeddings
from app.platform.worker import Job, PermanentJobError, handler


@handler("embed_product")
async def embed_product(session: AsyncSession, job: Job) -> None:
    """Queued with every product change that alters its text. At least once is fine: a
    second run finds the embedding up to date and makes no API call."""
    try:
        product_id = uuid.UUID(str(job.payload["product_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"bad payload: {job.payload}") from exc
    await refresh_embeddings(session, [product_id])
