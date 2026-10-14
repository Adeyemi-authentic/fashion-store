"""PLAN.md Phase 5 'done when': killing the worker mid-job loses no jobs and runs none
twice where it matters. A real worker process is SIGKILLed after a job's side effect and
before its commit; a second worker finishes the work."""

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.models import OutboxJob
from app.platform.outbox import enqueue
from tests.conftest import API_DIR


def start_worker(tmp: Path) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,  # DATABASE_URL points at the test database
        "KILL_TEST_DIR": str(tmp),
        "WORKER_LEASE_SECONDS": "3",
        "WORKER_POLL_SECONDS": "0.1",
        "LOG_LEVEL": "WARNING",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker_main", "--import", "tests.support.kill_handlers"],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


async def wait_for(check, seconds: float = 30) -> None:  # type: ignore[no-untyped-def]
    for _ in range(int(seconds / 0.1)):
        if await check():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("timed out")


async def test_sigkill_mid_job_loses_nothing_and_sends_once(
    session: AsyncSession, tmp_path: Path
) -> None:
    await session.execute(delete(OutboxJob))
    await enqueue(session, "test_job", {"pause": 30})  # A will die during this one
    await enqueue(session, "test_job", {"pause": 0})
    await enqueue(session, "test_job", {"pause": 0})
    await session.commit()
    ids = list(
        (
            await session.execute(
                select(OutboxJob.id).where(OutboxJob.type == "test_job").order_by(OutboxJob.id)
            )
        ).scalars()
    )
    first = ids[0]

    worker_a = start_worker(tmp_path)
    try:
        # A has done the side effect of the first job and is now inside its pause.
        await wait_for(lambda: asyncio.sleep(0, (tmp_path / f"job-{first}.done").exists()))
        worker_a.send_signal(signal.SIGKILL)  # no cleanup, no commit
        worker_a.wait(timeout=10)
    finally:
        if worker_a.poll() is None:
            worker_a.kill()

    session.expire_all()
    stuck = await session.get(OutboxJob, first)
    assert stuck is not None and stuck.status == "running"  # not lost: still claimed, lease ticking

    # Make the retried run quick (the payload's pause was only to give us time to kill A).
    stuck.payload = {**stuck.payload, "pause": 0}
    await session.commit()

    worker_b = start_worker(tmp_path)
    try:

        async def all_done() -> bool:
            session.expire_all()
            statuses = (
                (await session.execute(select(OutboxJob.status).where(OutboxJob.id.in_(ids))))
                .scalars()
                .all()
            )
            return set(statuses) == {"done"}

        await wait_for(all_done)
    finally:
        worker_b.send_signal(signal.SIGTERM)  # graceful stop
        assert worker_b.wait(timeout=15) == 0, (
            worker_b.stderr.read().decode()[-1500:] if worker_b.stderr else ""
        )

    calls = (tmp_path / "calls.log").read_text().split()  # noqa: ASYNC240 - test, tiny file
    assert calls.count(f"job-{first}") == 2  # it really ran twice (at least once)...
    effects = sorted(p.name for p in tmp_path.glob("*.done"))  # noqa: ASYNC240
    assert effects == sorted(f"job-{i}.done" for i in ids)  # ...but every effect happened once
    attempts = (
        await session.execute(select(OutboxJob.attempts).where(OutboxJob.id == first))
    ).scalar_one()
    assert attempts == 2
    await session.execute(delete(OutboxJob))
    await session.commit()
