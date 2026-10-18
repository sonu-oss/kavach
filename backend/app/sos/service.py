"""Discreet SOS + Responder Network (prototype: in-memory state, mock responders, simulated calls).

Safety design that IS real here:
- Location is read once, at trigger time, and arrives encrypted (see crypto.py).
- Responders see only an approximate ~1 km area until they accept; then that responder alone
  gets the exact point. Trusted contacts get a private, expiring live-location link.
- Only verified, opted-in responders within the radius are alerted.
- SOS creation is rate limited per user; re-triggering an active SOS updates it instead.
"""

import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

RADIUS_M = 3000
APPROX_RADIUS_M = 1000
TRACK_TTL_S = 2 * 3600
RATE_LIMIT = 3  # new SOS per user
RATE_WINDOW_S = 600

# Mock responders, placed relative to wherever the SOS happens so the demo map works anywhere.
RESPONDERS = [
    {"id": "r-asha", "name": "Asha K", "role": "Volunteer, first-aid trained", "verified": True, "opted_in": True,
     "distance_m": 650, "bearing": 40},
    {"id": "r-campus", "name": "Campus security desk", "role": "Verified institution", "verified": True,
     "opted_in": True, "distance_m": 1400, "bearing": 250},
    {"id": "r-meena", "name": "Meena R", "role": "Off-duty nurse", "verified": True, "opted_in": True,
     "distance_m": 2300, "bearing": 170},
    {"id": "r-ravi", "name": "Ravi T", "role": "Signed up, ID not yet verified", "verified": False, "opted_in": True,
     "distance_m": 1100, "bearing": 100},
    {"id": "r-kiran", "name": "Kiran P", "role": "Verified, alerts turned off", "verified": True, "opted_in": False,
     "distance_m": 900, "bearing": 300},
    {"id": "r-farah", "name": "Farah N", "role": "Volunteer", "verified": True, "opted_in": True,
     "distance_m": 5200, "bearing": 10},
]
RESPONDER_BY_ID = {r["id"]: r for r in RESPONDERS}


class SosError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Sos:
    id: str
    user: str
    lat: float
    lng: float
    accuracy: float
    approx_lat: float
    approx_lng: float
    contacts: list[dict]
    trigger: str
    source: str
    created_at: str
    track_token: str
    token_expires: float
    status: str = "active"  # active | cancelled
    alerts: dict[str, dict] = field(default_factory=dict)  # responder id -> {status, at, distance_m}
    updated_at: str = ""


_lock = threading.Lock()
_sos: dict[str, Sos] = {}
_by_token: dict[str, str] = {}
_created: dict[str, list[float]] = {}  # user -> creation times


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _expire_old() -> None:
    """An SOS nobody ended stops sharing after the same 2 hours as the live link."""
    now = time.time()
    for s in _sos.values():
        if s.status == "active" and now > s.token_expires:
            s.status = "expired"
            s.updated_at = _now_iso()


# ---------- geometry ----------

def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def offset(lat, lng, distance_m, bearing_deg) -> tuple[float, float]:
    r = 6_371_000
    b, d = math.radians(bearing_deg), distance_m / r
    p1, l1 = math.radians(lat), math.radians(lng)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def approximate(lat, lng) -> tuple[float, float]:
    """Snap to the centre of a ~1.1 km grid cell; the exact point is somewhere inside a 1 km circle."""
    return round(math.floor(lat * 100) / 100 + 0.005, 4), round(math.floor(lng * 100) / 100 + 0.005, 4)


def responder_position(r: dict, sos: Sos) -> tuple[float, float]:
    return offset(sos.lat, sos.lng, r["distance_m"], r["bearing"])


# ---------- SOS lifecycle ----------

