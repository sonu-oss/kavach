from datetime import date

from pydantic import BaseModel, Field

from app.vault.schemas import EvidenceScreenshot


class AgentMessage(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    sender: str = Field(min_length=1, max_length=80)
    text: str = Field(max_length=4000)
    time: str = Field(default="", max_length=40)


class AgentConversation(BaseModel):
    """The chat she has open, as shown on her screen."""

    name: str = Field(min_length=1, max_length=120)
    platform: str = Field(default="ChatSpace (demo)", max_length=60)
    url: str | None = Field(default=None, max_length=500)
    incident_date: date | None = None
    messages: list[AgentMessage] = Field(min_length=1, max_length=200)
    # Captured by the page; sealed only if the agent decides to save the chat.
    screenshots: list[EvidenceScreenshot] = Field(default_factory=list, max_length=10)


class AgentIn(BaseModel):
    goal: str = Field(min_length=1, max_length=500)
    self_handle: str = Field(default="me", max_length=80)
    conversation: AgentConversation | None = None
