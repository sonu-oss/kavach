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
    kind = p.get("kind") or ("contact" if "user_id" in p and "relationship" in p and p.get("relationship") != "Safety Volunteer" and "lat" not in p else "responder")
    return {
        "id": p["id"], "kind": kind, "name": p["name"], "phone": p.get("phone") or "",
        "whatsapp": p.get("whatsapp") or "", "email": p.get("email") or "",
        "relationship": p.get("relationship") or ("Guardian" if kind == "contact" else "Safety Volunteer"),
        "telegram_linked": p.get("telegram_chat_id") is not None, "link_code": p.get("link_code", ""),
        "verified": bool(p.get("verified", 1 if kind == "contact" else 0)),
        "opted_in": bool(p.get("opted_in", 1)),
        "has_location": p.get("lat") is not None, "lat": p.get("lat"), "lng": p.get("lng"), "location_at": p.get("location_at"),
        "user_id": p.get("user_id") or p.get("owner"),
        "user_email": p.get("user_email") or "",
    }


def create(kind: str, name: str, phone: str = "", email: str = "", lat=None, lng=None, owner=None, relationship: str = "Guardian", whatsapp: str = "", user_email: str = None) -> dict:
    if kind not in ("contact", "responder"):
        raise PeopleError("kind must be contact or responder")
    if not name.strip():
        raise PeopleError("name is required")
    _check_location(lat, lng)
    pid = secrets.token_hex(5)
    owner = owner or context.current()
    if not user_email and owner:
        try:
            from ..auth import accounts
            u = accounts.user_for_id(owner)
            if u:
                user_email = (u.get("email") or "").strip().lower()
        except Exception:
            pass
    now_ts = _now()
    link_code = secrets.token_urlsafe(6)
    clean_name = name.strip()
    clean_phone = phone.strip()
    clean_email = email.strip()
    clean_rel = relationship.strip() or ("Guardian" if kind == "contact" else "Safety Volunteer")
    clean_wa = whatsapp.strip()

    with db.lock:
        if kind == "contact":
            db.run(
                "INSERT INTO guardians (id, user_id, user_email, name, relationship, phone, whatsapp, email, lat, lng, location_at, link_code, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                pid, owner or "default", user_email or "", clean_name, clean_rel, clean_phone, clean_wa, clean_email, lat, lng, now_ts if lat is not None else None, link_code, now_ts
            )
        else:
            db.run(
                "INSERT INTO responders (id, user_id, user_email, name, phone, email, relationship, lat, lng, location_at, verified, opted_in, link_code, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)",
                pid, owner, user_email or "", clean_name, clean_phone, clean_email, clean_rel, lat, lng, now_ts if lat is not None else None, link_code, now_ts
            )

        # Mirror into people for complete backward compatibility with alerts, service, and tests
        db.run(
            "INSERT INTO people (id, kind, name, phone, email, link_code, verified, opted_in, lat, lng, location_at,"
            " created_at, owner, relationship, whatsapp) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
            pid, kind, clean_name, clean_phone, clean_email, link_code,
            1 if kind == "contact" else 0, lat, lng, now_ts if lat is not None else None, now_ts, owner,
            clean_rel, clean_wa,
        )
    return public(get(pid))


def get(pid: str) -> dict:
    g = db.one("SELECT * FROM guardians WHERE id=?", pid)
    if g:
        d = dict(g)
        d["kind"] = "contact"
        d["owner"] = d.get("user_id")
        d.setdefault("user_email", "")
        d.setdefault("verified", 1)
        d.setdefault("opted_in", 1)
        d.setdefault("lat", None)
        d.setdefault("lng", None)
        d.setdefault("location_at", None)
        return d
    r = db.one("SELECT * FROM responders WHERE id=?", pid)
    if r:
        d = dict(r)
        d["kind"] = "responder"
        d["owner"] = d.get("user_id")
        d.setdefault("user_email", "")
        d.setdefault("whatsapp", "")
        return d
    p = db.one("SELECT * FROM people WHERE id=?", pid)
    if p:
        return dict(p)
    raise PeopleError("person not found", 404)


