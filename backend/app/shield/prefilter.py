"""Fast keyword/regex pre-filter.

Two jobs:
1. Short-circuit chats with zero risk signals so they never reach the LLM
   (cheaper, faster, and less private text sent to the cloud).
2. Produce a deterministic verdict used as the fallback when the LLM is
   unavailable or returns invalid JSON twice.

Patterns cover English, Hinglish (romanised Hindi) and Kannada-English.
They are deliberately broad: a false positive only costs one LLM call,
a false negative skips the LLM entirely.
"""

import re
from dataclasses import dataclass

from app.schemas import Message, ThreatResult

# (pattern, weight). Weights are rough points toward a 0-100 risk score.
_RULES: dict[str, list[tuple[str, int]]] = {
    "sextortion_blackmail": [
        (r"\b(nude|nudes|naked|private (pics?|photos?|videos?)|intimate)\b", 30),
        (r"\b(leak|viral|post|share|send)\b.{0,30}\b(pics?|photos?|videos?|vid|chats?)\b", 30),
        (r"\b(pics?|photos?|videos?)\b.{0,30}\b(leak|viral|post|share)\b", 30),
        (r"\b(viral|leak) kar (dunga|dungi|denge)\b", 40),
        (r"\b(viral|leak) maad(thini|tini|tene|bidthini)\b", 40),
        (r"\b(pics?|photos?|videos?|vids?)\b.{0,40}\b(daal dunga|daal dungi|dikhaunga|dikha dunga|bhej dunga|haakthini|haaktini|haakbidthini|kalisthini|kalsthini|toristhini)\b", 40),
        (r"\b(pay|send|transfer)\b.{0,25}\b(or|warna|illandre|illa andre)\b", 30),
        (r"\b(paise|paisa|money|rs\.?|₹|\d{3,}\s?(rs|rupees))\b.{0,40}\b(warna|or else|illandre)\b", 30),
        (r"\b(your|tera|teri|tumhara|ninna) (family|parents|ghar wale|papa|appa|amma)\b.{0,40}\b(show|send|dikha|tell|bata|helthini)\b", 30),
        (r"\bblackmail\b", 35),
    ],
    "threat_of_violence": [
        (r"\b(kill|murder|stab|shoot|rape|acid|beat you|hurt you|finish you)\b", 40),
        (r"\b(maar (dunga|dalunga|daalunga|denge)|jaan se maar|khatam kar)\b", 45),
        (r"\b(kolthini|kollthini|kolthivi|hodithini|hodeethini|saaysthini)\b", 45),
        (r"\b(dekh lunga|dekh lena|chhodunga nahi|nahi chhodunga|bidalla|bidolla|batata hoon|bataunga|nodkothini|nodkotini)\b", 25),
        (r"\b(you('| wi)ll regret|watch your back|you('re| are) dead)\b", 30),
    ],
    "stalking": [
        (r"\b(i know|mujhe pata|nanage gottu)\b.{0,30}\b(where you (live|stay|are)|tera ghar|your house|ninna mane|address)\b", 35),
        (r"\b(tera ghar|ninna mane|your house|your office|your college|your hostel)\b", 15),
        (r"\b(following you|followed you|saw you|dekha tujhe|nodide)\b", 20),
        (r"\b(outside|near|below|in front of) your (house|home|building|flat|pg|hostel|office|college)\b", 30),
        (r"\b(wearing|pehna|haakidde)\b.{0,30}\b(today|aaj|ivattu)\b", 25),
        (r"\b(why (are|r) you (ignoring|not replying)|reply (karo|kar|maadu)|block (mat|madbeda))\b", 15),
        (r"\b(new account|another account|naya account|block kiya toh)\b", 20),
    ],
    "harassment": [
        (r"\b(slut|whore|bitch|randi|chutiya|kutti|kamini|sule|bosudi|gandu)\b", 35),
        (r"\b(ugly|worthless|nobody wants you|kill yourself|kys)\b", 25),
        (r"\b(send (me )?(pics?|photos?)|pic bhej|photo bhej|photo kalsu)\b", 20),
        (r"\b(sexy|hot body|figure|maal)\b", 15),
    ],
    "impersonation": [
        (r"\b(fake (profile|account|id)|pretending to be|made an account (in|with) your name)\b", 35),
        (r"\b(your (photos?|pics?|name) (on|in) (a |another )?(profile|account|id))\b", 30),
        (r"\b(i am|this is|main)\b.{0,15}\b(police|cyber cell|officer|inspector|cbi|customs)\b", 30),
    ],
    "scam": [
        (r"\b(otp|kyc|cvv|upi pin|atm pin|bank details|account (blocked|suspended))\b", 30),
        (r"\b(lottery|lucky draw|you (have |\'ve )?won|prize|cashback|refund|job offer|work from home)\b", 20),
        (r"\b(click (this|the) link|verify (now|immediately)|limited time|urgent)\b", 20),
        (r"https?://\S*(bit\.ly|tinyurl|\.xyz|\.top|\.click|-verify|-kyc)\S*", 30),
        (r"\b(digital arrest|parcel (seized|held)|drugs in your (parcel|courier))\b", 40),
    ],
}

