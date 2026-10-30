import pytest

from app import vectors


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    # The real embedding model is never loaded in tests; tests that need vectors patch in fake_embed.
    monkeypatch.setattr(vectors, "_failed", True)


def signed_in(client, username="tester"):
    """Sign a TestClient up (which also signs it in), and make direct calls in the test act as the same account."""
    from app.auth import context

    res = client.post("/auth/signup", json={"username": username, "password": "correct horse battery"})
    assert res.status_code == 200, res.text
    context.set_current(res.json()["user"]["id"])
    return client


@pytest.fixture(autouse=True)
def no_account_leaks():
    """Each test starts signed out."""
    from app.auth import context

    token = context.set_current(None)
    yield
    context.reset(token)
