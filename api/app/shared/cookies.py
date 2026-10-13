"""Every cookie the API sets goes through here, so all of them get the same flags.

First-party because the browser only ever talks to the storefront origin (ADR-005).
HttpOnly: page scripts can't read them. SameSite=Lax: other sites can't make the browser send
them on a POST, which (with JSON-only bodies) is the CSRF protection.
"""

from fastapi import Response

from app.config import get_settings

CART_COOKIE = "cart_token"
SESSION_COOKIE = "session"
THIRTY_DAYS = 60 * 60 * 24 * 30


def set_cookie(response: Response, name: str, value: str, max_age: int = THIRTY_DAYS) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(
        name, httponly=True, secure=get_settings().cookie_secure, samesite="lax", path="/"
    )
