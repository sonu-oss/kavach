"""Accounts: Argon2id password hashes, server-side sessions, and a lockout after repeated failures.

- Passwords are hashed with Argon2id (argon2-cffi's RFC 9106 profile, random salt per hash);
  only the hash is stored, and old hashes are upgraded on the next login if the settings change.
- A session is a random 256-bit token in an HttpOnly cookie; only its SHA-256 is stored,
  so a copy of the database can't be used to sign in.
- The first account created takes over data saved before accounts existed.
"""

import hashlib
import re
import secrets
import time
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app import database
from app.sos import db

SESSION_TTL_S = 7 * 24 * 3600
MAX_FAILS = 5  # wrong passwords per username within LOCK_WINDOW_S before a lockout
LOCK_WINDOW_S = 15 * 60
MIN_PASSWORD = 10

_ph = PasswordHasher()  # Argon2id, RFC 9106 low-memory profile (t=3, m=64 MiB, p=4)
# Spent on unknown usernames too, so response time doesn't reveal which usernames exist.
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))
_USERNAME = re.compile(r"^[a-z0-9._-]{3,30}$")
_COMMON = {
    "password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop", "iloveyou123",
    "qwerty12345", "abcdefghij", "1111111111", "0000000000", "passw0rd123", "letmein123", "welcome123",
    "admin12345", "kavach1234", "sunshine123", "princess123", "football123", "monkey12345",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_sha256 TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    expires      REAL NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    username TEXT NOT NULL,
    at       REAL NOT NULL
);
"""


class AuthError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


database.schema("auth", _SCHEMA)


def _init() -> None:
    """Tables are created by app.database on first use; kept so callers read the same."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise(username: str) -> str:
    return (username or "").strip().lower()


def check_password_rules(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    if len(password) > 256:
        raise AuthError("That password is too long.")
    if password.lower() in _COMMON or password.lower() == username or len(set(password)) < 4:
        raise AuthError("That password is too easy to guess. Try a short sentence you'll remember.")


def signup(username: str, password: str) -> dict:
    _init()
    username = normalise(username)
    if not _USERNAME.match(username):
        raise AuthError("Username: 3–30 characters, using letters, numbers, dots, dashes or underscores.")
    check_password_rules(password, username)
    pw_hash = _ph.hash(password)
    uid = secrets.token_hex(8)
    with db.lock:
        if db.one("SELECT 1 AS x FROM users WHERE username=?", username):
            raise AuthError("That username is taken.", 409)
        first = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
        db.run("INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
               uid, username, pw_hash, _now())
    if first:
        _claim_legacy_data(uid)
    return {"id": uid, "username": username}


def _locked(username: str) -> bool:
    since = time.time() - LOCK_WINDOW_S
    db.run("DELETE FROM login_failures WHERE at < ?", since)
    return db.one("SELECT COUNT(*) AS n FROM login_failures WHERE username=?", username)["n"] >= MAX_FAILS


def login(username: str, password: str) -> tuple[dict, str]:
    """Returns (user, session token). Same message for a wrong username or password."""
    _init()
    username = normalise(username)
    if _locked(username):
        raise AuthError("Too many wrong attempts. Wait 15 minutes and try again.", 429)
    u = db.one("SELECT * FROM users WHERE username=?", username)
    try:
        _ph.verify(u["password_hash"] if u else _DUMMY_HASH, password)
        ok = u is not None
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok:
        with db.lock:
            db.run("INSERT INTO login_failures (username, at) VALUES (?, ?)", username, time.time())
        raise AuthError("Wrong username or password.", 401)
    with db.lock:
        db.run("DELETE FROM login_failures WHERE username=?", username)
        if _ph.check_needs_rehash(u["password_hash"]):
            db.run("UPDATE users SET password_hash=? WHERE id=?", _ph.hash(password), u["id"])
    return {"id": u["id"], "username": u["username"]}, new_session(u["id"])


def new_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.lock:
        db.run("DELETE FROM sessions WHERE expires < ?", time.time())
        db.run("INSERT INTO sessions (token_sha256, user_id, expires, created_at) VALUES (?, ?, ?, ?)",
               _digest(token), user_id, time.time() + SESSION_TTL_S, _now())
    return token


def user_for_session(token: str | None) -> dict | None:
    if not token or len(token) > 100:
        return None
    _init()
    row = db.one("SELECT u.id, u.username, s.expires FROM sessions s JOIN users u ON u.id=s.user_id"
                 " WHERE s.token_sha256=?", _digest(token))
    if not row or row["expires"] < time.time():
        return None
    return {"id": row["id"], "username": row["username"]}


def logout(token: str | None) -> None:
    if token:
        _init()
        with db.lock:
            db.run("DELETE FROM sessions WHERE token_sha256=?", _digest(token))


def _claim_legacy_data(uid: str) -> None:
    """The first account keeps what was saved before accounts existed: Vault, contacts, name, SOS history."""
    from app.vault import store  # late import: store imports the auth context

    store.claim_unowned(uid)
    with db.lock:
        db.run("UPDATE people SET owner=? WHERE kind='contact' AND owner IS NULL", uid)
        db.run("UPDATE sos SET user=? WHERE user='demo-user'", uid)
        for key in ("name", "invite_code"):
            db.run("UPDATE profile SET key=? WHERE key=? AND NOT EXISTS (SELECT 1 FROM profile WHERE key=?)",
                   f"{uid}:{key}", key, f"{uid}:{key}")
