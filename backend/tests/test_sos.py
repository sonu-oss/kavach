import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config
from app.sos import crypto, db, notify, people, service

# The real senders, kept before the autouse fixture replaces them for endpoint tests.
REAL = {n: getattr(notify, n) for n in ("sos_started", "location_updated", "responder_accepted", "sos_ended", "location_found", "sos_repeated")}

HER = {"lat": 12.9352, "lng": 77.6245, "accuracy": 25}
NEAR = (12.9390, 77.6290)  # ~0.6 km away
FAR = (12.9800, 77.6245)  # ~5 km away


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "_key", None)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "SMTP_USER", "")
    monkeypatch.setattr(notify, "_bot_username", None)
    # Endpoint tests never send real alerts; notify is tested on its own below.
    sent = []

    async def record(*args):
        sent.append(args)

    for name in ("sos_started", "location_updated", "responder_accepted", "sos_ended", "location_found", "sos_repeated"):
        monkeypatch.setattr(notify, name, record)
    return sent


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def enc(loc=HER):
    return crypto.encrypt_location_for_tests(loc)


def send_sos(client, loc=HER):
    return client.post("/sos", json={"ciphertext": enc(loc)})


def responder(client, name, at=NEAR, verify=True, opted_in=True):
    p = client.post("/people", json={"kind": "responder", "name": name, "lat": at[0] if at else None,
                                     "lng": at[1] if at else None}).json()
    if verify:
        client.post(f"/people/{p['id']}/verify")
    if not opted_in:
        client.patch(f"/people/{p['id']}", json={"opted_in": False})
    return p["id"]


# ---------- encryption & validation ----------

def test_location_must_be_encrypted_with_the_server_key(client):
    assert client.get("/sos/key").json()["alg"] == "RSA-OAEP-256"
    assert client.post("/sos", json={"ciphertext": "A" * 344}).status_code == 400


def test_out_of_range_location_rejected(client):
    assert send_sos(client, loc={"lat": 200, "lng": 0}).status_code == 400


# ---------- people ----------

def test_contacts_are_trusted_but_responders_need_verifying(client):
    c = client.post("/people", json={"kind": "contact", "name": "Mom", "email": "mom@example.com"}).json()
    r = client.post("/people", json={"kind": "responder", "name": "Asha", "lat": NEAR[0], "lng": NEAR[1]}).json()
    assert c["verified"] and not r["verified"]
    assert "telegram_chat_id" not in c and not c["telegram_linked"]
    assert client.post(f"/people/{r['id']}/verify").json()["verified"]
    assert [p["name"] for p in client.get("/people?kind=contact").json()["people"]] == ["Mom"]


def test_people_survive_a_restart(client):
    client.post("/people", json={"kind": "contact", "name": "Mom"})
    # A fresh read straight from the database file, as after a server restart.
    assert [p["name"] for p in db.rows("SELECT * FROM people")] == ["Mom"]


# ---------- who gets alerted ----------

def test_only_verified_opted_in_nearby_responders_are_alerted(client):
    asha = responder(client, "Asha")
    responder(client, "Ravi", verify=False)
    responder(client, "Kiran", opted_in=False)
    responder(client, "Farah", at=FAR)
    responder(client, "Noloc", at=None)
    client.post("/people", json={"kind": "contact", "name": "Mom"})
    r = send_sos(client).json()
    assert [x["id"] for x in r["responders_alerted"]] == [asha]
    reasons = {x["name"]: x["reason"] for x in r["responders_excluded"]}
    assert "not verified" in reasons["Ravi"] and "turned off" in reasons["Kiran"]
    assert "outside" in reasons["Farah"] and "no location" in reasons["Noloc"]
    assert [c["name"] for c in r["contacts_notified"]] == ["Mom"]


