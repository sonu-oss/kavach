from typing import Literal

from pydantic import BaseModel, Field


class TrustedContact(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    phone: str = Field(default="", max_length=20)


class SosIn(BaseModel):
    # RSA-OAEP(SHA-256) ciphertext of {"lat", "lng", "accuracy"} under GET /sos/key.
    ciphertext: str = Field(min_length=100, max_length=1000)
    trusted_contacts: list[TrustedContact] = Field(default_factory=list, max_length=5)
    trigger: Literal["long_press_decline", "manual"] = "long_press_decline"
    location_source: Literal["gps", "demo"] = "gps"


class RespondIn(BaseModel):
    responder_id: str = Field(max_length=40)
    action: Literal["accept", "decline"]