def list_kind(kind: str, owner=None) -> list[dict]:
    uid = owner or context.current()
    user_email = ""
    if uid:
        try:
            from ..auth import accounts
            u = accounts.user_for_id(uid)
            if u:
                user_email = (u.get("email") or "").strip().lower()
        except Exception:
            pass

    if kind == "contact":
        seen_ids = set()
        out = []
        if uid or user_email:
            g_rows = db.rows(
                "SELECT * FROM guardians WHERE (user_id = ? OR (? != '' AND (LOWER(COALESCE(user_email, '')) = ? OR LOWER(COALESCE(email, '')) = ?))) ORDER BY created_at",
                uid or "", user_email, user_email, user_email,
            )
            p_rows = db.rows(
                "SELECT * FROM people WHERE kind='contact' AND (owner = ? OR (? != '' AND (LOWER(COALESCE(owner, '')) = ? OR LOWER(COALESCE(email, '')) = ?))) ORDER BY created_at",
                uid or "", user_email, user_email, user_email,
            )
        else:
            g_rows = db.rows("SELECT * FROM guardians ORDER BY created_at")
            p_rows = db.rows("SELECT * FROM people WHERE kind='contact' ORDER BY created_at")

        for r in (g_rows or []):
            d = dict(r)
            d["kind"] = "contact"
            d["owner"] = d.get("user_id") or d.get("owner")
            d.setdefault("verified", 1)
            d.setdefault("opted_in", 1)
            d.setdefault("lat", None)
            d.setdefault("lng", None)
            d.setdefault("location_at", None)
            seen_ids.add(d["id"])
            out.append(public(d))

        for r in (p_rows or []):
            d = dict(r)
            if d.get("id") not in seen_ids:
                d["kind"] = "contact"
                d["owner"] = d.get("user_id") or d.get("owner")
                d.setdefault("verified", 1)
                d.setdefault("opted_in", 1)
                d.setdefault("lat", None)
                d.setdefault("lng", None)
                d.setdefault("location_at", None)
                seen_ids.add(d["id"])
                out.append(public(d))
        return out
    else:
        seen_ids = set()
        out = []
        g_rows = db.rows("SELECT * FROM responders ORDER BY created_at")
        p_rows = db.rows("SELECT * FROM people WHERE kind='responder' ORDER BY created_at")
        for r in (g_rows or []):
            d = dict(r)
            d["kind"] = "responder"
            d["owner"] = d.get("user_id") or d.get("owner")
            d.setdefault("whatsapp", "")
            seen_ids.add(d["id"])
            out.append(public(d))
        for r in (p_rows or []):
            d = dict(r)
            if d.get("id") not in seen_ids:
                d["kind"] = "responder"
                d["owner"] = d.get("user_id") or d.get("owner")
                d.setdefault("whatsapp", "")
                seen_ids.add(d["id"])
                out.append(public(d))
        return out


def owns(p: dict) -> bool:
    """A contact can only be seen or changed by the account it belongs to."""
    if p.get("kind") != "contact":
        return True
    uid = context.current()
    if not uid:
        return True
    owner = p.get("user_id") or p.get("owner")
    if not owner or owner == uid:
        return True
    try:
        from app.auth import accounts
        curr_user = accounts.user_for_id(uid)
        if curr_user:
            u_email = (curr_user.get("email") or "").lower().strip()
            p_uemail = (p.get("user_email") or "").lower().strip()
            p_email = (p.get("email") or "").lower().strip()
            if u_email and (p_uemail == u_email or p_email == u_email):
                return True
    except Exception:
        pass
    return False


def linked_contacts(owner=None) -> list[dict]:
    """Trusted contacts who have connected Telegram (raw rows: includes the chat id)."""
    uid = owner or context.current()
    if uid:
        rows = db.rows("SELECT * FROM guardians WHERE telegram_chat_id IS NOT NULL AND user_id = ? ORDER BY created_at", uid)
        if rows:
            return rows
    return db.rows("SELECT * FROM people WHERE kind='contact' AND telegram_chat_id IS NOT NULL AND owner IS ?"
                   " ORDER BY created_at", uid)


