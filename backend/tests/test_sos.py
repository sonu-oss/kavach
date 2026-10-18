import pytest
from fastapi.testclient import TestClient

from app import config
from app.sos import crypto, service

LOC = {"lat": 12.9352, "lng": 77.6245, "accuracy": 25}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "_key", None)
    service.reset()


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def send_sos(client, loc=LOC, contacts=({"name": "Mom", "phone": "98xxxxxx01"},)):
    return client.post("/sos", json={"ciphertext": crypto.encrypt_location_for_tests(loc),
                                     "trusted_contacts": list(contacts)})


def test_location_must_be_encrypted_with_the_server_key(client):
    assert client.get("/sos/key").json()["alg"] == "RSA-OAEP-256"
    bad = client.post("/sos", json={"ciphertext": "A" * 344})
    assert bad.status_code == 400


def test_only_verified_opted_in_nearby_responders_are_alerted(client):
    r = send_sos(client).json()
    alerted = {x["id"] for x in r["responders_alerted"]}
    assert alerted == {"r-asha", "r-campus", "r-meena"}
    reasons = {x["id"]: x["reason"] for x in r["responders_excluded"]}
    assert "not verified" in reasons["r-ravi"]
    assert "opted in" in reasons["r-kiran"]
    assert "outside" in reasons["r-farah"]
    assert r["contacts_notified"][0]["name"] == "Mom"


def test_responders_see_only_approximate_area_until_they_accept(client):
    sos_id = send_sos(client).json()["sos_id"]
    alert = client.get("/responders/r-asha/alerts").json()["alerts"][0]
    assert "exact" not in alert
    area = alert["approx_area"]
    assert (area["lat"], area["lng"]) != (LOC["lat"], LOC["lng"])
    assert service.haversine_m(area["lat"], area["lng"], LOC["lat"], LOC["lng"]) < area["radius_m"]

    client.post(f"/sos/{sos_id}/respond", json={"responder_id": "r-asha", "action": "accept"})
    accepted = client.get("/responders/r-asha/alerts").json()["alerts"][0]
    assert accepted["exact"]["lat"] == LOC["lat"]
    # ...and only to the one who accepted.
    assert "exact" not in client.get("/responders/r-campus/alerts").json()["alerts"][0]


def test_unalerted_responder_cannot_accept_or_see(client):
    sos_id = send_sos(client).json()["sos_id"]
    assert client.get("/responders/r-ravi/alerts").json()["alerts"] == []
    r = client.post(f"/sos/{sos_id}/respond", json={"responder_id": "r-ravi", "action": "accept"})
    assert r.status_code == 403


def test_track_link_shows_exact_location_until_she_is_safe(client):
    r = send_sos(client).json()
    token = r["track_path"].split("t=")[1]
    t = client.get(f"/sos/track/{token}").json()
    assert t["status"] == "active" and t["lat"] == LOC["lat"]
    client.post(f"/sos/{r['sos_id']}/cancel")
    ended = client.get(f"/sos/track/{token}").json()
    assert ended["status"] == "cancelled" and "lat" not in ended
    assert client.get("/sos/track/not-a-token").status_code == 404


def test_cancelled_sos_hides_exact_location_from_responders(client):
    sos_id = send_sos(client).json()["sos_id"]
    client.post(f"/sos/{sos_id}/respond", json={"responder_id": "r-asha", "action": "accept"})
    client.post(f"/sos/{sos_id}/cancel")
    assert "exact" not in client.get("/responders/r-asha/alerts").json()["alerts"][0]


def test_retrigger_updates_active_sos_and_rate_limit_applies(client):
    first = send_sos(client).json()
    again = send_sos(client, loc={**LOC, "lat": 12.94}).json()
    assert again["updated_existing"] and again["sos_id"] == first["sos_id"]
    client.post(f"/sos/{first['sos_id']}/cancel")
    for _ in range(service.RATE_LIMIT - 1):  # new alerts, each closed again
        client.post(f"/sos/{send_sos(client).json()['sos_id']}/cancel")
    blocked = send_sos(client)
    assert blocked.status_code == 429 and "112" in blocked.json()["detail"]


def test_out_of_range_location_rejected(client):
    assert send_sos(client, loc={"lat": 200, "lng": 0}).status_code == 400


def test_active_sos_can_be_recovered_after_a_reload(client):
    assert client.get("/sos/active").json()["active"] is None
    sos_id = send_sos(client).json()["sos_id"]
    assert client.get("/sos/active").json()["active"]["sos_id"] == sos_id
    client.post(f"/sos/{sos_id}/cancel")
    assert client.get("/sos/active").json()["active"] is None


def test_sos_expires_after_two_hours(client, monkeypatch):
    r = send_sos(client).json()
    token = r["track_path"].split("t=")[1]
    real = service.time.time
    monkeypatch.setattr(service.time, "time", lambda: real() + service.TRACK_TTL_S + 1)
    assert client.get("/sos/active").json()["active"] is None
    assert client.get("/responders/r-asha/alerts").json()["alerts"][0]["sos_status"] == "expired"
    assert client.get(f"/sos/track/{token}").status_code == 404
    # A new SOS is a fresh alert, not an update of the expired one.
    assert send_sos(client).json()["sos_id"] != r["sos_id"]


def test_responders_see_when_location_was_updated(client):
    first = send_sos(client).json()
    client.post(f"/sos/{first['sos_id']}/respond", json={"responder_id": "r-asha", "action": "accept"})
    send_sos(client, loc={**LOC, "lat": 12.9401})
    alert = client.get("/responders/r-asha/alerts").json()["alerts"][0]
    assert alert["exact"]["lat"] == 12.9401 and alert["updated_at"] >= alert["created_at"]
