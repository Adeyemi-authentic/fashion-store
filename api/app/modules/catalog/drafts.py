"""Description drafts in the database: save, list, approve, reject. Re-exported by
catalog.service. Nothing here commits; callers own the transaction."""

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.descriptions import DraftOutcome, Facts, rule_checks
from app.modules.catalog.models import DescriptionDraft, Product, Variant
from app.platform.outbox import enqueue

OPEN = ("needs_review", "failed_checks")


class DraftNotFound(LookupError):
    pass


class DraftNotApprovable(ValueError):
    pass


async def facts_for(session: AsyncSession, product_id: uuid.UUID) -> Facts | None:
    product = await session.get(Product, product_id)
    if product is None:
        return None
    rows = (
        await session.execute(
            select(Variant.colour, Variant.size).where(Variant.product_id == product_id)
        )
    ).all()
    return Facts(
        name=product.name,
        category=product.category,
        attributes=dict(product.attributes or {}),
        colours=sorted({c for c, _ in rows}),
        sizes=sorted({s for _, s in rows}),
    )


async def save_draft(
    session: AsyncSession, product_id: uuid.UUID, outcome: DraftOutcome, model: str
) -> uuid.UUID:
    """Store a new draft, replacing the product's open one (at most one open per product,
    enforced by a partial unique index)."""
    await session.execute(
        update(DescriptionDraft)
        .where(DescriptionDraft.product_id == product_id, DescriptionDraft.status.in_(OPEN))
        .values(status="superseded")
    )
    draft = DescriptionDraft(
        product_id=product_id,
        status="needs_review" if outcome.passed else "failed_checks",
        text=outcome.text,
        checks=[asdict(c) for c in outcome.checks],
        attempts=outcome.attempts,
        model=model,
        input_tokens=outcome.usage.input_tokens,
        output_tokens=outcome.usage.output_tokens,
    )
    session.add(draft)
    await session.flush()
    return draft.id


async def queue_drafts(session: AsyncSession, product_ids: list[uuid.UUID]) -> int:
    """One `draft_description` job per product. Spaced 3 s apart: the worker runs jobs one
    at a time, and a bulk run must not hold up order emails behind 100+ LLM calls."""
    now = await session.scalar(select(func.now()))
    assert now is not None
    for i, pid in enumerate(product_ids):
        await enqueue(
            session,
            "draft_description",
            {"product_id": str(pid)},
            run_after=now + timedelta(seconds=3 * i),
        )
    return len(product_ids)


async def products_with_template_descriptions(session: AsyncSession) -> list[uuid.UUID]:
    """Visible products still showing the seed's plain template, without an open draft."""
    has_open = select(DescriptionDraft.product_id).where(DescriptionDraft.status.in_(OPEN))
    rows = await session.execute(
        select(Product.id)
        .where(
            Product.status == "active",
            Product.description_source == "template",
            Product.id.not_in(has_open),
        )
        .order_by(Product.slug)
    )
    return list(rows.scalars())


@dataclass(frozen=True)
class DraftRow:
    id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_name: str
    current_description: str
    status: str
    text: str
    checks: list[dict[str, Any]]
    attempts: int
    model: str
    input_tokens: int
    output_tokens: int
    created_at: datetime


async def list_drafts(
    session: AsyncSession, status: str | None, limit: int = 100
) -> list[DraftRow]:
    stmt = (
        select(DescriptionDraft, Product.slug, Product.name, Product.description)
        .join(Product, Product.id == DescriptionDraft.product_id)
        .order_by(DescriptionDraft.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(DescriptionDraft.status == status)
    return [
        DraftRow(
            id=d.id,
            product_id=d.product_id,
            product_slug=slug,
            product_name=name,
            current_description=desc,
            status=d.status,
            text=d.text,
            checks=d.checks,
            attempts=d.attempts,
            model=d.model,
            input_tokens=d.input_tokens,
            output_tokens=d.output_tokens,
            created_at=d.created_at,
        )
        for d, slug, name, desc in await session.execute(stmt)
    ]


async def approve_draft(
    session: AsyncSession, draft_id: uuid.UUID, admin_id: uuid.UUID, edited_text: str | None
) -> str:
    """Make the draft the live description. An admin may edit the text first; the edit is
    re-checked by the rule checks (the claim check needs an LLM call, and the admin is the
    reviewer). A draft that failed its checks can only go live edited into shape.
    Returns the product slug. The description change queues an embedding refresh."""
    draft = await session.get(DescriptionDraft, draft_id, with_for_update=True)
    if draft is None:
        raise DraftNotFound(draft_id)
    if draft.status not in OPEN:
        raise DraftNotApprovable(f"this draft is already {draft.status}")
    text = (edited_text or draft.text).strip()
    if edited_text is not None or draft.status == "failed_checks":
        facts = await facts_for(session, draft.product_id)
        assert facts is not None
        failed = [c for c in rule_checks(text, facts) if not c.passed]
        if failed:
            raise DraftNotApprovable(
                "Fails: " + "; ".join(f"{c.name} ({c.detail})" for c in failed)
            )
    product = await session.get(Product, draft.product_id, with_for_update=True)
    assert product is not None
    product.description = text
    product.description_source = "ai_approved"
    product.updated_at = func.now()  # type: ignore[assignment]
    draft.status, draft.text = "approved", text
    draft.reviewed_by, draft.reviewed_at = admin_id, func.now()  # type: ignore[assignment]
    await enqueue(session, "embed_product", {"product_id": str(product.id)})
    await session.flush()
    return product.slug


async def reject_draft(session: AsyncSession, draft_id: uuid.UUID, admin_id: uuid.UUID) -> None:
    draft = await session.get(DescriptionDraft, draft_id, with_for_update=True)
    if draft is None:
        raise DraftNotFound(draft_id)
    if draft.status not in OPEN:
        raise DraftNotApprovable(f"this draft is already {draft.status}")
    draft.status = "rejected"
    draft.reviewed_by, draft.reviewed_at = admin_id, func.now()  # type: ignore[assignment]
    await session.flush()