# Gate-only signals: not enough to score a threat, but enough that the chat must
# not be waved through as "clearly safe" without the LLM looking at it.
_ATTENTION = re.compile(
    r"\b(pics?|photos?|videos?|vids?|selfies?|dp|screenshots?)\b"
    r"|\b(your|tera|teri|tere|ninna|nimma) (house|home|ghar|mane|building|room|hostel|pg|office|college|family|parents?|mom|dad|amma|appa|papa|mummy)\b"
    r"|\b(amma|appa|papa|mummy|parents?|family) ?(ge|ko|ki|ke)\b"
    r"|\b(come down|come out|bahar (aa|nikal)|horage baa|waiting for you|i'?m outside)\b"
    r"|\b(warna|illandre|illa andre|otherwise|or else|regret|pachtayegi|pachtaogi)\b"
    r"|\b(daal dunga|dikhaunga|bataunga|batata hoon|haakthini|kalisthini|nodkothini|bidalla)\b"
    r"|\b(money|paise|paisa|pay|transfer|upi|gpay|phonepe|paytm|link|account)\b",
    re.IGNORECASE,
)

_COMPILED = {
    cat: [(re.compile(p, re.IGNORECASE), w) for p, w in rules] for cat, rules in _RULES.items()
}


# Text aimed at the classifier rather than at the victim (prompt injection).
_INJECTION = re.compile(
    r"\b(ignore|disregard|forget) (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|prompts?)\b"
    r"|^\s*(system|assistant|developer)\s*:"
    r"|\byou are now\b|\bnew instructions\b"
    r"|\b(respond|reply|answer|output) with\b.{0,40}\b(safe|risk_score|category)\b"
    r"|\b(classify|mark|label) (this|the chat|it) as safe\b",
    re.IGNORECASE | re.MULTILINE,
)


def find_injection_ids(messages: list[Message], self_handle: str) -> list[str]:
    return [m.id for m in messages if m.sender != self_handle and _INJECTION.search(m.text)]


@dataclass
class PrefilterResult:
    is_clearly_safe: bool
    verdict: ThreatResult
    hits: dict[str, list[str]]  # category -> message ids that matched


def severity_for(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def run_prefilter(messages: list[Message], self_handle: str) -> PrefilterResult:
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}

    for msg in messages:
        if msg.sender == self_handle:
            continue  # the user's own replies are never the threat
        for cat, rules in _COMPILED.items():
            for pattern, weight in rules:
                if pattern.search(msg.text):
                    scores[cat] = scores.get(cat, 0) + weight
                    if msg.id not in hits.setdefault(cat, []):
                        hits[cat].append(msg.id)

    if not scores:
        attention = [
            m.id for m in messages if m.sender != self_handle and _ATTENTION.search(m.text)
        ]
        if attention:
            return PrefilterResult(
                is_clearly_safe=False,
                verdict=ThreatResult(
                    category="harassment",
                    risk_score=30,
                    severity="low",
                    reason="Some messages contain words that can signal risk, but no clear threat pattern "
                    "was matched. Review them yourself. (Rule-based result; AI analysis unavailable.)",
                    flagged_message_ids=attention,
                ),
                hits={},
            )
        return PrefilterResult(
            is_clearly_safe=True,
            verdict=ThreatResult(
                category="safe",
                risk_score=5,
                severity="low",
                reason="No harassment, threat, blackmail, stalking, impersonation or scam signals found.",
                flagged_message_ids=[],
            ),
            hits={},
        )

    top = max(scores, key=lambda c: scores[c])
    # Escalation: signals spread across several messages are worse than one.
    spread_bonus = 10 * max(0, len(hits[top]) - 1)
    score = min(95, scores[top] + spread_bonus)
    flagged = sorted({mid for ids in hits.values() for mid in ids}, key=_order(messages))
    others = [c for c in scores if c != top]
    reason = (
        f"Keyword scan matched {top.replace('_', ' ')} patterns in {len(hits[top])} message(s)"
        + (f", plus {', '.join(o.replace('_', ' ') for o in others)} signals" if others else "")
        + ". (Rule-based result; AI analysis unavailable.)"
    )
    return PrefilterResult(
        is_clearly_safe=False,
        verdict=ThreatResult(
            category=top,  # type: ignore[arg-type]
            risk_score=score,
            severity=severity_for(score),  # type: ignore[arg-type]
            reason=reason,
            flagged_message_ids=flagged,
        ),
        hits=hits,
    )


def _order(messages: list[Message]):
    index = {m.id: i for i, m in enumerate(messages)}
    return lambda mid: index.get(mid, 0)
