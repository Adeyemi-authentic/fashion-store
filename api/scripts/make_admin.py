"""Give an existing account the admin role (or take it away with --revoke).

There is deliberately no API route for this: the only way to become an admin is to have
shell access to a machine with the database URL.

    uv run python -m scripts.make_admin someone@example.com
    uv run python -m scripts.make_admin someone@example.com --revoke
"""

import argparse
import asyncio
import sys

from app.db import dispose_engine, get_sessionmaker
from app.modules.accounts.service import set_role


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("--revoke", action="store_true", help="make them a customer again")
    args = parser.parse_args()
    role = "customer" if args.revoke else "admin"
    async with get_sessionmaker()() as session:
        found = await set_role(session, args.email, role)
        await session.commit()
    await dispose_engine()
    if not found:
        print(f"No account with email {args.email}. Sign up on the site first.", file=sys.stderr)
        return 1
    print(f"{args.email} is now {'a customer' if args.revoke else 'an admin'}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
