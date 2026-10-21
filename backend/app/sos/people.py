"""Her trusted contacts and the registered responders.

Contacts are trusted by her, so they see her exact location straight away.
Responders are volunteers: an admin must verify them, they can opt out, and they
only ever see an approximate area until they accept an alert.
"""

import secrets
from datetime import datetime, timezone

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


def create(kind: str, name: str, phone: str = "", email: str = "", lat=None, lng=None) -> dict:
    if kind not in ("contact", "responder"):
        raise PeopleError("kind must be contact or responder")
    if not name.strip():
        raise PeopleError("name is required")
    _check_location(lat, lng)
    pid = secrets.token_hex(5)
    with db.lock:
        db.run(
            "INSERT INTO people (id, kind, name, phone, email, link_code, verified, opted_in, lat, lng, location_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            pid, kind, name.strip(), phone.strip(), email.strip(), secrets.token_urlsafe(6),
            1 if kind == "contact" else 0, lat, lng, _now() if lat is not None else None, _now(),
        )
    return public(get(pid))


def get(pid: str) -> dict:
    p = db.one("SELECT * FROM people WHERE id=?", pid)
    if not p:
        raise PeopleError("person not found", 404)
    return p


def list_kind(kind: str) -> list[dict]:
    return [public(p) for p in db.rows("SELECT * FROM people WHERE kind=? ORDER BY created_at", kind)]


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


def her_name() -> str:
    r = db.one("SELECT value FROM profile WHERE key='name'")
    return (r or {}).get("value") or "Someone you know"


def set_her_name(name: str) -> str:
    with db.lock:
        db.run("INSERT INTO profile (key, value) VALUES ('name', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               name.strip()[:60])
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
