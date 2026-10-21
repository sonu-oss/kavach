"""Real SOS alerts: Telegram (instant, live location pin, Accept buttons) with email as a backup.

Telegram needs no public webhook: a background task long-polls the Bot API, which also
works behind a laptop on hotel / hackathon Wi-Fi.
- /start <code> links a contact or responder to their Telegram chat.
- Accept / Decline buttons on a responder's alert call service.respond.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app import config
from app.sos import people, service

log = logging.getLogger("kavach.sos")

LIVE_PERIOD_S = service.TRACK_TTL_S  # Telegram keeps the live pin updatable for this long
_bot_username: str | None = None


def telegram_on() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN)


def email_on() -> bool:
    return bool(config.SMTP_USER and config.SMTP_PASSWORD)


# ---------- Telegram Bot API ----------

async def tg(method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(url, json=params)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"HTTP {r.status_code}"))
    return data["result"]


async def bot_username() -> str | None:
    global _bot_username
    if _bot_username is None and telegram_on():
        try:
            _bot_username = (await tg("getMe"))["username"]
        except Exception as exc:  # bad token, offline
            log.warning("Telegram getMe failed: %s", exc)
    return _bot_username


async def link_for(person: dict) -> str | None:
    name = await bot_username()
    return f"https://t.me/{name}?start={person['link_code']}" if name else None


# ---------- email ----------

def _send_email(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"Kavach SOS <{config.SMTP_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)


async def email(to: str | None, subject: str, body: str) -> str:
    if not email_on():
        return "not set up"
    if not to:
        return "no email"
    try:
        await asyncio.to_thread(_send_email, to, subject, body)
        return "sent"
    except Exception as exc:
        log.warning("email to %s failed: %s", to, exc)
        return "failed"


# ---------- events ----------

def _maps(lat, lng) -> str:
    return f"https://maps.google.com/?q={lat:.6f},{lng:.6f}"


def _directions(lat, lng) -> str:
    """Opens Google Maps (app or web) with a route from wherever the reader is to her."""
    return f"https://www.google.com/maps/dir/?api=1&destination={lat:.6f},{lng:.6f}"


async def sos_started(sos_id: str) -> None:
    s = service.get_sos(sos_id)
    name = people.her_name()
    track = f"{s['base_url']}/track.html?t={s['track_token']}"
    jobs = []
    for d in service.deliveries(sos_id):
        if d["role"] == "contact":
            jobs.append(_alert_contact(s, d, name, track))
        else:
            jobs.append(_alert_responder(s, d, name))
    await asyncio.gather(*jobs)


async def _alert_contact(s: dict, d: dict, name: str, track: str) -> None:
    where = (f"Live location: {track}" if s["has_location"] else
             f"Her phone hasn't found her location yet. You'll get a live pin here as soon as it does.\nLink: {track}")
    text = (f"🚨 SOS from {name}\n\nShe triggered a silent SOS and may need help right now.\n\n"
            f"{where}\n\nIf you can't reach her, call 112.")
    tg_status = "not set up" if not telegram_on() else "not linked"
    if telegram_on() and d["telegram_chat_id"]:
        try:
            await tg("sendMessage", chat_id=d["telegram_chat_id"], text=text, disable_web_page_preview=True)
            if s["has_location"]:
                await _send_live_pin(s, d)
            tg_status = "sent"
        except Exception as exc:
            log.warning("Telegram to contact %s failed: %s", d["name"], exc)
            tg_status = "failed"
    map_line = f"Map: {_maps(s['lat'], s['lng'])}" if s["has_location"] else "Map: not available yet (see the live link)"
    mail = await email(d["person_email"], f"SOS from {name}: she may need help",
                       f"{text}\n\n{map_line}\n\nSent by Kavach.")
    service.record_delivery(s["id"], d["person_id"], telegram=tg_status, email=mail)


async def _alert_responder(s: dict, d: dict, name: str) -> None:
    km = service.coarse_km(d["distance_m"]) or "?"
    area = f"https://www.google.com/maps/search/?api=1&query={s['approx_lat']},{s['approx_lng']}"
    text = (f"🚨 SOS nearby · about {km} km from you\n\nSomeone using Kavach needs help.\n"
            f"Approximate area (±1 km): {area}\n\nYou'll get her exact location only if you accept.")
    tg_status = "not set up" if not telegram_on() else "not linked"
    if telegram_on() and d["telegram_chat_id"]:
        try:
            await tg("sendMessage", chat_id=d["telegram_chat_id"], text=text, disable_web_page_preview=True,
                     reply_markup={"inline_keyboard": [[
                         {"text": "✅ Accept", "callback_data": f"a|{s['id']}|{d['person_id']}"},
                         {"text": "Decline", "callback_data": f"d|{s['id']}|{d['person_id']}"},
                     ]]})
            tg_status = "sent"
        except Exception as exc:
            log.warning("Telegram to responder %s failed: %s", d["name"], exc)
            tg_status = "failed"
    dash = f"{s['base_url']}/responder.html?as={d['person_id']}"
    mail = await email(d["person_email"], "Kavach: SOS near you",
                       f"{text}\n\nAccept or decline on your dashboard: {dash}\n\nSent by Kavach.")
    service.record_delivery(s["id"], d["person_id"], telegram=tg_status, email=mail)


async def _send_live_pin(s: dict, d: dict) -> None:
    pin = await tg("sendLocation", chat_id=d["telegram_chat_id"], latitude=s["lat"], longitude=s["lng"],
                   live_period=LIVE_PERIOD_S, horizontal_accuracy=min(s["accuracy"], 1500))
    service.record_delivery(s["id"], d["person_id"], live_message_id=pin["message_id"])


async def location_found(sos_id: str) -> None:
    """Her phone got its first GPS fix after the SOS went out: pin for contacts, alerts for responders."""
    s = service.get_sos(sos_id)
    name = people.her_name()
    jobs = []
    for d in service.deliveries(sos_id):
        if d["role"] == "contact" and d["telegram_chat_id"] and not d["live_message_id"] and telegram_on():
            async def pin(d=d):
                try:
                    await tg("sendMessage", chat_id=d["telegram_chat_id"], text=f"📍 {name}'s location is now available:")
                    await _send_live_pin(s, d)
                except Exception as exc:
                    log.warning("first pin to %s failed: %s", d["name"], exc)
            jobs.append(pin())
        elif d["role"] == "responder" and d["telegram"] is None:  # newly matched responder
            jobs.append(_alert_responder(s, d, name))
    await asyncio.gather(*jobs)


async def location_updated(sos_id: str) -> None:
    """Move every live pin (contacts, and responders who accepted)."""
    if not telegram_on():
        return
    s = service.get_sos(sos_id)
    for d in service.deliveries(sos_id):
        if d["live_message_id"] and d["telegram_chat_id"]:
            try:
                await tg("editMessageLiveLocation", chat_id=d["telegram_chat_id"], message_id=d["live_message_id"],
                         latitude=s["lat"], longitude=s["lng"], horizontal_accuracy=min(s["accuracy"], 1500))
            except Exception as exc:
                if "not modified" not in str(exc):
                    log.warning("live location update for %s failed: %s", d["name"], exc)


async def responder_accepted(sos_id: str, person_id: str) -> None:
    s = service.get_sos(sos_id)
    ds = service.deliveries(sos_id)
    me = next(d for d in ds if d["person_id"] == person_id)
    if telegram_on() and me["telegram_chat_id"]:
        try:
            await tg("sendMessage", chat_id=me["telegram_chat_id"],
                     text="✅ Thank you. Her exact location is shared with you only.\n\n"
                          f"🧭 Directions: {_directions(s['lat'], s['lng'])}\n\n"
                          "The live pin below follows her as she moves. Tap it for directions too.\n"
                          f"Dashboard: {s['base_url']}/responder.html?as={person_id}\n\nIf it's serious, call 112 too.",
                     disable_web_page_preview=True)
            pin = await tg("sendLocation", chat_id=me["telegram_chat_id"], latitude=s["lat"], longitude=s["lng"],
                           live_period=LIVE_PERIOD_S, horizontal_accuracy=min(s["accuracy"], 1500))
            service.record_delivery(sos_id, person_id, live_message_id=pin["message_id"])
        except Exception as exc:
            log.warning("exact location to responder failed: %s", exc)
    # Reassure her contacts that help is coming.
    for d in ds:
        if d["role"] == "contact" and d["telegram_chat_id"] and telegram_on():
            try:
                await tg("sendMessage", chat_id=d["telegram_chat_id"], text=f"✅ {me['name']} (Kavach responder) is on the way.")
            except Exception:
                pass


async def sos_ended(sos_id: str) -> None:
    if not telegram_on():
        return
    name = people.her_name()
    for d in service.deliveries(sos_id):
        if not d["telegram_chat_id"] or (d["role"] == "responder" and d["telegram"] != "sent"):
            continue
        try:
            if d["live_message_id"]:
                await tg("stopMessageLiveLocation", chat_id=d["telegram_chat_id"], message_id=d["live_message_id"])
            await tg("sendMessage", chat_id=d["telegram_chat_id"],
                     text=f"✅ {name} marked herself safe. Location sharing has stopped.")
        except Exception as exc:
            log.warning("safe message to %s failed: %s", d["name"], exc)


# ---------- bot: /start links, Accept / Decline buttons ----------

async def poll_forever() -> None:
    if not telegram_on():
        return
    log.info("Telegram bot @%s listening", await bot_username())
    offset = 0
    while True:
        try:
            updates = await tg("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    await _handle(u)
                except Exception as exc:
                    log.warning("Telegram update failed: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram polling error: %s", exc)
            await asyncio.sleep(5)


async def _handle(u: dict) -> None:
    if "message" in u:
        msg = u["message"]
        text = (msg.get("text") or "").strip()
        chat_id = msg["chat"]["id"]
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            person = people.link_telegram(parts[1], chat_id) if len(parts) == 2 else None
            log.info("Telegram /start %s → %s", "with code" if len(parts) == 2 else "without code",
                     f"linked {person['kind']} {person['name']}" if person else "no match")
            if person:
                role = (f"a trusted contact for {people.her_name()}. If she sends an SOS, you'll get her live location here."
                        if person["kind"] == "contact" else
                        "a Kavach responder. You'll get alerts for SOS nearby once you're verified.")
                await tg("sendMessage", chat_id=chat_id, text=f"✅ Connected, {person['name']}. You're {role}")
            else:
                await tg("sendMessage", chat_id=chat_id, text="Open your personal Kavach link to connect this chat.")
        return

    cb = u.get("callback_query")
    if not cb:
        return
    try:
        action, sos_id, person_id = cb.get("data", "").split("|")
    except ValueError:
        return
    # Only the responder the alert was sent to may answer it.
    if person_id not in {p["id"] for p in people.by_chat(cb["from"]["id"])}:
        await tg("answerCallbackQuery", callback_query_id=cb["id"], text="This alert isn't for you.")
        return
    try:
        result = service.respond(sos_id, person_id, "accept" if action == "a" else "decline")
        note = "Accepted. Her exact location is coming." if result["status"] == "accepted" else "Declined."
    except service.SosError as exc:
        result, note = None, str(exc)
    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=note)
    msg = cb.get("message")
    if msg:
        await tg("editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                 reply_markup={"inline_keyboard": [[{"text": f"✔ {note}", "callback_data": "noop|x|x"}]]})
    if result and result["status"] == "accepted" and result["changed"]:
        await responder_accepted(sos_id, person_id)
