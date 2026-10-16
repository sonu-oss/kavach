import asyncio
import json

import pytest

from app import config
from app.complaint import drafting, retrieval
from app.complaint.drafting import check_citations
from app.llm import LLMError
from app.vault import store
from app.vault.schemas import EvidenceIn


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(retrieval, "_embed_failed", True)  # no model download in tests: BM25 + prior


def seal_sextortion() -> str:
    out = store.save_capture(EvidenceIn(
        platform="Instagram",
        conversation="rahul_k_07",
        messages=[
            {"ref": "1", "sender": "me", "text": "please stop messaging", "time": "21:20"},
            {"ref": "2", "sender": "rahul", "text": "10k bhej de UPI pe rahul.k07@okaxis warna tere photos viral kar dunga",
             "time": "21:23", "flagged": True},
            {"ref": "3", "sender": "rahul", "text": "call me on 9845012345 or your papa gets them", "time": "21:25",
             "flagged": True},
        ],
        analysis={"category": "sextortion_blackmail", "risk_score": 85, "severity": "high",
                  "reason": "Threatens to leak photos unless paid."},
    ))
    return out["capture_id"]


# ---------- corpus ----------

def test_corpus_entries_are_complete_and_unique():
    secs = retrieval.corpus()
    assert 15 <= len(secs) <= 30
    assert len({s["id"] for s in secs}) == len(secs)
    for s in secs:
        for key in ("act", "section", "title", "summary", "punishment", "keywords", "categories", "source"):
            assert s[key], (s["id"], key)
        assert s["id"].split("-", 1)[1] == s["section"]


def test_bm25_and_prior_find_the_obvious_sections():
    ids = [c.section["id"] for c in retrieval.retrieve("he keeps following me and knows my address", "stalking")]
    assert "BNS-78" in ids[:3]
    ids = [c.section["id"] for c in retrieval.retrieve("share OTP bank KYC link", "scam")]
    assert "BNS-318" in ids[:4]


# ---------- citation checker ----------

def test_unretrieved_tags_and_free_text_sections_are_removed():
    draft = {
        "incident_summary": "This is stalking under [BNS-78] and extortion under [BNS-308]. "
                            "It is also an offence under Section 354D IPC and u/s 66E of the IT Act.",
        "section_explanations": [{"id": "BNS-78", "how_it_applies": "repeated contact"},
                                 {"id": "BNS-999", "how_it_applies": "made up"}],
        "relief_sought": ["Act under Section 78 of the BNS"],
        "timeline": [],
    }
    out, dropped = check_citations(draft, {"BNS-78"})
    text = out["incident_summary"]
    assert "[BNS-78]" in text and "BNS-308" not in text
    assert "354D" not in text and "66E" not in text and "[citation removed]" in text
    assert [e["id"] for e in out["section_explanations"]] == ["BNS-78"]
    assert out["relief_sought"] == ["Act under Section 78 of the BNS"]  # allowed number stays
    assert {d["citation"] for d in dropped} >= {"BNS-308", "BNS-999"}
    assert len(dropped) == 4


def test_ipc_numbers_are_never_allowed_even_if_number_matches():
    out, dropped = check_citations({"incident_summary": "See IPC 78 and [BNS-78]."}, {"BNS-78"})
    assert "IPC" not in out["incident_summary"] and dropped


# ---------- pipeline ----------

def test_llm_sees_masked_text_and_only_cites_reranked_sections(monkeypatch):
    cid = seal_sextortion()
    seen = []

    async def fake_llm(system, payload, model, max_tokens=1500):
        seen.append(json.dumps(payload))
        if "ratings" in system:
            return {"ratings": [
                {"id": c["id"], "score": 90 if c["id"] in ("BNS-308", "BNS-351") else 50 if c["id"] == "BNS-78" else 10,
                 "why": "because"} for c in payload["candidates"]]}, model
        return {
            "subject": "Sextortion by rahul_k_07",
            "incident_summary": "The accused demanded money via [UPI_1] and threatened to leak my photos [BNS-308] "
                                "[BNS-351]. This is also IPC 384 and [ITA-67A].",
            "timeline": [{"when": "21:23", "what": "Demanded 10k", "evidence_ids": ["E2"]}],
            "section_explanations": [{"id": "BNS-308", "how_it_applies": "money demanded under threat"}],
            "relief_sought": ["Register FIR"],
            "portal_description": "The accused demanded money on UPI [UPI_1] and threatened me! #help",
        }, model

    monkeypatch.setattr(drafting, "groq_json_with_fallback", fake_llm)
    result = asyncio.run(drafting.build_complaint(cid))

    assert all("rahul.k07@okaxis" not in s and "9845012345" not in s for s in seen)  # masked for the LLM
    ids = [s["id"] for s in result["sections"]]
    assert ids[:2] == ["BNS-308", "BNS-351"] and "BNS-78" in ids  # 50 kept, flagged low confidence
    assert next(s for s in result["sections"] if s["id"] == "BNS-78")["low_confidence"]
    assert "ITA-67A" not in ids
    summary = result["complaint"]["incident_summary"]
    assert "Section 308 of the BNS, 2023" in summary  # humanised
    assert "rahul.k07@okaxis" in summary  # unmasked for her
    assert "67A" not in summary and "384" not in summary
    assert {d["citation"] for d in result["dropped_citations"]} >= {"ITA-67A"}
    portal = result["portal"]
    assert "#" not in portal["description"] and "!" not in portal["description"] and "@" not in portal["description"]
    assert "rahul.k07 at okaxis" in portal["description"]
    assert {"type": "UPI ID", "value": "rahul.k07@okaxis"} in portal["suspect_identifiers"]
    assert any(i["type"] == "Mobile number" for i in portal["suspect_identifiers"])
    assert [e["id"] for e in result["complaint"]["evidence"]] == ["E2", "E3"]  # flagged messages, sealed hashes
    assert result["complaint"]["evidence"][0]["description"].startswith("Message from rahul_k_07")
    assert result["pipeline"]["source"] == "llm"


def test_falls_back_to_template_when_llm_unavailable(monkeypatch):
    cid = seal_sextortion()

    async def down(*a, **k):
        raise LLMError("no key")

    monkeypatch.setattr(drafting, "groq_json_with_fallback", down)
    result = asyncio.run(drafting.build_complaint(cid))
    assert result["pipeline"]["source"] == "fallback"
    assert result["sections"] and all(s["low_confidence"] for s in result["sections"])
    assert result["complaint"]["incident_summary"]


def test_unknown_capture_is_rejected():
    with pytest.raises(drafting.ComplaintError):
        asyncio.run(drafting.build_complaint("doesnotexist"))


def test_complaint_pdf_renders():
    from app.complaint.pdf import build_complaint_pdf
    from app.complaint.schemas import ComplaintPdfIn

    cid = seal_sextortion()
    pdf = build_complaint_pdf(ComplaintPdfIn(
        capture_id=cid,
        letter="To,\nThe SHO\n\nSubject: test complaint about sextortion 🙂",
        sections=[{"id": "BNS-308", "act": "Bharatiya Nyaya Sanhita, 2023", "section": "308", "title": "Extortion",
                   "confidence": 0.9, "low_confidence": False}],
    ))
    assert pdf.startswith(b"%PDF")
