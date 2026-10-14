"""Sending email. Every send carries an idempotency key (the outbox job id), because jobs run
at least once: a worker killed after sending but before marking the job done will send again
on retry, and the key makes that second send a no-op instead of a second email.

- Resend (when RESEND_API_KEY is set): the key goes in Resend's `Idempotency-Key` header,
  which Resend honours for 24 hours.
- Otherwise (dev, no account needed): the email is written to `.emails/<key>.json`, and an
  existing file means "already sent", the same rule.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.config import get_settings
from app.platform.logging import mask_email

log = logging.getLogger("app.email")


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


class EmailSender(Protocol):
    async def send(self, email: Email, idempotency_key: str) -> None: ...


class ResendSender:
    def __init__(
        self, api_key: str, sender: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._sender = sender
        self._client = httpx.AsyncClient(
            base_url="https://api.resend.com",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
        )

    async def send(self, email: Email, idempotency_key: str) -> None:
        resp = await self._client.post(
            "/emails",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "from": self._sender,
                "to": [email.to],
                "subject": email.subject,
                "text": email.text,
                "html": email.html,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Resend {resp.status_code}: {resp.text[:200]}")
        log.info(
            "email sent via resend to %s", email.to, extra={"idempotency_key": idempotency_key}
        )


class FileSender:
    def __init__(self, folder: str) -> None:
        self._folder = Path(folder)

    async def send(self, email: Email, idempotency_key: str) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._folder / f"{re.sub(r'[^A-Za-z0-9_-]', '_', idempotency_key)}.json"
        if path.exists():
            log.info(
                "email already sent, skipped (same idempotency key)",
                extra={"idempotency_key": idempotency_key},
            )
            return
        path.write_text(json.dumps(asdict(email), indent=2))
        log.info(
            "email written to %s for %s",
            path.name,
            mask_email(email.to),
            extra={"idempotency_key": idempotency_key},
        )


_sender: EmailSender | None = None


def get_sender() -> EmailSender:
    global _sender
    if _sender is None:
        s = get_settings()
        _sender = (
            ResendSender(s.resend_api_key, s.email_from)
            if s.resend_api_key
            else FileSender(s.email_outbox_dir)
        )
    return _sender
