from typing import Literal

from pydantic import BaseModel, Field


class EvidenceMessage(BaseModel):
    ref: str = Field(max_length=120)  # the chat's own message id
    sender: str = Field(max_length=80)
    text: str = Field(max_length=4000)
    time: str = Field(default="", max_length=40)  # as displayed in the chat
    flagged: bool = False


class EvidenceScreenshot(BaseModel):
    kind: Literal["chat_capture", "original_upload"]
    # PNG/JPEG data URL. ~6 MB of base64 per image is plenty for a phone screenshot.
    data_url: str = Field(max_length=8_000_000)


class EvidenceIn(BaseModel):
    platform: str = Field(max_length=60)
    conversation: str = Field(max_length=120)
    url: str | None = Field(default=None, max_length=500)
    messages: list[EvidenceMessage] = Field(min_length=1, max_length=300)
    screenshots: list[EvidenceScreenshot] = Field(default_factory=list, max_length=10)
    # The Smart Shield verdict at the time of saving.
    analysis: dict | None = None
    # The device clock is recorded but never trusted; the server timestamp is the one hashed.
    client_captured_at: str | None = Field(default=None, max_length=40)


class TamperIn(BaseModel):
    record_id: int | None = None  # default: a flagged message near the middle of the chain
    rehash: bool = False  # True = a smarter attacker who also recomputes that record's hash
