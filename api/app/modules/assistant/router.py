import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts.deps import OptionalCustomer
from app.modules.assistant import agent, policies
from app.modules.cart.deps import Owner
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ProductCard
from app.platform.ratelimit import assistant_limit
from app.shared.cookies import ASSISTANT_COOKIE, CART_COOKIE, clear_cookie, set_cookie

router = APIRouter(tags=["assistant"])

Session = Annotated[AsyncSession, Depends(get_session)]
ConversationCookie = Annotated[str | None, Cookie(alias=ASSISTANT_COOKIE)]
ONE_DAY = 60 * 60 * 24

UNAVAILABLE = (
    "The shopping assistant is resting for now. You can still search the shop with the "
    "search box at the top of the page."
)


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=agent.MAX_MESSAGE_CHARS)


class ShownMessageOut(BaseModel):
    role: str
    text: str
    products: list[ProductCard]


class ReplyOut(BaseModel):
    reply: ShownMessageOut
    cart_changed: bool


class TranscriptOut(BaseModel):
    messages: list[ShownMessageOut]


def _session_id(cookie: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(cookie) if cookie else None
    except ValueError:
        return None


@router.post(
    "/assistant/messages", response_model=ReplyOut, dependencies=[Depends(assistant_limit)]
)
async def send_message(
    body: MessageIn,
    response: Response,
    session: Session,
    customer: OptionalCustomer,
    owner: Owner,
    conversation: ConversationCookie = None,
) -> ReplyOut:
    customer_id = customer.id if customer else None
    row = await agent.find_session(session, _session_id(conversation), customer_id)
    try:
        reply = await agent.respond(session, row, customer_id, owner, body.message.strip())
    except agent.AssistantUnavailable as exc:
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc
    set_cookie(response, ASSISTANT_COOKIE, str(reply.session_id), max_age=ONE_DAY)
    if reply.new_cart_token:
        set_cookie(response, CART_COOKIE, reply.new_cart_token)
    return ReplyOut(
        reply=ShownMessageOut(role="assistant", text=reply.text, products=reply.products),
        cart_changed=reply.cart_changed,
    )


@router.get("/assistant/messages", response_model=TranscriptOut)
async def get_transcript(
    session: Session, customer: OptionalCustomer, conversation: ConversationCookie = None
) -> TranscriptOut:
    row = await agent.find_session(
        session, _session_id(conversation), customer.id if customer else None
    )
    if row is None:
        return TranscriptOut(messages=[])
    shown = await agent.transcript(session, row.id)
    slugs = list(dict.fromkeys(s for m in shown for s in m.products))
    cards = {c.slug: c for c in await catalog.cards_by_slug(session, slugs)}
    return TranscriptOut(
        messages=[
            ShownMessageOut(
                role=m.role,
                text=m.text,
                products=[cards[s] for s in m.products if s in cards],
            )
            for m in shown
        ]
    )


@router.delete("/assistant/messages", status_code=204)
async def new_conversation(response: Response) -> None:
    """Start over. The old conversation stays in the database for evals."""
    clear_cookie(response, ASSISTANT_COOKIE)


@router.get("/policies/{slug}", response_class=PlainTextResponse)
async def policy_page(slug: str) -> str:
    """The markdown of one policy page; the storefront renders it at /help/<slug>."""
    page = policies.pages().get(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return page.markdown
