from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "harassment",
    "threat_of_violence",
    "sextortion_blackmail",
    "stalking",
    "impersonation",
    "scam",
    "safe",
]
Severity = Literal["low", "medium", "high"]

CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]


class Message(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    sender: str = Field(min_length=1, max_length=80)
    text: str = Field(max_length=4000)
    timestamp: str | None = None


class AnalyzeRequest(BaseModel):
    platform: str = Field(default="mock-chat", max_length=40)
    # The user's own handle, so the model knows whose messages are incoming.
    self_handle: str = Field(default="me", max_length=80)
    messages: list[Message] = Field(min_length=1, max_length=200)


class ThreatResult(BaseModel):
    """The strict JSON contract from the PRD."""

    category: Category
    risk_score: int = Field(ge=0, le=100)
    severity: Severity
    reason: str = Field(min_length=1, max_length=600)
    flagged_message_ids: list[str]


class AnalyzeResponse(ThreatResult):
    # Metadata so the panel (and judges) can see which path produced the verdict.
    source: Literal["prefilter", "llm", "keyword_fallback"]
    pii_masked: int
    model: str | None = None
