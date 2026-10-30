"""Her trusted contacts and the registered responders.

Contacts are trusted by her, so they see her exact location straight away.
Responders are volunteers: an admin must verify them, they can opt out, and they
only ever see an approximate area until they accept an alert.
"""

import secrets
from datetime import datetime, timezone

from app.auth import context
from app.sos import db


class PeopleError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public(p: dict) -> dict:
    """What the browser may see: never the Telegram chat id."""
    return {
        "id": p["id"], "kind": p["kind"], "name": p["name"], "phone": p["phone"] or "", "email": p["email"] or "",
        "telegram_linked": p["telegram_chat_id"] is not None, "link_code": p["link_code"],
        "verified": bool(p["verified"]), "opted_in": bool(p["opted_in"]),
        "has_location": p["lat"] is not None, "lat": p["lat"], "lng": p["lng"], "location_at": p["location_at"],
    }


def create(kind: str, name: str, phone: str = "", email: str = "", lat=None, lng=None, owner=None) -> dict:
    if kind not in ("contact", "responder"):
        raise PeopleError("kind must be contact or responder")
    if not name.strip():
        raise PeopleError("name is required")
    _check_location(lat, lng)
    pid = secrets.token_hex(5)
    # Contacts belong to the signed-in account; responders are a shared network.
    owner = (owner or context.current()) if kind == "contact" else None
    with db.lock:
        db.run(
            "INSERT INTO people (id, kind, name, phone, email, link_code, verified, opted_in, lat, lng, location_at,"
            " created_at, owner) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            pid, kind, name.strip(), phone.strip(), email.strip(), secrets.token_urlsafe(6),
            1 if kind == "contact" else 0, lat, lng, _now() if lat is not None else None, _now(), owner,
        )
    return public(get(pid))


def get(pid: str) -> dict:
    p = db.one("SELECT * FROM people WHERE id=?", pid)
    if not p:
        raise PeopleError("person not found", 404)
    return p


def list_kind(kind: str) -> list[dict]:
    if kind == "contact":
        rows = db.rows("SELECT * FROM people WHERE kind='contact' AND owner IS ? ORDER BY created_at", context.current())
    else:
        rows = db.rows("SELECT * FROM people WHERE kind=? ORDER BY created_at", kind)
    return [public(p) for p in rows]


def owns(p: dict) -> bool:
    """A contact can only be seen or changed by the account it belongs to."""
    return p["kind"] != "contact" or p["owner"] == context.current()


def linked_contacts(owner=None) -> list[dict]:
    """Trusted contacts who have connected Telegram (raw rows: includes the chat id)."""
    return db.rows("SELECT * FROM people WHERE kind='contact' AND telegram_chat_id IS NOT NULL AND owner IS ?"
                   " ORDER BY created_at", owner or context.current())


def update(pid: str, **fields) -> dict:
    get(pid)
    allowed = {"name", "phone", "email", "opted_in", "lat", "lng"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "lat" in sets or "lng" in sets:
        _check_location(sets.get("lat"), sets.get("lng"))
        sets["location_at"] = _now()
    if "opted_in" in sets:
        sets["opted_in"] = int(bool(sets["opted_in"]))
    if sets:
        cols = ", ".join(f"{k}=?" for k in sets)
        with db.lock:
            db.run(f"UPDATE people SET {cols} WHERE id=?", *sets.values(), pid)
    return public(get(pid))


def verify(pid: str, verified: bool = True) -> dict:
    p = get(pid)
    if p["kind"] != "responder":
        raise PeopleError("only responders need verifying")
    with db.lock:
        db.run("UPDATE people SET verified=? WHERE id=?", int(verified), pid)
    return public(get(pid))


def delete(pid: str) -> None:
    get(pid)
    with db.lock:
        db.run("DELETE FROM people WHERE id=?", pid)
        db.run("DELETE FROM alerts WHERE person_id=?", pid)


def link_telegram(code: str, chat_id: int) -> dict | None:
    """Called when someone taps Start on the bot with their personal code."""
    p = db.one("SELECT * FROM people WHERE link_code=?", code)
    if not p:
        return None
    with db.lock:
        db.run("UPDATE people SET telegram_chat_id=? WHERE id=?", chat_id, p["id"])
    return get(p["id"])


def by_chat(chat_id: int) -> list[dict]:
    return db.rows("SELECT * FROM people WHERE telegram_chat_id=?", chat_id)


INVITE_PREFIX = "join_"  # /start join_<code>: the shared link that adds whoever opens it as a contact


def _key(key: str, owner) -> str:
    """Profile keys are per account ("<user id>:name"); plain keys are from before accounts."""
    return f"{owner}:{key}" if owner else key


def invite_code(reset: bool = False, owner=None) -> str:
    """One shared code for this account's trusted contacts. Reset it to stop an old link working."""
    key = _key("invite_code", owner or context.current())
    with db.lock:
        r = db.one("SELECT value FROM profile WHERE key=?", key)
        if r and not reset:
            return r["value"]
        code = secrets.token_urlsafe(9)
        db.run("INSERT INTO profile (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               key, code)
        return code


def join_by_invite(code: str, chat_id: int, tg_name: str) -> tuple[dict | None, bool]:
    """Someone opened a shared invite link and tapped Start. Returns (contact, newly_added);
    (None, False) when the link is wrong or was reset. The code says whose contact they become."""
    if not code:
        return None, False
    r = db.one("SELECT key FROM profile WHERE value=? AND (key='invite_code' OR key LIKE '%:invite_code')", code)
    if not r:
        return None, False
    owner = r["key"].split(":", 1)[0] if ":" in r["key"] else None
    existing = db.one("SELECT * FROM people WHERE kind='contact' AND telegram_chat_id=? AND owner IS ?", chat_id, owner)
    if existing:
        return get(existing["id"]), False
    p = create("contact", (tg_name or "Telegram contact").strip()[:60] or "Telegram contact", owner=owner)
    with db.lock:
        db.run("UPDATE people SET telegram_chat_id=? WHERE id=?", chat_id, p["id"])
    return get(p["id"]), True


def her_name(owner=None) -> str:
    r = db.one("SELECT value FROM profile WHERE key=?", _key("name", owner or context.current()))
    return (r or {}).get("value") or "Someone you know"


def set_her_name(name: str) -> str:
    with db.lock:
        db.run("INSERT INTO profile (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               _key("name", context.current()), name.strip()[:60])
    return her_name()


def _check_location(lat, lng) -> None:
    if lat is None and lng is None:
        return
    try:
        ok = -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180
    except (TypeError, ValueError):
        ok = False
    if not ok:
        raise PeopleError("location is out of range")