def test_responders_see_only_approximate_area_until_they_accept(client):
    asha, campus = responder(client, "Asha"), responder(client, "Campus", at=(12.9330, 77.6200))
    sos_id = send_sos(client).json()["sos_id"]
    alert = client.get(f"/responders/{asha}/alerts").json()["alerts"][0]
    assert "exact" not in alert
    area = alert["approx_area"]
    assert (area["lat"], area["lng"]) != (HER["lat"], HER["lng"])
    assert service.haversine_m(area["lat"], area["lng"], HER["lat"], HER["lng"]) < area["radius_m"]

    client.post(f"/sos/{sos_id}/respond", json={"responder_id": asha, "action": "accept"})
    assert client.get(f"/responders/{asha}/alerts").json()["alerts"][0]["exact"]["lat"] == HER["lat"]
    # ...and only to the one who accepted.
    assert "exact" not in client.get(f"/responders/{campus}/alerts").json()["alerts"][0]


def test_unalerted_responder_cannot_accept(client):
    ravi = responder(client, "Ravi", verify=False)
    sos_id = send_sos(client).json()["sos_id"]
    assert client.get(f"/responders/{ravi}/alerts").json()["alerts"] == []
    r = client.post(f"/sos/{sos_id}/respond", json={"responder_id": ravi, "action": "accept"})
    assert r.status_code == 403


# ---------- lifecycle ----------

def test_track_link_shows_exact_location_until_she_is_safe(client):
    r = send_sos(client).json()
    token = r["track_path"].split("t=")[1]
    t = client.get(f"/sos/track/{token}").json()
    assert t["status"] == "active" and t["lat"] == HER["lat"]
    client.post(f"/sos/{r['sos_id']}/cancel")
    ended = client.get(f"/sos/track/{token}").json()
    assert ended["status"] == "cancelled" and "lat" not in ended
    assert client.get("/sos/track/not-a-token").status_code == 404


def test_cancelled_sos_hides_exact_location_from_responders(client):
    asha = responder(client, "Asha")
    sos_id = send_sos(client).json()["sos_id"]
    client.post(f"/sos/{sos_id}/respond", json={"responder_id": asha, "action": "accept"})
    client.post(f"/sos/{sos_id}/cancel")
    assert "exact" not in client.get(f"/responders/{asha}/alerts").json()["alerts"][0]


def test_live_location_updates_while_active(client, fresh):
    asha = responder(client, "Asha")
    sos_id = send_sos(client).json()["sos_id"]
    client.post(f"/sos/{sos_id}/respond", json={"responder_id": asha, "action": "accept"})
    moved = {**HER, "lat": 12.9401}
    assert client.post(f"/sos/{sos_id}/location", json={"ciphertext": enc(moved)}).status_code == 200
    assert client.get(f"/responders/{asha}/alerts").json()["alerts"][0]["exact"]["lat"] == 12.9401
    client.post(f"/sos/{sos_id}/cancel")
    assert client.post(f"/sos/{sos_id}/location", json={"ciphertext": enc(moved)}).status_code == 409


def test_retrigger_updates_active_sos_and_rate_limit_applies(client):
    first = send_sos(client).json()
    again = send_sos(client, loc={**HER, "lat": 12.94}).json()
    assert again["updated_existing"] and again["sos_id"] == first["sos_id"]
    client.post(f"/sos/{first['sos_id']}/cancel")
    for _ in range(service.RATE_LIMIT - 1):  # new alerts, each closed again
        client.post(f"/sos/{send_sos(client).json()['sos_id']}/cancel")
    blocked = send_sos(client)
    assert blocked.status_code == 429 and "112" in blocked.json()["detail"]


def test_active_sos_can_be_recovered_after_a_reload(client):
    assert client.get("/sos/active").json()["active"] is None
    sos_id = send_sos(client).json()["sos_id"]
    assert client.get("/sos/active").json()["active"]["sos_id"] == sos_id
    client.post(f"/sos/{sos_id}/cancel")
    assert client.get("/sos/active").json()["active"] is None


def test_sos_expires_after_two_hours(client, monkeypatch):
    asha = responder(client, "Asha")
    r = send_sos(client).json()
    token = r["track_path"].split("t=")[1]
    real = service.time.time
    monkeypatch.setattr(service.time, "time", lambda: real() + service.TRACK_TTL_S + 1)
    assert client.get("/sos/active").json()["active"] is None
    assert client.get(f"/responders/{asha}/alerts").json()["alerts"][0]["sos_status"] == "expired"
    assert client.get(f"/sos/track/{token}").status_code == 404
    assert send_sos(client).json()["sos_id"] != r["sos_id"]


