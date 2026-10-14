"""Platform job handlers, and the one import that registers every module's handlers."""

import html

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.platform.email import Email, get_sender
from app.platform.worker import Job, handler


@handler("admin_alert")
async def admin_alert(session: AsyncSession, job: Job) -> None:
    s = get_settings()
    reason = str(job.payload.get("reason", "(no reason given)"))
    details = {k: v for k, v in job.payload.items() if k != "reason"}
    await get_sender().send(
        Email(
            to=s.admin_email,
            subject=f"[{s.brand_name} admin] {reason[:80]}",
            text=f"{reason}\n\n{details}",
            html=f"<p>{html.escape(reason)}</p><pre>{html.escape(str(details))}</pre>",
        ),
        idempotency_key=f"job-{job.id}",
    )


def register_all() -> None:
    """Import every module that defines handlers (decorators register on import)."""
    from app.modules.orders import jobs as _orders  # noqa: F401
