"""Complaint letter PDF: her edited letter, the cited sections with confidence, and an evidence index."""

import io
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.complaint.drafting import _STATUS_NAME, SELF
from app.complaint.schemas import ComplaintPdfIn
from app.vault import store
from app.vault.report import LINE, MUTED, S, ist, p, pdf_text

# Letter text is free-form (she can edit it), so structure is read from the lines themselves:
# a short first line ending in ":" is a heading, "1. " / "- " lines are list items.
_HEADING = ParagraphStyle("lh", parent=S["body"], fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=2)
_ITEM = ParagraphStyle("li", parent=S["body"], leftIndent=14, firstLineIndent=-14, spaceAfter=1.5)
_LIST = re.compile(r"^(\d+\.|-)\s+")
FLAG_BG = colors.HexColor("#fdecec")


def _letter(text: str) -> list:
    story = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [ln for ln in block.split("\n")]
        if len(lines) > 1 and lines[0].rstrip().endswith(":") and len(lines[0]) < 140:
            story.append(Paragraph(pdf_text(lines[0]), _HEADING))
            lines = lines[1:]
        plain = []
        for ln in lines:
            m = _LIST.match(ln)
            if m:
                if plain:
                    story.append(Paragraph("<br/>".join(plain), S["body"]))
                    plain = []
                marker = m.group(1) if m.group(1) != "-" else "•"
                story.append(Paragraph(f"{pdf_text(marker)}&nbsp;&nbsp;{pdf_text(ln[m.end():])}", _ITEM))
            else:
                plain.append(pdf_text(ln))
        if plain:
            story.append(Paragraph("<br/>".join(plain), S["body"]))
        story.append(Spacer(1, 7))
    return story


def _sections_annex(sections) -> list:
    rows = [[p("Section", "head"), p("Title", "head"), p("Confidence", "head")]]
    for s in sections:
        note = " · consult a lawyer to confirm" if s.low_confidence else ""
        act = "BNS, 2023" if s.act.startswith("Bharatiya") else "IT Act, 2000"
        rows.append([p(f"{pdf_text(s.section)}, {act}", "cell"), p(pdf_text(s.title), "cell"),
                     p(f"{round(s.confidence * 100)}%{note}", "cell")])
    t = Table(rows, colWidths=[35 * mm, 85 * mm, 50 * mm], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f3f8")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [KeepTogether([p("Annex A: legal sections cited", "h3"), t])]


def _evidence_annex(capture_id: str, status: dict) -> list:
    rows = [r for r in store.all_rows() if r["capture_id"] == capture_id]
    if not rows:
        return []
    conv = (rows[0]["conversation"] or "").strip()
    other = conv if conv and not _STATUS_NAME.match(conv) else "Unknown account"
    shots = [r for r in rows if r["kind"] == "screenshot"]
    msgs = [r for r in rows if r["kind"] == "message"]

    data = [[p(h, "head") for h in ("Ref", "Time", "From", "Item", "SHA-256 (first 16)", "Check")]]
    flagged_rows = []
    for r in shots + msgs:
        if r["kind"] == "screenshot":
            who, item = "", ("Chat screenshot captured by Kavach" if r["screenshot_kind"] == "chat_capture"
                             else "Original screenshot from the complainant")
        else:
            who = "Complainant" if r["sender"] == SELF else other
            item = f"“{r['message_text']}”" + ("  [flagged]" if r["flagged"] else "")
            if r["flagged"]:
                flagged_rows.append(len(data))
        ok = status.get(r["id"]) == "ok"
        data.append([p(f"E{r['id']}", "cell"), p(pdf_text(r["message_time"] or "–"), "cell"), p(pdf_text(who), "cell"),
                     p(pdf_text(item), "cell"), p(r["hash"][:16], "mono"), p("Intact" if ok else "ALTERED", "cell")])
    t = Table(data, colWidths=[11 * mm, 18 * mm, 25 * mm, 70 * mm, 30 * mm, 16 * mm], repeatRows=1)
    style = [("GRID", (0, 0), (-1, -1), 0.4, LINE),
             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f3f8")),
             ("VALIGN", (0, 0), (-1, -1), "TOP")]
    style += [("BACKGROUND", (0, i), (-1, i), FLAG_BG) for i in flagged_rows]
    t.setStyle(TableStyle(style))
    first = rows[0]
    meta = (f"Platform: {pdf_text(first['platform'])} · Conversation: {pdf_text(other)} · "
            f"Sealed: {ist(first['server_timestamp'])} · Times are as shown in the chat; “–” means the app did not show one. "
            f"Flagged rows were marked as threatening or abusive by Kavach.")
    return [Spacer(1, 6), p("Annex B: evidence index", "h3"), p(meta, "small"), Spacer(1, 4), t]


def build_complaint_pdf(body: ComplaintPdfIn) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="Cybercrime complaint", author="Kavach AI",
    )
    v = store.verify()
    status = {r["id"]: r["status"] for r in v["records"]}

    story = _letter(body.letter)
    if body.sections:
        story += [Spacer(1, 6)] + _sections_annex(body.sections)
    story += _evidence_annex(body.capture_id, status)
    story += [
        Spacer(1, 10),
        p(f"Evidence: sealed in the Kavach Evidence Vault (capture {pdf_text(body.capture_id)}). "
          f"Chain status at export: {'VERIFIED' if v['ok'] else 'BROKEN, see evidence report'}. "
          f"Chain head SHA-256:", "small"),
        p(v["head_hash"] or "n/a", "mono"),
        Spacer(1, 4),
        p("Drafted with Kavach AI. Legal sections are suggestions retrieved from a curated legal corpus and should be "
          "confirmed by the police or a lawyer. This is not legal advice.", "small"),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 9 * mm, "Cybercrime complaint · drafted with Kavach AI · not legal advice")
    canvas.drawRightString(A4[0] - 20 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()
