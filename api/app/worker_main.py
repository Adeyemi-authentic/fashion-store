"""Run the outbox worker as its own process:  uv run python -m app.worker_main

(On the free tier it runs inside the API instead; see `worker_in_api` in config.)
SIGTERM / Ctrl-C stop it after the job in progress. SIGKILL loses nothing either: the job's
lease runs out and another worker takes it.
"""

import argparse
import asyncio
import importlib
import signal
from datetime import timedelta

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.platform.jobs import register_all
from app.platform.logging import configure_logging
from app.platform.worker import run_forever


async def main(extra_modules: list[str]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    register_all()
    for name in extra_modules:  # e.g. test-only handlers
        importlib.import_module(name)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run_forever(
            get_sessionmaker(),
            stop,
            poll_seconds=settings.worker_poll_seconds,
            lease=timedelta(seconds=settings.worker_lease_seconds),
        )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FlyFit outbox worker")
    parser.add_argument(
        "--import", dest="modules", action="append", default=[], help="extra handler module"
    )
    asyncio.run(main(parser.parse_args().modules))
