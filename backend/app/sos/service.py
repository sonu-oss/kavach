"""Discreet SOS + Responder Network.

Real people, stored in the database:
- Her trusted contacts get her exact live location straight away (Telegram + email, see notify.py).
- Registered responders who are verified, opted in and within RADIUS_M get an alert with an
  approximate ~1 km area only. Accepting reveals the exact location to that responder alone.

Safety rules:
- Location arrives encrypted (crypto.py) and is only read when SOS fires (then live while active).
- SOS creation is rate limited; re-triggering an active SOS updates it instead of blocking her.
- An SOS nobody ends expires after TRACK_TTL_S, the same as the live link.
"""

import math
import secrets
import time
from datetime import datetime, timezone

from app import config
from app.sos import db, people

RADIUS_M = config.SOS_RADIUS_M
APPROX_RADIUS_M = 1000
TRACK_TTL_S = 2 * 3600
RATE_LIMIT = 3  # new SOS per user
RATE_WINDOW_S = 600


class SosError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- geometry ----------

def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def approximate(lat, lng) -> tuple[float, float]:
    """Snap to the centre of a ~1.1 km grid cell; the exact point is somewhere inside a 1 km circle."""
    return round(math.floor(lat * 100) / 100 + 0.005, 4), round(math.floor(lng * 100) / 100 + 0.005, 4)