def test_alerts_are_sent_in_the_background(client, fresh):
    sos_id = send_sos(client).json()["sos_id"]
    client.post(f"/sos/{sos_id}/cancel")
    assert (sos_id,) in fresh  # sos_started and sos_ended were scheduled


# ---------- Telegram + email delivery (Bot API faked) ----------

@pytest.fixture
def fake_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:test")
    calls = []

    async def tg(method, **params):
        calls.append((method, params))
        if method == "getMe":
            return {"username": "kavach_test_bot"}
        return {"message_id": len(calls)}

    monkeypatch.setattr(notify, "tg", tg)
    return calls


def _start_sos(contacts=(), responders=()):
    for name, chat in contacts:
        p = people.create("contact", name)
        if chat:
            people.link_telegram(p["link_code"], chat)
    ids = []
    for name, chat in responders:
        p = people.create("responder", name, lat=NEAR[0], lng=NEAR[1])
        people.verify(p["id"])
        if chat:
            people.link_telegram(p["link_code"], chat)
        ids.append(p["id"])
    sos_id = service.trigger("u", HER, "manual", "demo", "https://example.trycloudflare.com")["sos_id"]
    return sos_id, ids


def test_contacts_get_live_pin_and_responders_get_buttons(fake_telegram):
    sos_id, _ = _start_sos(contacts=[("Mom", 111), ("Riya", None)], responders=[("Asha", 222)])
    asyncio.run(REAL["sos_started"](sos_id))
    methods = [(m, p.get("chat_id")) for m, p in fake_telegram]
    assert ("sendLocation", 111) in methods  # live pin to Mom
    assert not any(c == 222 and m == "sendLocation" for m, c in methods)  # no exact pin to responder yet
    buttons = next(p for m, p in fake_telegram if p.get("chat_id") == 222)["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["✅ Accept", "Decline"]
    status = {c["name"]: c["telegram"] for c in service.summary(sos_id)["contacts_notified"]}
    assert status == {"Mom": "sent", "Riya": "not linked"}


def test_start_link_and_accept_button(fake_telegram):
    sos_id, (asha,) = _start_sos(responders=[("Asha", None)])
    code = people.get(asha)["link_code"]
    asyncio.run(notify._handle({"message": {"text": f"/start {code}", "chat": {"id": 222}}}))
    assert people.get(asha)["telegram_chat_id"] == 222

    # Someone else pressing her button is refused.
    asyncio.run(notify._handle({"callback_query": {"id": "c1", "data": f"a|{sos_id}|{asha}", "from": {"id": 999}}}))
    assert service.alerts_for(asha)[0]["my_status"] == "pending"

    asyncio.run(notify._handle({"callback_query": {"id": "c2", "data": f"a|{sos_id}|{asha}", "from": {"id": 222},
                                                   "message": {"chat": {"id": 222}, "message_id": 5}}}))
    assert service.alerts_for(asha)[0]["my_status"] == "accepted"


def test_without_telegram_or_email_status_says_not_set_up():
    sos_id, _ = _start_sos(contacts=[("Mom", None)])
    asyncio.run(REAL["sos_started"](sos_id))
    c = service.summary(sos_id)["contacts_notified"][0]
    assert c["telegram"] == "not set up" and c["email"] == "not set up"


def test_accepting_sends_exact_pin_and_safe_stops_it(fake_telegram):
    sos_id, (asha,) = _start_sos(contacts=[("Mom", 111)], responders=[("Asha", 222)])
    asyncio.run(REAL["sos_started"](sos_id))
    service.respond(sos_id, asha, "accept")
    asyncio.run(REAL["responder_accepted"](sos_id, asha))
    assert any(m == "sendLocation" and p["chat_id"] == 222 for m, p in fake_telegram)  # exact pin now
    assert any(p.get("chat_id") == 111 and "on the way" in p.get("text", "") for m, p in fake_telegram)
    fake_telegram.clear()
    service.cancel(sos_id)
    asyncio.run(REAL["sos_ended"](sos_id))
    stopped = {p["chat_id"] for m, p in fake_telegram if m == "stopMessageLiveLocation"}
    assert stopped == {111, 222}



# ---------- no GPS fix yet: the SOS still goes out, location follows ----------

def test_sos_without_location_goes_out_and_first_fix_alerts_responders(client):
    asha = responder(client, "Asha")
    r = client.post("/sos", json={"location_source": "none"}).json()
    assert r["has_location"] is False and r["responders_alerted"] == []
    assert r["responders_excluded"][0]["reason"] == "waiting for her location"
    token = r["track_path"].split("t=")[1]
    assert client.get(f"/sos/track/{token}").json()["waiting_for_location"] is True

    fix = client.post(f"/sos/{r['sos_id']}/location", json={"ciphertext": enc()}).json()
    assert fix["first_fix"] is True
    after = client.get(f"/sos/{r['sos_id']}").json()
    assert after["has_location"] and [x["id"] for x in after["responders_alerted"]] == [asha]
    assert client.get(f"/sos/track/{token}").json()["lat"] == HER["lat"]
    # Later updates are ordinary moves, not a second "first fix".
    assert client.post(f"/sos/{r['sos_id']}/location", json={"ciphertext": enc()}).json()["first_fix"] is False


def test_retrigger_without_location_keeps_the_known_location(client):
    r = send_sos(client).json()
    again = client.post("/sos", json={"location_source": "none"}).json()
    assert again["sos_id"] == r["sos_id"] and again["has_location"] is True
    token = r["track_path"].split("t=")[1]
    assert client.get(f"/sos/track/{token}").json()["lat"] == HER["lat"]


def test_contact_gets_no_pin_until_location_found(fake_telegram):
    people.link_telegram(people.create("contact", "Mom")["link_code"], 111)
    p = people.create("responder", "Asha", lat=NEAR[0], lng=NEAR[1])
    people.verify(p["id"])
    people.link_telegram(p["link_code"], 222)
    sos_id = service.trigger("u", None, "manual", "none", "https://x.trycloudflare.com")["sos_id"]
    asyncio.run(REAL["sos_started"](sos_id))
    assert not any(m == "sendLocation" for m, _ in fake_telegram)
    assert "hasn't found her location yet" in next(p["text"] for m, p in fake_telegram if m == "sendMessage")

    fake_telegram.clear()
    service.update_location(sos_id, HER)
    asyncio.run(REAL["location_found"](sos_id))
    assert ("sendLocation", 111) in [(m, p.get("chat_id")) for m, p in fake_telegram]  # pin to Mom
    assert any(p.get("chat_id") == 222 and "reply_markup" in p for m, p in fake_telegram)  # responder buttons


def test_responders_only_get_coarse_distances(fake_telegram):
    assert service.coarse_km(240) == "under 0.5" and service.coarse_km(1400) == "1.5" and service.coarse_km(None) is None
    sos_id, _ = _start_sos(responders=[("Asha", 222)])
    asyncio.run(REAL["sos_started"](sos_id))
    text = next(p["text"] for m, p in fake_telegram if p.get("chat_id") == 222)
    assert "about 0.5 km" in text and "0.6" not in text  # 0.6 km away, shown only as 0.5 km


def test_accepting_responder_gets_a_directions_link(fake_telegram):
    sos_id, (asha,) = _start_sos(responders=[("Asha", 222)])
    service.respond(sos_id, asha, "accept")
    asyncio.run(REAL["responder_accepted"](sos_id, asha))
    text = next(p["text"] for m, p in fake_telegram if m == "sendMessage" and p["chat_id"] == 222)
    assert f"google.com/maps/dir/?api=1&destination={HER['lat']:.6f},{HER['lng']:.6f}" in text


def test_demo_location_is_rejected(client):
    r = client.post("/sos", json={"ciphertext": enc(), "location_source": "demo"})
    assert r.status_code == 422


def test_links_use_the_public_address_not_localhost(client):
    import app.main as main

    client.get("/health", headers={"host": "abc-def.trycloudflare.com"})  # app opened via the tunnel
    # ...then the SOS is triggered from the laptop at localhost.
    sos_id = client.post("/sos", json={"ciphertext": enc()}, headers={"host": "localhost:8000"}).json()["sos_id"]
    assert service.get_sos(sos_id)["base_url"] == "https://abc-def.trycloudflare.com"
    main._last_public_base = None


def test_she_can_clear_sos_history_but_not_while_sos_is_on(client, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    sid = send_sos(client).json()["sos_id"]
    assert client.delete("/sos/history").status_code == 409
    client.post(f"/sos/{sid}/cancel")
    assert client.delete("/sos/history").status_code == 200
    assert client.get(f"/sos/{sid}").status_code == 404


def test_responder_can_leave_the_network(client):
    rid = responder(client, "Asha")
    sid = send_sos(client).json()["sos_id"]
    assert client.delete(f"/people/{rid}").status_code == 200
    assert all(r["id"] != rid for r in client.get("/responders").json()["responders"])
    assert all(r["name"] != "Asha" for r in client.get(f"/sos/{sid}").json()["responders_alerted"])


def test_pressing_again_alerts_everyone_again(fake_telegram):
    sos_id, (asha,) = _start_sos(contacts=[("Mom", 111)], responders=[("Asha", 222)])
    asyncio.run(REAL["sos_started"](sos_id))
    first = len(fake_telegram)
    p = people.create("contact", "Riya")  # added after the first press
    people.link_telegram(p["link_code"], 333)
    again = service.trigger("u", HER, "manual", "gps", "https://example.trycloudflare.com")
    assert again["sos_id"] == sos_id and again["event"] == "repeated"
    asyncio.run(REAL["sos_repeated"](sos_id))
    texts = {p["chat_id"]: p["text"] for m, p in fake_telegram[first:] if m == "sendMessage"}
    assert "pressed SOS again" in texts[111]
    assert "SOS nearby" in texts[222]  # still pending: alert with Accept again
    assert "SOS from" in texts[333]  # new contact gets the full first alert


def test_reset_everything_ends_sos_and_keeps_people(client, fresh):
    rid = responder(client, "Asha")
    sid = send_sos(client).json()["sos_id"]
    out = client.post("/reset").json()
    assert out["ended_sos"]
    assert any(args == (sid,) for args in fresh)  # sos_ended ran, so live pins stop
    assert client.get("/sos/active").json()["active"] is None
    assert client.get(f"/sos/{sid}").status_code == 404
    assert client.get("/evidence").json()["records"] == []
    assert any(r["id"] == rid for r in client.get("/responders").json()["responders"])


def test_im_safe_from_any_screen_ends_the_running_sos(client, fresh):
    assert client.post("/sos/safe").json() == {"ended": False}
    sid = send_sos(client).json()["sos_id"]
    out = client.post("/sos/safe").json()
    assert out["ended"] and out["sos_id"] == sid
    assert client.get(f"/sos/{sid}").json()["status"] == "cancelled"
    assert client.get("/sos/active").json()["active"] is None


def test_responders_are_told_they_need_not_go(fake_telegram):
    sos_id, _ = _start_sos(contacts=[("Mom", 111)], responders=[("Asha", 222)])
    asyncio.run(REAL["sos_started"](sos_id))
    service.cancel(sos_id)
    asyncio.run(REAL["sos_ended"](sos_id))
    last = {p["chat_id"]: p["text"] for m, p in fake_telegram if m == "sendMessage"}
    assert "don't need to go" in last[222]
    assert "marked herself safe" in last[111]


def test_coordinates_go_to_contacts_and_accepting_responders_only(fake_telegram):
    sos_id, (asha,) = _start_sos(contacts=[("Mom", 111)], responders=[("Asha", 222)])
    asyncio.run(REAL["sos_started"](sos_id))
    texts = lambda chat: [p["text"] for m, p in fake_telegram if m == "sendMessage" and p["chat_id"] == chat]
    assert any("12.935200, 77.624500" in t for t in texts(111))  # contact: exact coordinates
    assert not any("Coordinates" in t for t in texts(222))  # responder: approximate only
    service.respond(sos_id, asha, "accept")
    asyncio.run(REAL["responder_accepted"](sos_id, asha))
    assert any("12.935200, 77.624500" in t for t in texts(222))
