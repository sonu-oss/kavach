"""Complaint letter PDF: her edited letter, the cited sections with confidence, and the evidence anchor."""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.complaint.schemas import ComplaintPdfIn
from app.vault import store
from app.vault.report import LINE, MUTED, S, p, pdf_text


def build_complaint_pdf(body: ComplaintPdfIn) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title="Cybercrime complaint", author="Kavach AI",
    )
    story = []
    for block in body.letter.replace("\r\n", "\n").split("\n\n"):
        html = pdf_text(block).replace("\n", "<br/>")
        story += [Paragraph(html, S["body"]), Spacer(1, 6)]

    if body.sections:
        story += [Spacer(1, 6), p("Annex: legal sections cited", "h3")]
        rows = [[p("Section", "head"), p("Title", "head"), p("Confidence", "head")]]
        for s in body.sections:
            note = " · consult a lawyer to confirm" if s.low_confidence else ""
            act = "BNS, 2023" if s.act.startswith("Bharatiya") else "IT Act, 2000"
            rows.append([p(f"{pdf_text(s.section)}, {act}", "cell"), p(pdf_text(s.title), "cell"),
                         p(f"{round(s.confidence * 100)}%{note}", "cell")])
        t = Table(rows, colWidths=[35 * mm, 95 * mm, 40 * mm], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f3f8")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)

    v = store.verify()
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
