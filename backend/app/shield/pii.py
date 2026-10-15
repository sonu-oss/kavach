"""PII masking applied before any text leaves the backend for the cloud LLM.

Each distinct value gets a stable token (e.g. the same phone number is always
[PHONE_1] within one conversation) so the model can still reason about
"she keeps sharing the same number" without ever seeing the number.
"""

import re
from dataclasses import dataclass, field

# Order matters: emails before UPI IDs (both contain "@"), Aadhaar before phone.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("UPI", re.compile(r"\b[\w.-]{2,}@(?:ok)?[a-z]{2,}\b", re.IGNORECASE)),
    ("AADHAAR", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?!\w)")),
    (
        "ADDRESS",
        re.compile(
            r"(?:\b(?:house|flat|door|plot)\s*(?:no\.?|number|#)?\s*\d+[\w/-]*"
            r"|#\s?\d+[\w/-]*)"
            r"(?:[ ,]+[\w.'-]+){0,6}?"
            r"(?:[ ,]+(?:road|rd|street|st|cross|main|layout|nagar|colony|lane|block|sector|stage|phase)\b)"
            r"(?:[ ,]+[\w.'-]+){0,3}",
            re.IGNORECASE,
        ),
    ),
    ("PINCODE", re.compile(r"\b[1-9]\d{2}\s?\d{3}\b")),
]


@dataclass
class Masker:
    counts: dict[str, int] = field(default_factory=dict)
    seen: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.seen)

    def mask(self, text: str) -> str:
        for label, pattern in _PATTERNS:
            text = pattern.sub(lambda m, label=label: self._token(label, m.group(0)), text)
        return text

    def _token(self, label: str, value: str) -> str:
        norm = re.sub(r"[\s-]", "", value.lower())
        if label == "PHONE":
            norm = norm[-10:]  # +91 98450 12345 and 9845012345 are the same number
        key = (label, norm)
        if key not in self.seen:
            self.counts[label] = self.counts.get(label, 0) + 1
            self.seen[key] = f"[{label}_{self.counts[label]}]"
        return self.seen[key]
