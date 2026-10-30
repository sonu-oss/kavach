"""Who is signed in for the current request.

Set once per request by the session middleware; the Vault, contacts, profile and SOS
read it so every user only ever sees their own data, without each caller passing it on.
None means "no account" (tests, and data saved before accounts existed).
"""

from contextvars import ContextVar

_user: ContextVar[str | None] = ContextVar("kavach_user", default=None)


def current() -> str | None:
    return _user.get()


def set_current(user_id: str | None):
    return _user.set(user_id)


def reset(token) -> None:
    _user.reset(token)