def _parse_location(loc: dict) -> tuple[float, float, float]:
    try:
        lat, lng = float(loc["lat"]), float(loc["lng"])
        accuracy = float(loc.get("accuracy", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise SosError("location is malformed") from exc
    if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 50_000):
        raise SosError("location is out of range")
    return lat, lng, accuracy


def _expire_old() -> None:
    with db.lock:
        db.run("UPDATE sos SET status='expired', updated_at=? WHERE status='active' AND token_expires < ?",
               _now_iso(), time.time())


def _get(sos_id: str) -> dict:
    _expire_old()
    s = db.one("SELECT * FROM sos WHERE id=?", sos_id)
    if not s:
        raise SosError("SOS not found", 404)
    return s


def _active(user: str) -> dict | None:
    _expire_old()
    return db.one("SELECT * FROM sos WHERE user=? AND status='active' ORDER BY created_ts DESC LIMIT 1", user)


def coarse_km(m: float | None) -> str | None:
    """Distance in 0.5 km steps. Responders must not get a precise distance: with their own
    position it would pin her down far tighter than the approximate 1 km area."""
    if m is None:
        return None
    return "under 0.5" if m < 500 else f"{round(m / 500) / 2:g}"


_km = coarse_km


# ---------- SOS lifecycle ----------

def trigger(user: str, loc: dict | None, trigger_kind: str, source: str, base_url: str) -> dict:
    """Returns the summary plus 'event':
    'started' (notify everyone), 'updated' (move live pins) or 'first_fix' (location just became known).
    loc is None when her phone had no GPS fix: the SOS goes out anyway and the location follows."""
    has_loc = loc is not None
    lat, lng, accuracy = _parse_location(loc) if has_loc else (0.0, 0.0, 0.0)
    alat, alng = approximate(lat, lng) if has_loc else (0.0, 0.0)
    with db.lock:
        active = _active(user)
        if active:
            # Re-triggering while an SOS is active refreshes it: never block a person in danger twice.
            if not has_loc:
                return {**summary(active["id"]), "updated_existing": True, "event": "updated"}
            first_fix = not active["has_location"]
            db.run("UPDATE sos SET lat=?, lng=?, accuracy=?, approx_lat=?, approx_lng=?, source=?, has_location=1,"
                   " updated_at=? WHERE id=?", lat, lng, accuracy, alat, alng, source, _now_iso(), active["id"])
            if first_fix:
                _match_responders(active["id"], lat, lng)
            return {**summary(active["id"]), "updated_existing": True, "event": "first_fix" if first_fix else "updated"}

        recent = db.one("SELECT COUNT(*) AS n FROM sos WHERE user=? AND created_ts > ?", user, time.time() - RATE_WINDOW_S)
        if recent["n"] >= RATE_LIMIT:
            raise SosError("Too many SOS alerts in 10 minutes. Call 112 directly.", 429)

        sos_id, now = secrets.token_hex(6), time.time()
        with db.connect() as c:
            c.execute("BEGIN")
            c.execute(
                "INSERT INTO sos (id, user, lat, lng, accuracy, approx_lat, approx_lng, trigger, source, status,"
                " track_token, token_expires, base_url, has_location, created_ts, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
                (sos_id, user, lat, lng, accuracy, alat, alng, trigger_kind, source, secrets.token_urlsafe(16),
                 now + TRACK_TTL_S, base_url, int(has_loc), now, _now_iso(), _now_iso()),
            )
            for p in c.execute("SELECT * FROM people WHERE kind='contact'").fetchall():
                c.execute("INSERT INTO alerts (sos_id, person_id, role, status, at) VALUES (?, ?, 'contact', 'notified', ?)",
                          (sos_id, p["id"], _now_iso()))
            c.execute("COMMIT")
        if has_loc:  # without a location nobody can be matched yet; done on the first fix
            _match_responders(sos_id, lat, lng)
    return {**summary(sos_id), "updated_existing": False, "event": "started"}


def _match_responders(sos_id: str, lat: float, lng: float) -> list[str]:
    """Alert every eligible responder not already alerted for this SOS. Returns their ids."""
    already = {a["person_id"] for a in db.rows("SELECT person_id FROM alerts WHERE sos_id=?", sos_id)}
    added = []
    for p in db.rows("SELECT * FROM people WHERE kind='responder'"):
        if p["id"] not in already and _eligible(p, lat, lng)[0]:
            db.run("INSERT INTO alerts (sos_id, person_id, role, status, distance_m) VALUES (?, ?, 'responder', 'pending', ?)",
                   sos_id, p["id"], haversine_m(lat, lng, p["lat"], p["lng"]))
            added.append(p["id"])
    return added


def _eligible(p: dict, lat: float, lng: float) -> tuple[bool, str]:
    if not p["verified"]:
        return False, "not verified yet"
    if not p["opted_in"]:
        return False, "alerts turned off"
    if p["lat"] is None:
        return False, "no location shared"
    dist = haversine_m(lat, lng, p["lat"], p["lng"])
    if dist > RADIUS_M:
        return False, f"{dist / 1000:.1f} km away, outside {RADIUS_M / 1000:.0f} km"
    return True, ""


def update_location(sos_id: str, loc: dict) -> dict:
    """Live GPS from her phone while the SOS is active."""
    lat, lng, accuracy = _parse_location(loc)
    s = _get(sos_id)
    if s["status"] != "active":
        raise SosError("this SOS has ended", 409)
    alat, alng = approximate(lat, lng)
    first_fix = not s["has_location"]
    with db.lock:
        db.run("UPDATE sos SET lat=?, lng=?, accuracy=?, approx_lat=?, approx_lng=?, has_location=1, source='gps',"
               " updated_at=? WHERE id=?", lat, lng, accuracy, alat, alng, _now_iso(), sos_id)
        if first_fix:
            _match_responders(sos_id, lat, lng)
    return {"ok": True, "first_fix": first_fix, "updated_at": _get(sos_id)["updated_at"]}


def summary(sos_id: str) -> dict:
    s = _get(sos_id)
    alerts = db.rows(
        "SELECT a.*, p.name, p.telegram_chat_id FROM alerts a JOIN people p ON p.id=a.person_id WHERE a.sos_id=?", sos_id)
    alerted = {a["person_id"] for a in alerts}
    excluded = []
    for p in db.rows("SELECT * FROM people WHERE kind='responder'"):
        if p["id"] not in alerted:
            ok, reason = _eligible(p, s["lat"], s["lng"]) if s["has_location"] else (False, "waiting for her location")
            excluded.append({"id": p["id"], "name": p["name"], "reason": reason or "registered after this SOS"})
    return {
        "sos_id": s["id"],
        "status": s["status"],
        "has_location": bool(s["has_location"]),
        "location_source": s["source"],
        "created_at": s["created_at"],
        "updated_at": s["updated_at"],
        "track_path": f"/track.html?t={s['track_token']}",
        "track_expires_in_min": TRACK_TTL_S // 60,
        "contacts_notified": [
            {"id": a["person_id"], "name": a["name"], "telegram": a["telegram"] or "sending…", "email": a["email"] or "sending…"}
            for a in alerts if a["role"] == "contact"
        ],
        "approx_area": {"lat": s["approx_lat"], "lng": s["approx_lng"], "radius_m": APPROX_RADIUS_M},
        "responders_alerted": [
            {"id": a["person_id"], "name": a["name"], "distance_km": _km(a["distance_m"]), "status": a["status"],
             "telegram": a["telegram"] or "sending…"}
            for a in alerts if a["role"] == "responder"
        ],
        "responders_excluded": excluded,
    }


def cancel(sos_id: str) -> dict:
    _get(sos_id)
    with db.lock:
        db.run("UPDATE sos SET status='cancelled', updated_at=? WHERE id=? AND status='active'", _now_iso(), sos_id)
    return {"sos_id": sos_id, "status": _get(sos_id)["status"]}


def active_for(user: str) -> dict | None:
    """Lets her SOS screen pick up an alert that is still running (e.g. after a page reload)."""
    a = _active(user)
    return summary(a["id"]) if a else None


def track(token: str) -> dict:
    _expire_old()
    s = db.one("SELECT * FROM sos WHERE track_token=?", token)
    if not s or s["status"] == "expired":
        raise SosError("This location link is invalid or has expired.", 404)
    if s["status"] != "active":
        return {"status": s["status"], "updated_at": s["updated_at"]}
    on_way = db.rows("SELECT p.name FROM alerts a JOIN people p ON p.id=a.person_id"
                     " WHERE a.sos_id=? AND a.role='responder' AND a.status='accepted'", s["id"])
    if not s["has_location"]:
        return {"status": s["status"], "name": people.her_name(), "waiting_for_location": True,
                "created_at": s["created_at"], "updated_at": s["updated_at"], "responders_on_the_way": []}
    return {
        "status": s["status"], "name": people.her_name(),
        "lat": s["lat"], "lng": s["lng"], "accuracy": s["accuracy"],
        "created_at": s["created_at"], "updated_at": s["updated_at"],
        "responders_on_the_way": [r["name"] for r in on_way],
    }


# ---------- responders ----------

def alerts_for(responder_id: str) -> list[dict]:
    me = people.get(responder_id)
    _expire_old()
    out = []
    mine = db.rows("SELECT a.* FROM alerts a JOIN sos s ON s.id=a.sos_id"
                   " WHERE a.person_id=? AND a.role='responder' ORDER BY s.created_ts DESC", responder_id)
    for a in mine:
        s = _get(a["sos_id"])
        others = db.one("SELECT COUNT(*) AS n FROM alerts WHERE sos_id=? AND role='responder'"
                        " AND status='accepted' AND person_id<>?", s["id"], responder_id)["n"]
        item = {
            "sos_id": s["id"],
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
            "sos_status": s["status"],
            "my_status": a["status"],
            "my_position": {"lat": me["lat"], "lng": me["lng"]} if me["lat"] is not None else None,
            "approx_area": {"lat": s["approx_lat"], "lng": s["approx_lng"], "radius_m": APPROX_RADIUS_M},
            "approx_distance_km": _km(a["distance_m"]),
            "others_accepted": others,
        }
        # The exact point goes only to a responder who accepted, and only while the SOS is active.
        if a["status"] == "accepted" and s["status"] == "active":
            item["exact"] = {"lat": s["lat"], "lng": s["lng"], "accuracy": s["accuracy"]}
        out.append(item)
    return out


def respond(sos_id: str, responder_id: str, action: str) -> dict:
    s = _get(sos_id)
    alert = db.one("SELECT * FROM alerts WHERE sos_id=? AND person_id=? AND role='responder'", sos_id, responder_id)
    if not alert:
        raise SosError("this responder was not alerted for this SOS", 403)
    if s["status"] != "active":
        raise SosError("this SOS has ended", 409)
    status = "accepted" if action == "accept" else "declined"
    changed = alert["status"] != status
    with db.lock:
        db.run("UPDATE alerts SET status=?, at=? WHERE sos_id=? AND person_id=?", status, _now_iso(), sos_id, responder_id)
    return {"ok": True, "status": status, "changed": changed}


# ---------- used by notify.py ----------

def get_sos(sos_id: str) -> dict:
    return _get(sos_id)


def deliveries(sos_id: str) -> list[dict]:
    return db.rows("SELECT a.*, p.name, p.email AS person_email, p.telegram_chat_id FROM alerts a"
                   " JOIN people p ON p.id=a.person_id WHERE a.sos_id=?", sos_id)


def record_delivery(sos_id: str, person_id: str, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("telegram", "email", "live_message_id")}
    if allowed:
        cols = ", ".join(f"{k}=?" for k in allowed)
        with db.lock:
            db.run(f"UPDATE alerts SET {cols} WHERE sos_id=? AND person_id=?", *allowed.values(), sos_id, person_id)


def reset() -> None:
    """Clears SOS history (keeps contacts and responders)."""
    with db.lock:
        db.run("DELETE FROM alerts")
        db.run("DELETE FROM sos")


def clear_history(user: str) -> None:
    """Her choice to wipe past SOS alerts. Refused while one is running, so nobody stops getting her location by accident."""
    _expire_old()
    if _active(user):
        raise SosError("Your SOS is still on. Tap I'm safe first.", 409)
    reset()
