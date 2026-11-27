import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import accounts
from app.sos import crypto, db, people
from app.vault import store
from app.vault.schemas import EvidenceIn

PW = "correct horse battery"
SHOT = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crypto, "_key", None)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "SMTP_USER", "")


def client(name=None):
    from app.main import app

    c = TestClient(app)
    if name:
        assert c.post("/auth/signup", json={"username": name, "password": PW}).status_code == 200
    return c


def test_passwords_are_stored_as_argon2id_hashes():
    accounts.signup("Priya", PW)
    row = db.one("SELECT * FROM users WHERE username='priya'")
    assert row["password_hash"].startswith("$argon2id$")
    assert PW not in row["password_hash"]
    # Same password, different salt: two hashes never match.
    accounts.signup("meera", PW)
    assert db.one("SELECT password_hash FROM users WHERE username='meera'")["password_hash"] != row["password_hash"]


def test_sessions_are_stored_hashed_and_cookie_is_httponly():
    c = client()
    res = c.post("/auth/signup", json={"username": "priya", "password": PW})
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    token = c.cookies.get("kavach_session")
    assert token and db.one("SELECT COUNT(*) AS n FROM sessions WHERE token_sha256=?", token)["n"] == 0


def test_weak_passwords_and_bad_usernames_are_refused():
    c = client()
    for username, password in [("priya", "short"), ("priya", "password123"), ("priya", "aaaaaaaaaaaa"),
                               ("a", PW), ("bad name!", PW)]:
        assert c.post("/auth/signup", json={"username": username, "password": password}).status_code == 400
    assert c.post("/auth/signup", json={"username": "priya", "password": PW}).status_code == 200
    assert client().post("/auth/signup", json={"username": "PRIYA", "password": PW}).status_code == 409


def test_login_logout_and_wrong_password():
    client("priya")
    c = client()
    assert c.get("/auth/me").status_code == 401
    wrong = c.post("/auth/login", json={"username": "priya", "password": "not the password"})
    nobody = c.post("/auth/login", json={"username": "nobody", "password": "not the password"})
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.json()["detail"] == nobody.json()["detail"]  # doesn't reveal which usernames exist
    assert c.post("/auth/login", json={"username": " Priya ", "password": PW}).status_code == 200
    assert c.get("/auth/me").json()["user"]["username"] == "priya"
    c.post("/auth/logout")
    assert c.get("/auth/me").status_code == 401
    assert c.get("/evidence").status_code == 401


def test_lockout_after_repeated_wrong_passwords():
    client("priya")
    c = client()
    for _ in range(accounts.MAX_FAILS):
        assert c.post("/auth/login", json={"username": "priya", "password": "guess guess"}).status_code == 401
    locked = c.post("/auth/login", json={"username": "priya", "password": PW})
    assert locked.status_code == 429  # even the right password waits out the lockout


