import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import config
from app.schemas import AnalyzeRequest, AnalyzeResponse
from app.shield.classifier import analyze
from app.complaint.drafting import ComplaintError, build_complaint
from app.complaint.pdf import build_complaint_pdf
from app.complaint.retrieval import corpus
from app.complaint.schemas import ComplaintIn, ComplaintPdfIn
from app.vault import report, store
from app.vault.schemas import EvidenceIn, TamperIn

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Kavach AI", version="0.1.0")

# Lets a browser extension or a separately served frontend call the API during the demo.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def revalidate_static(request, call_next):
    # Without this, browsers heuristically cache app.js etc. and keep showing an old build.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/health")
def health():
    return {
        "ok": True,
        "llm_configured": bool(config.GROQ_API_KEY),
        "model": config.GROQ_MODEL,
        "demo_mode": config.DEMO_MODE,
    }


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_conversation(req: AnalyzeRequest) -> AnalyzeResponse:
    return await analyze(req)


# ---------- Evidence Vault ----------

@app.post("/evidence")
def save_evidence(item: EvidenceIn):
    try:
        return store.save_capture(item)
    except store.EvidenceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/evidence")
def list_evidence():
    rows = store.all_rows()
    return {"records": rows, "verification": store.verify(rows)}


@app.get("/evidence/verify")
def verify_evidence():
    return store.verify()


@app.get("/evidence/report")
def evidence_report():
    pdf = report.build_report()
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="kavach-evidence-report.pdf"'},
    )


@app.get("/evidence/screenshot/{digest}")
def evidence_screenshot(digest: str):
    path = store.screenshot_path(digest)
    if path is None:
        raise HTTPException(404, "screenshot not found")
    return FileResponse(path)


@app.post("/evidence/demo/tamper")
def demo_tamper(body: TamperIn):
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    try:
        return store.demo_tamper(body.record_id, body.rehash)
    except store.EvidenceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/evidence/demo/reset")
def demo_reset():
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    store.demo_reset()
    return {"ok": True}


# ---------- Complaint Assistant ----------

@app.post("/complaint")
async def draft_complaint(body: ComplaintIn):
    try:
        return await build_complaint(body.capture_id)
    except ComplaintError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/complaint/pdf")
def complaint_pdf(body: ComplaintPdfIn):
    return Response(
        build_complaint_pdf(body),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="kavach-complaint.pdf"'},
    )


@app.get("/legal/sections")
def legal_sections():
    """The whole corpus, so anyone can see exactly which laws the assistant is allowed to cite."""
    return {"sections": corpus()}


# Mounted last so API routes take precedence over static files.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
