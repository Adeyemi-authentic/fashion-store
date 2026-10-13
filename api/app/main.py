from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as _all_models  # noqa: F401  (registers every table for cross-module FKs)
from app.config import get_settings
from app.db import dispose_engine, get_session
from app.modules.accounts.router import router as accounts_router
from app.modules.admin.router import router as admin_router
from app.modules.cart.router import router as cart_router
from app.modules.catalog.router import router as catalog_router
from app.modules.orders.router import router as orders_router
from app.modules.payments.router import router as payments_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    # Paths have no /api prefix: Next.js rewrites /api/* to here (ADR-005).
    app = FastAPI(title=f"{get_settings().brand_name} API", lifespan=lifespan)
    app.include_router(catalog_router)
    app.include_router(cart_router)
    app.include_router(accounts_router)
    app.include_router(admin_router)
    app.include_router(orders_router)
    app.include_router(payments_router)

    @app.get("/health", tags=["ops"])
    async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
