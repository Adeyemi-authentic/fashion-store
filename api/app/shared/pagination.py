"""Opaque keyset cursors.

A cursor encodes the sort it was made for plus the last row's sort value and id. The next
page asks for rows strictly after that (value, id) pair, so pages stay correct when rows are
inserted or deleted between requests (unlike OFFSET), and the query uses the index instead of
skipping rows.
"""

import base64
import binascii
import json
from typing import Any


class InvalidCursor(ValueError):
    pass


def encode_cursor(sort: str, value: Any, row_id: str) -> str:
    raw = json.dumps({"s": sort, "v": value, "id": row_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, expected_sort: str) -> tuple[Any, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        sort, value, row_id = data["s"], data["v"], data["id"]
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor("cursor is not valid") from exc
    if sort != expected_sort:
        raise InvalidCursor("cursor was made for a different sort order")
    return value, row_id
