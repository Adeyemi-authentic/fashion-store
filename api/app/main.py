import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as _all_models  # noqa: F401  (registers every table for cross-module FKs)
from app.config import get_settings
from app.db import dispose_engine, get_session, get_sessionmaker
from app.modules.accounts.router import router as accounts_router
from app.modules.admin.router import router as admin_router
from app.modules.cart.router import router as cart_router
from app.modules.catalog.router import router as catalog_router
from app.modules.orders.router import router as orders_router
from app.modules.payments.router import router as payments_router
from app.platform.jobs import register_all
from app.platform.logging import configure_logging
from app.platform.middleware import RequestIdMiddleware
from app.platform.worker import run_forever


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    if settings.worker_in_api:
        register_all()
        task = asyncio.create_task(
            run_forever(
                get_sessionmaker(),
                stop,
                poll_seconds=settings.worker_poll_seconds,
                lease=timedelta(seconds=settings.worker_lease_seconds),
            )
        )
    yield
    if task is not None:
        stop.set()
        await asyncio.wait_for(task, timeout=settings.worker_lease_seconds)
    await dispose_engine()


def create_app() -> FastAPI:
    # Paths have no /api prefix: Next.js rewrites /api/* to here (ADR-005).
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title=f"{settings.brand_name} API", lifespan=lifespan)
    app.add_middleware(RequestIdMiddleware)
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
