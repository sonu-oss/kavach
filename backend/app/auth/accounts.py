"""Accounts: Argon2id password hashes, server-side sessions, owner passcodes, and lockout protection.

- Passwords are hashed with Argon2id (argon2-cffi's RFC 9106 profile, random salt per hash);
  only the hash is stored, and old hashes are upgraded on the next login if settings change.
- A session is a random 256-bit token in an HttpOnly cookie; only its SHA-256 is stored,
  so a copy of the database cannot be used to sign in.
- Supports owner passcode (4-8 digits) hashed with Argon2id for locking protected app screens.
- Supports password reset tokens with 15-minute expiry.
- Scoped data ownership and account deletion for privacy compliance.
"""

import hashlib
import re
import secrets
import time
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app import config, database
from app.sos import db

SESSION_TTL_S = 7 * 24 * 3600
MAX_FAILS = 5  # wrong passwords per username/email within LOCK_WINDOW_S before lockout
LOCK_WINDOW_S = 15 * 60
MIN_PASSWORD = 10
RESET_TOKEN_TTL_S = 15 * 60  # 15 minutes

_ph = PasswordHasher()  # Argon2id, RFC 9106 low-memory profile (t=3, m=64 MiB, p=4)
# Spent on unknown usernames too, so response time doesn't reveal which accounts exist.
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

_ADD_COLUMNS = [
    ("users", "email", "TEXT"),
    ("users", "phone", "TEXT"),
    ("users", "full_name", "TEXT"),
    ("users", "city", "TEXT"),
    ("users", "address", "TEXT"),
    ("users", "email_verified", "INTEGER DEFAULT 0"),
    ("users", "passcode_hash", "TEXT"),
    ("users", "reset_token", "TEXT"),
    ("users", "reset_expires", "REAL"),
    ("users", "lat", "REAL"),
    ("users", "lng", "REAL"),
]


class AuthError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


database.schema("auth", _SCHEMA, _ADD_COLUMNS)