def update(pid: str, **fields) -> dict:
    p = get(pid)
    allowed = {"name", "phone", "email", "opted_in", "lat", "lng", "relationship", "whatsapp", "user_id", "user_email"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "lat" in sets or "lng" in sets:
        _check_location(sets.get("lat"), sets.get("lng"))
        sets["location_at"] = _now()
    if "opted_in" in sets:
        sets["opted_in"] = int(bool(sets["opted_in"]))
    if sets:
        with db.lock:
            if p["kind"] == "contact":
                g_sets = {k: v for k, v in sets.items() if k in {"name", "phone", "email", "relationship", "whatsapp", "user_id", "user_email", "lat", "lng", "location_at"}}
                if g_sets:
                    g_cols = ", ".join(f"{'user_id' if k=='user_id' else k}=?" for k in g_sets)
                    db.run(f"UPDATE guardians SET {g_cols} WHERE id=?", *g_sets.values(), pid)
            else:
                r_sets = {k: v for k, v in sets.items() if k in {"name", "phone", "email", "relationship", "lat", "lng", "location_at", "opted_in", "user_id", "user_email"}}
                if r_sets:
                    r_cols = ", ".join(f"{k}=?" for k in r_sets)
                    db.run(f"UPDATE responders SET {r_cols} WHERE id=?", *r_sets.values(), pid)
            # Sync to people
            p_sets = {k: v for k, v in sets.items() if k != "user_id"}
            if p_sets:
                p_cols = ", ".join(f"{k}=?" for k in p_sets)
                db.run(f"UPDATE people SET {p_cols} WHERE id=?", *p_sets.values(), pid)
    return public(get(pid))


def verify(pid: str, verified: bool = True) -> dict:
    p = get(pid)
    if p["kind"] != "responder":
        raise PeopleError("only responders need verifying")
    with db.lock:
        db.run("UPDATE responders SET verified=? WHERE id=?", int(verified), pid)
        db.run("UPDATE people SET verified=? WHERE id=?", int(verified), pid)
    return public(get(pid))


def delete(pid: str) -> None:
    get(pid)
    with db.lock:
        db.run("DELETE FROM guardians WHERE id=?", pid)
        db.run("DELETE FROM responders WHERE id=?", pid)
        db.run("DELETE FROM people WHERE id=?", pid)
        db.run("DELETE FROM alerts WHERE person_id=?", pid)


def link_telegram(code: str, chat_id: int) -> dict | None:
    """Called when someone taps Start on the bot with their personal code."""
    p = db.one("SELECT * FROM guardians WHERE link_code=?", code)
    if not p:
        p = db.one("SELECT * FROM responders WHERE link_code=?", code)
    if not p:
        p = db.one("SELECT * FROM people WHERE link_code=?", code)
    if not p:
        return None
    with db.lock:
        db.run("UPDATE guardians SET telegram_chat_id=? WHERE id=?", chat_id, p["id"])
        db.run("UPDATE responders SET telegram_chat_id=? WHERE id=?", chat_id, p["id"])
        db.run("UPDATE people SET telegram_chat_id=? WHERE id=?", chat_id, p["id"])
    return get(p["id"])


def by_chat(chat_id: int) -> list[dict]:
    res = db.rows("SELECT * FROM guardians WHERE telegram_chat_id=?", chat_id)
    if res:
        return res
    res = db.rows("SELECT * FROM responders WHERE telegram_chat_id=?", chat_id)
    if res:
        return res
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
    existing = db.one("SELECT * FROM guardians WHERE telegram_chat_id=? AND user_id IS ?", chat_id, owner) or db.one("SELECT * FROM people WHERE kind='contact' AND telegram_chat_id=? AND owner IS ?", chat_id, owner)
    if existing:
        return get(existing["id"]), False
    p = create("contact", (tg_name or "Telegram contact").strip()[:60] or "Telegram contact", owner=owner)
    with db.lock:
        db.run("UPDATE guardians SET telegram_chat_id=? WHERE id=?", chat_id, p["id"])
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