def test_signed_out_visitors_are_sent_to_login_and_kept_out_of_data():
    c = client()
    res = c.get("/", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login.html"
    assert c.get("/login.html").status_code == 200
    for method, path in [("get", "/evidence"), ("get", "/sos/active"), ("post", "/reset"), ("get", "/people")]:
        assert getattr(c, method)(path).status_code == 401, path
    # Pages and endpoints that carry their own codes stay open.
    assert c.get("/responder.html").status_code == 200
    assert c.get("/health").status_code == 200
    assert "contacts" not in c.get("/sos/setup").json()


def test_each_account_only_sees_its_own_data():
    priya, meera = client("priya"), client("meera")
    body = {"platform": "Instagram", "conversation": "rahul_k_07",
            "messages": [{"ref": "1", "sender": "rahul", "text": "I know where you live", "flagged": True}],
            "screenshots": [{"kind": "original_upload", "data_url": SHOT}]}
    capture = priya.post("/evidence", json=body).json()["capture_id"]
    assert priya.post("/people", json={"kind": "contact", "name": "Mom", "email": "mom@example.com"}).status_code == 200
    priya.put("/sos/profile", json={"name": "Priya"})

    assert len(priya.get("/evidence").json()["records"]) == 2
    assert meera.get("/evidence").json()["records"] == []
    assert meera.get("/evidence").json()["verification"]["ok"]
    assert meera.post("/complaint", json={"capture_id": capture}).status_code == 404
    digest = next(r["screenshot_sha256"] for r in priya.get("/evidence").json()["records"] if r["kind"] == "screenshot")
    assert priya.get(f"/evidence/screenshot/{digest}").status_code == 200
    assert meera.get(f"/evidence/screenshot/{digest}").status_code == 404

    mom = priya.get("/people").json()["people"][0]
    assert meera.get("/people").json()["people"] == []
    assert meera.delete(f"/people/{mom['id']}").status_code == 404
    assert meera.get(f"/people/{mom['id']}/telegram").status_code == 404
    assert meera.get("/sos/setup").json()["name"] == "Someone you know"
    assert priya.get("/sos/setup").json()["name"] == "Priya"

    # Meera clearing her Vault leaves Priya's alone; each chain verifies on its own.
    meera.post("/evidence", json=body)
    assert meera.delete("/evidence").status_code == 200
    assert len(priya.get("/evidence").json()["records"]) == 2 and priya.get("/evidence/verify").json()["ok"]
    assert priya.get(f"/evidence/screenshot/{digest}").status_code == 200  # shared image file kept


def test_each_account_has_its_own_sos():
    priya, meera = client("priya"), client("meera")
    priya.post("/people", json={"kind": "contact", "name": "Mom"})
    loc = crypto.encrypt_location_for_tests({"lat": 12.93, "lng": 77.62, "accuracy": 20})
    sos_id = priya.post("/sos", json={"ciphertext": loc, "trigger": "long_press_decline"}).json()["sos_id"]
    assert priya.get("/sos/active").json()["active"]["sos_id"] == sos_id
    assert [c["name"] for c in priya.get(f"/sos/{sos_id}").json()["contacts_notified"]] == ["Mom"]
    assert meera.get("/sos/active").json()["active"] is None
    assert meera.get(f"/sos/{sos_id}").status_code == 404
    assert meera.post(f"/sos/{sos_id}/cancel").status_code == 404
    meera.post("/reset")
    assert priya.get("/sos/active").json()["active"]["sos_id"] == sos_id  # Meera's reset didn't touch it


def test_first_account_keeps_data_saved_before_accounts():
    store.save_capture(EvidenceIn(platform="WhatsApp", conversation="x",
                                  messages=[{"ref": "1", "sender": "x", "text": "old message"}]))
    people.create("contact", "Mom")
    people.set_her_name("Priya")
    first = client("priya")
    assert [r["message_text"] for r in first.get("/evidence").json()["records"]] == ["old message"]
    assert [p["name"] for p in first.get("/people").json()["people"]] == ["Mom"]
    assert first.get("/sos/setup").json()["name"] == "Priya"
    second = client("meera")
    assert second.get("/evidence").json()["records"] == [] and second.get("/people").json()["people"] == []


def test_email_login_and_full_profile():
    c = client()
    res = c.post("/auth/signup", json={
        "username": "ananya",
        "password": PW,
        "full_name": "Ananya Sharma",
        "email": "ananya@example.com",
        "phone": "+91 9876543210",
    })
    assert res.status_code == 200
    user = res.json()["user"]
    assert user["full_name"] == "Ananya Sharma"
    assert user["email"] == "ananya@example.com"

    # Log in using email instead of username
    c2 = client()
    login_res = c2.post("/auth/login", json={"username": "ananya@example.com", "password": PW})
    assert login_res.status_code == 200
    me = c2.get("/auth/me").json()["user"]
    assert me["username"] == "ananya"
    assert me["email"] == "ananya@example.com"
    assert me["phone"] == "+91 9876543210"


def test_password_recovery_and_reset():
    client("priya")
    c = client()
    # Forgot password
    forgot = c.post("/auth/forgot-password", json={"email_or_username": "priya"}).json()
    assert forgot["ok"] is True
    token = forgot.get("reset_token")
    assert token

    # Reset password with invalid token
    bad = c.post("/auth/reset-password", json={"token": "invalid-token", "new_password": "new password 123"})
    assert bad.status_code == 400

    # Reset with valid token
    NEW_PW = "brand new secure passphrase"
    ok = c.post("/auth/reset-password", json={"token": token, "new_password": NEW_PW})
    assert ok.status_code == 200

    # Old password no longer works
    assert c.post("/auth/login", json={"username": "priya", "password": PW}).status_code == 401
    # New password works
    assert c.post("/auth/login", json={"username": "priya", "password": NEW_PW}).status_code == 200


def test_app_passcode_and_account_deletion():
    priya = client("priya")
    # Setting invalid passcode
    assert priya.post("/auth/passcode/set", json={"passcode": "abc"}).status_code == 400
    assert priya.post("/auth/passcode/set", json={"passcode": "12"}).status_code == 400

    # Setting valid 4-digit PIN
    set_pin = priya.post("/auth/passcode/set", json={"passcode": "4826"})
    assert set_pin.status_code == 200
    assert priya.get("/auth/me").json()["user"]["has_passcode"] is True

    # Verifying passcode
    assert priya.post("/auth/passcode/verify", json={"passcode": "0000"}).status_code == 401
    assert priya.post("/auth/passcode/verify", json={"passcode": "4826"}).status_code == 200

    # Deleting account
    del_res = priya.delete("/auth/me")
    assert del_res.status_code == 200
    assert priya.get("/auth/me").status_code == 401

