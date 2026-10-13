"""Delete what the Playwright tests create: accounts e2e-*@example.com and products named
"E2E …". Runs after the browser tests, which use the real (Neon) database."""

import asyncio

from sqlalchemy import delete

from app.db import dispose_engine, get_sessionmaker
from app.modules.accounts.models import Customer
from app.modules.catalog.models import Product


async def main() -> None:
    async with get_sessionmaker()() as session:
        products = await session.execute(delete(Product).where(Product.name.like("E2E %")))
        customers = await session.execute(
            delete(Customer).where(Customer.email.like("e2e-%@example.com"))
        )
        await session.commit()
    await dispose_engine()
    print(f"removed {products.rowcount} products, {customers.rowcount} accounts")  # type: ignore[attr-defined]


if __name__ == "__main__":
    asyncio.run(main())
