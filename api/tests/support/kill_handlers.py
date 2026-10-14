"""Handlers for the SIGKILL test, loaded into a real worker process with --import.

The job's side effect is a file named after its idempotency key (like an email provider
that drops a repeat key); every call is also appended to calls.log so the test can see the
job really ran twice. After the side effect it sleeps, giving the test time to kill the
worker before the job is marked done."""

import asyncio
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.worker import Job, handler

OUT = Path(os.environ["KILL_TEST_DIR"])


@handler("test_job")
async def side_effect_then_pause(session: AsyncSession, job: Job) -> None:
    with (OUT / "calls.log").open("a") as f:
        f.write(f"job-{job.id}\n")
    effect = OUT / f"job-{job.id}.done"
    if not effect.exists():  # idempotent: a repeat with the same key does nothing
        effect.write_text("sent")
    await asyncio.sleep(float(job.payload.get("pause", 0)))
