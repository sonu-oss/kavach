from pydantic import BaseModel, Field


class ComplaintIn(BaseModel):
    capture_id: str = Field(min_length=6, max_length=40)


class CitedSection(BaseModel):
    id: str
    act: str
    section: str
    title: str
    confidence: float
    low_confidence: bool


class ComplaintPdfIn(BaseModel):
    capture_id: str = Field(max_length=40)
    letter: str = Field(min_length=20, max_length=30_000)  # the text as she edited it
    sections: list[CitedSection] = Field(default_factory=list, max_length=10)