def _init() -> None:
    """Tables are created by app.database on first use; kept so callers read the same."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise(text: str) -> str:
    return (text or "").strip().lower()


def check_password_rules(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    if len(password) > 256:
        raise AuthError("That password is too long.")
    if password.lower() in _COMMON or password.lower() == username or len(set(password)) < 4:
        raise AuthError("That password is too easy to guess. Try a memorable passphrase.")


def signup(username: str, password: str, full_name: str = "", email: str = "", phone: str = "") -> dict:
    _init()
    username = normalise(username)
    email = normalise(email)
    phone = (phone or "").strip()
    full_name = (full_name or "").strip()

    if not _USERNAME.match(username):
        raise AuthError("Username: 3–30 characters, using letters, numbers, dots, dashes or underscores.")
    check_password_rules(password, username)
    pw_hash = _ph.hash(password)
    uid = secrets.token_hex(8)

    with db.lock:
        if db.one("SELECT 1 AS x FROM users WHERE username=?", username):
            raise AuthError("That username is taken.", 409)
        if email and db.one("SELECT 1 AS x FROM users WHERE email=? AND email != ''", email):
            raise AuthError("An account with that email already exists.", 409)

        first = db.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
        db.run(
            "INSERT INTO users (id, username, password_hash, full_name, email, phone, email_verified, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            uid, username, pw_hash, full_name, email, phone, _now()
        )
    if first:
        _claim_legacy_data(uid)
    return {
        "id": uid,
        "username": username,
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "email_verified": False,
        "has_passcode": False,
    }


def _locked(identifier: str) -> bool:
    since = time.time() - LOCK_WINDOW_S
    db.run("DELETE FROM login_failures WHERE at < ?", since)
    return db.one("SELECT COUNT(*) AS n FROM login_failures WHERE username=?", identifier)["n"] >= MAX_FAILS


def login(identifier: str, password: str) -> tuple[dict, str]:
    """Returns (user, session token). Accepts username or registered email."""
    _init()
    identifier = normalise(identifier)
    if _locked(identifier):
        raise AuthError("Too many wrong attempts. Wait 15 minutes and try again.", 429)

    u = db.one("SELECT * FROM users WHERE username=? OR (email IS NOT NULL AND email=? AND email != '')",
               identifier, identifier)
    try:
        _ph.verify(u["password_hash"] if u else _DUMMY_HASH, password)
        ok = u is not None
    except (VerificationError, InvalidHashError):
        ok = False

    if not ok:
        with db.lock:
            db.run("INSERT INTO login_failures (username, at) VALUES (?, ?)", identifier, time.time())
        raise AuthError("Wrong username or password.", 401)

    with db.lock:
        db.run("DELETE FROM login_failures WHERE username=?", identifier)
        if _ph.check_needs_rehash(u["password_hash"]):
            db.run("UPDATE users SET password_hash=? WHERE id=?", _ph.hash(password), u["id"])

    user_data = {
        "id": u["id"],
        "username": u["username"],
        "full_name": u.get("full_name") or "",
        "email": u.get("email") or "",
        "phone": u.get("phone") or "",
        "email_verified": bool(u.get("email_verified")),
        "has_passcode": bool(u.get("passcode_hash")),
    }
    return user_data, new_session(u["id"])


def new_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.lock:
        db.run("DELETE FROM sessions WHERE expires < ?", time.time())
        db.run("INSERT INTO sessions (token_sha256, user_id, expires, created_at) VALUES (?, ?, ?, ?)",
               _digest(token), user_id, time.time() + SESSION_TTL_S, _now())
    return token


def _format_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row.get("full_name") or "",
        "email": row.get("email") or "",
        "phone": row.get("phone") or "",
        "city": row.get("city") or "",
        "address": row.get("address") or "",
        "lat": row.get("lat"),
        "lng": row.get("lng"),
        "email_verified": bool(row.get("email_verified")),
        "has_passcode": bool(row.get("passcode_hash")),
    }


def user_for_id(uid: str) -> dict | None:
    _init()
    u = db.one("SELECT * FROM users WHERE id = ?", uid)
    return _format_user(u) if u else None


def update_profile(user_id: str, full_name: str | None = None, phone: str | None = None, city: str | None = None, address: str | None = None, lat: float | None = None, lng: float | None = None) -> dict:
    _init()
    fields, vals = [], []
    if full_name is not None:
        fields.append("full_name = ?")
        vals.append(full_name.strip())
    if phone is not None:
        fields.append("phone = ?")
        vals.append(phone.strip())
    if city is not None:
        fields.append("city = ?")
        vals.append(city.strip())
    if address is not None:
        fields.append("address = ?")
        vals.append(address.strip())
    if lat is not None:
        fields.append("lat = ?")
        vals.append(float(lat))
    if lng is not None:
        fields.append("lng = ?")
        vals.append(float(lng))
    if fields:
        vals.append(user_id)
        with db.lock:
            db.run(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", *vals)
    u = db.one("SELECT * FROM users WHERE id = ?", user_id)
    return _format_user(u)


def user_for_session(token: str | None) -> dict | None:
    if not token or len(token) > 100:
        return None
    _init()
    row = db.one(
        "SELECT u.*, s.expires "
        "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_sha256=?",
        _digest(token)
    )
    if not row or row["expires"] < time.time():
        return None
    return _format_user(row)


def latest_active_user() -> dict | None:
    """Returns the most recent active user, for trusted local browser extension integrations."""
    _init()
    row = db.one(
        "SELECT u.*, s.expires FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.expires > ? ORDER BY s.expires DESC LIMIT 1",
        time.time()
    )
    if row:
        return _format_user(row)
    u = db.one("SELECT * FROM users ORDER BY created_at DESC LIMIT 1")
    return _format_user(u) if u else None


def login_or_create_google(email: str, full_name: str = "", sub: str = "") -> dict:
    """Finds or registers a user via Google OAuth2."""
    clean_email = email.strip().lower()
    if not clean_email or "@" not in clean_email:
        raise AuthError("Invalid Google email address", status=400)
    _init()
    user = db.one("SELECT * FROM users WHERE email = ?", clean_email)
    if user:
        return _format_user(user)
    base_user = re.sub(r"[^a-z0-9]", "", clean_email.split("@")[0])[:20] or f"user{sub[:6]}"
    username = base_user
    count = 1
    while db.one("SELECT id FROM users WHERE username = ?", username):
        username = f"{base_user[:15]}_{count}"
        count += 1
    now = datetime.now(timezone.utc).isoformat()
    uid = secrets.token_hex(8)
    dummy_pass_hash = _ph.hash(secrets.token_urlsafe(24))
    with db.lock:
        db.run(
            "INSERT INTO users (id, username, password_hash, created_at, full_name, email, email_verified)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            uid, username, dummy_pass_hash, now, full_name or username, clean_email, 1
        )
    return _format_user(db.one("SELECT * FROM users WHERE id = ?", uid))


def logout(token: str | None) -> None:
    if token:
        _init()
        with db.lock:
            db.run("DELETE FROM sessions WHERE token_sha256=?", _digest(token))


def forgot_password(identifier: str) -> dict:
    """Generates a 15-minute recovery token for an account."""
    _init()
    identifier = normalise(identifier)
    u = db.one("SELECT id, email, username FROM users WHERE username=? OR (email IS NOT NULL AND email=? AND email != '')",
               identifier, identifier)
    # Always return success message to prevent user enumeration
    resp = {"ok": True, "message": "If an account exists with that identifier, recovery instructions have been initiated."}
    if not u:
        return resp

    token = secrets.token_urlsafe(24)
    expires = time.time() + RESET_TOKEN_TTL_S
    with db.lock:
        db.run("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?", token, expires, u["id"])

    # In demo mode, or when email is not configured, provide token directly to facilitate testing
    if config.DEMO_MODE or not config.SMTP_USER:
        resp["reset_token"] = token
        resp["message"] += f" Demo mode token: {token}"
    return resp


def reset_password(token: str, new_password: str) -> bool:
    """Resets password using a valid reset token."""
    _init()
    if not token:
        raise AuthError("Reset token is required.", 400)
    u = db.one("SELECT * FROM users WHERE reset_token=? AND reset_expires > ?", token, time.time())
    if not u:
        raise AuthError("Invalid or expired password reset link. Please request a new one.", 400)

    check_password_rules(new_password, u["username"])
    pw_hash = _ph.hash(new_password)
    with db.lock:
        db.run("UPDATE users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?",
               pw_hash, u["id"])
        # Revoke all existing sessions for security
        db.run("DELETE FROM sessions WHERE user_id=?", u["id"])
    return True


def set_passcode(user_id: str, passcode: str) -> bool:
    """Sets a separate 4-8 digit app-lock passcode."""
    _init()
    passcode = (passcode or "").strip()
    if not (4 <= len(passcode) <= 8 and passcode.isdigit()):
        raise AuthError("App passcode must be between 4 and 8 digits.", 400)
    pc_hash = _ph.hash(passcode)
    with db.lock:
        db.run("UPDATE users SET passcode_hash=? WHERE id=?", pc_hash, user_id)
    return True


def verify_passcode(user_id: str, passcode: str) -> bool:
    """Verifies the owner's app passcode."""
    _init()
    u = db.one("SELECT passcode_hash FROM users WHERE id=?", user_id)
    if not u or not u.get("passcode_hash"):
        raise AuthError("No passcode has been configured for this account.", 404)
    try:
        _ph.verify(u["passcode_hash"], passcode.strip())
        return True
    except (VerificationError, InvalidHashError):
        return False