def trigger(user: str, loc: dict, contacts: list[dict], trigger_kind: str, source: str) -> dict:
    try:
        lat, lng = float(loc["lat"]), float(loc["lng"])
        accuracy = float(loc.get("accuracy", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise SosError("location is malformed") from exc
    if not (-90 <= lat <= 90 and -180 <= lng <= 180 and 0 <= accuracy <= 50_000):
        raise SosError("location is out of range")

    with _lock:
        _expire_old()
        # Re-triggering while an SOS is active refreshes it: never block a person in danger twice.
        active = next((s for s in _sos.values() if s.user == user and s.status == "active"), None)
        if active:
            active.lat, active.lng, active.accuracy = lat, lng, accuracy
            active.approx_lat, active.approx_lng = approximate(lat, lng)
            active.updated_at = _now_iso()
            return summary(active, updated=True)

        now = time.time()
        recent = [t for t in _created.get(user, []) if now - t < RATE_WINDOW_S]
        if len(recent) >= RATE_LIMIT:
            raise SosError("Too many SOS alerts in 10 minutes. Call 112 directly.", 429)
        _created[user] = recent + [now]

        token = secrets.token_urlsafe(16)
        sos = Sos(
            id=secrets.token_hex(6), user=user, lat=lat, lng=lng, accuracy=accuracy,
            approx_lat=0, approx_lng=0, contacts=contacts, trigger=trigger_kind, source=source,
            created_at=_now_iso(), track_token=token, token_expires=now + TRACK_TTL_S,
        )
        sos.approx_lat, sos.approx_lng = approximate(lat, lng)
        sos.updated_at = sos.created_at
        for r in RESPONDERS:
            rl, rg = responder_position(r, sos)
            dist = haversine_m(lat, lng, rl, rg)
            if r["verified"] and r["opted_in"] and dist <= RADIUS_M:
                sos.alerts[r["id"]] = {"status": "pending", "at": None, "distance_m": dist}
        _sos[sos.id] = sos
        _by_token[token] = sos.id
        return summary(sos, updated=False)


def summary(sos: Sos, updated: bool) -> dict:
    excluded = []
    for r in RESPONDERS:
        if r["id"] in sos.alerts:
            continue
        rl, rg = responder_position(r, sos)
        dist = haversine_m(sos.lat, sos.lng, rl, rg)
        reason = ("ID not verified" if not r["verified"] else "has not opted in to alerts" if not r["opted_in"]
                  else f"{dist / 1000:.1f} km away, outside {RADIUS_M / 1000:.0f} km")
        excluded.append({"id": r["id"], "name": r["name"], "reason": reason})
    return {
        "sos_id": sos.id,
        "updated_existing": updated,
        "status": sos.status,
        "created_at": sos.created_at,
        "updated_at": sos.updated_at,
        "track_path": f"/track.html?t={sos.track_token}",
        "track_expires_in_min": TRACK_TTL_S // 60,
        "contacts_notified": [
            {"name": c["name"], "phone": c.get("phone", ""), "channel": "SMS + call (simulated)"} for c in sos.contacts
        ],
        "approx_area": {"lat": sos.approx_lat, "lng": sos.approx_lng, "radius_m": APPROX_RADIUS_M},
        "responders_alerted": [
            {"id": rid, "name": RESPONDER_BY_ID[rid]["name"], "role": RESPONDER_BY_ID[rid]["role"],
             "distance_km": round(a["distance_m"] / 500) / 2, "status": a["status"]}
            for rid, a in sos.alerts.items()
        ],
        "responders_excluded": excluded,
    }


def get(sos_id: str) -> Sos:
    _expire_old()
    sos = _sos.get(sos_id)
    if not sos:
        raise SosError("SOS not found", 404)
    return sos


def cancel(sos_id: str) -> dict:
    with _lock:
        sos = get(sos_id)
        sos.status = "cancelled"
        sos.updated_at = _now_iso()
        return {"sos_id": sos.id, "status": sos.status}


def status_for_victim(sos_id: str) -> dict:
    return summary(get(sos_id), updated=False)


def active_for(user: str) -> dict | None:
    """Lets her SOS screen pick up an alert that is still running (e.g. after a page reload)."""
    _expire_old()
    sos = next((s for s in _sos.values() if s.user == user and s.status == "active"), None)
    return summary(sos, updated=False) if sos else None


def track(token: str) -> dict:
    _expire_old()
    sos_id = _by_token.get(token)
    sos = _sos.get(sos_id) if sos_id else None
    if not sos or time.time() > sos.token_expires:
        raise SosError("This location link is invalid or has expired.", 404)
    if sos.status != "active":
        return {"status": sos.status, "updated_at": sos.updated_at}
    return {
        "status": sos.status,
        "lat": sos.lat, "lng": sos.lng, "accuracy": sos.accuracy,
        "created_at": sos.created_at, "updated_at": sos.updated_at,
        "responders_on_the_way": [RESPONDER_BY_ID[r]["name"] for r, a in sos.alerts.items() if a["status"] == "accepted"],
    }


# ---------- responders ----------

def list_responders() -> list[dict]:
    return [{k: r[k] for k in ("id", "name", "role", "verified", "opted_in")} for r in RESPONDERS]


def alerts_for(responder_id: str) -> list[dict]:
    if responder_id not in RESPONDER_BY_ID:
        raise SosError("unknown responder", 404)
    _expire_old()
    out = []
    for sos in sorted(_sos.values(), key=lambda s: s.created_at, reverse=True):
        a = sos.alerts.get(responder_id)
        if not a:
            continue
        me = responder_position(RESPONDER_BY_ID[responder_id], sos)
        item = {
            "sos_id": sos.id,
            "created_at": sos.created_at,
            "updated_at": sos.updated_at,
            "sos_status": sos.status,
            "my_status": a["status"],
            "my_position": {"lat": me[0], "lng": me[1]},
            "approx_area": {"lat": sos.approx_lat, "lng": sos.approx_lng, "radius_m": APPROX_RADIUS_M},
            "approx_distance_km": round(a["distance_m"] / 500) / 2,
            "others_accepted": sum(1 for r, x in sos.alerts.items() if r != responder_id and x["status"] == "accepted"),
        }
        # The exact point goes only to a responder who accepted, and only while the SOS is active.
        if a["status"] == "accepted" and sos.status == "active":
            item["exact"] = {"lat": sos.lat, "lng": sos.lng, "accuracy": sos.accuracy}
        out.append(item)
    return out


def respond(sos_id: str, responder_id: str, action: str) -> dict:
    with _lock:
        sos = get(sos_id)
        if responder_id not in sos.alerts:
            raise SosError("this responder was not alerted for this SOS", 403)
        if sos.status != "active":
            raise SosError("this SOS has ended", 409)
        sos.alerts[responder_id].update(status="accepted" if action == "accept" else "declined", at=_now_iso())
    return {"ok": True, "status": sos.alerts[responder_id]["status"]}


def reset() -> None:
    with _lock:
        _sos.clear()
        _by_token.clear()
        _created.clear()
