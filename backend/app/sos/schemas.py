from typing import Literal

from pydantic import BaseModel, Field


class SosIn(BaseModel):
    # RSA-OAEP(SHA-256) ciphertext of {"lat", "lng", "accuracy"} under GET /sos/key.
    ciphertext: str = Field(min_length=100, max_length=1000)
    trigger: Literal["long_press_decline", "manual"] = "long_press_decline"
    location_source: Literal["gps", "demo"] = "gps"


class LocationIn(BaseModel):
    ciphertext: str = Field(min_length=100, max_length=1000)


class RespondIn(BaseModel):
    responder_id: str = Field(max_length=40)
    action: Literal["accept", "decline"]


class PersonIn(BaseModel):
    kind: Literal["contact", "responder"]
    name: str = Field(min_length=1, max_length=60)
    phone: str = Field(default="", max_length=20)
    email: str = Field(default="", max_length=120)
    lat: float | None = None
    lng: float | None = None


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=120)
    opted_in: bool | None = None
    lat: float | None = None
    lng: float | None = None


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