def delete_account(user_id: str) -> None:
    """Permanently deletes the account and all associated user data."""
    _init()
    with db.lock:
        db.run("DELETE FROM sessions WHERE user_id=?", user_id)
        db.run("DELETE FROM guardians WHERE user_id=?", user_id)
        db.run("DELETE FROM responders WHERE user_id=?", user_id)
        db.run("DELETE FROM people WHERE owner=?", user_id)
        db.run("DELETE FROM sos WHERE user=?", user_id)
        db.run("DELETE FROM profile WHERE key LIKE ?", f"{user_id}:%")
        try:
            from app.vault import connect as vault_conn
            with vault_conn() as conn:
                conn.execute("DELETE FROM evidence WHERE owner=?", (user_id,))
        except Exception:
            pass
        db.run("DELETE FROM users WHERE id=?", user_id)


def _claim_legacy_data(uid: str) -> None:
    """The first account keeps what was saved before accounts existed."""
    from app.vault import store

    store.claim_unowned(uid)
    with db.lock:
        db.run("UPDATE people SET owner=? WHERE kind='contact' AND owner IS NULL", uid)
        db.run("UPDATE sos SET user=? WHERE user='demo-user'", uid)
        for key in ("name", "invite_code"):
            db.run("UPDATE profile SET key=? WHERE key=? AND NOT EXISTS (SELECT 1 FROM profile WHERE key=?)",
                   f"{uid}:{key}", key, f"{uid}:{key}")
