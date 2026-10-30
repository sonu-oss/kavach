import pytest

from app import vectors


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    # The real embedding model is never loaded in tests; tests that need vectors patch in fake_embed.
    monkeypatch.setattr(vectors, "_failed", True)
