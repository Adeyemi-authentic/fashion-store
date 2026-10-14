"""JSON log lines with a request id on every line (docs/ARCHITECTURE.md §5).

The id comes from the `X-Request-ID` header (the storefront proxy sets one) or is made here.
It lives in a contextvar, so every log call during that request, and every outbox job the
request writes, carries it. The worker puts a job's id back into the contextvar while it
runs the job, so one checkout can be followed from browser to email.
"""

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+)")
# Standard LogRecord attributes; anything else passed via `extra=` is logged as a field.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


def new_request_id(incoming: str | None = None) -> str:
    """Keep a well-formed incoming id (so the proxy's id carries through); otherwise make one.
    Anything else is replaced, so a client can't inject junk into our logs."""
    if incoming and _VALID_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


def mask_email(text: str) -> str:
    """ada@example.com -> a***@example.com. Logs shouldn't be a list of customer emails."""
    return _EMAIL.sub(r"\1***@\2", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": mask_email(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                line[key] = value
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    # Replace only our own handler (calling this twice is safe), never anyone else's, e.g.
    # pytest's log capture or an APM agent.
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_flyfit", False)]
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._flyfit = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Our middleware logs each request (with its id); uvicorn's own access line is a duplicate.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
