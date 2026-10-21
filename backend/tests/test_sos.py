import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config
from app.sos import crypto, db, notify, people, service

# The real senders, kept before the autouse fixture replaces them for endpoint tests.
REAL = {n: getattr(notify, n) for n in ("sos_started", "location_updated", "responder_accepted", "sos_ended")}

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

    for name in ("sos_started", "location_updated", "responder_accepted", "sos_ended"):
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
