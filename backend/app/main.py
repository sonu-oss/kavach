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
from app.sos import crypto as sos_crypto
from app.sos import service as sos
from app.sos.schemas import RespondIn, SosIn
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


# ---------- Discreet SOS + Responder Network (prototype) ----------

DEMO_USER = "demo-user"  # no accounts in the prototype; real build: the signed-in user


def _sos_call(fn, *args):
    try:
        return fn(*args)
    except sos.SosError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.get("/sos/key")
def sos_key():
    """RSA-OAEP public key the browser uses to encrypt her location before sending it."""
    return {"alg": "RSA-OAEP-256", "spki": sos_crypto.public_key_spki_b64()}


@app.post("/sos")
def sos_trigger(body: SosIn):
    try:
        loc = sos_crypto.decrypt_location(body.ciphertext)
    except Exception as exc:
        raise HTTPException(400, "could not decrypt location") from exc
    contacts = [c.model_dump() for c in body.trusted_contacts]
    return _sos_call(sos.trigger, DEMO_USER, loc, contacts, body.trigger, body.location_source)


@app.get("/sos/active")
def sos_active():
    """Her running SOS, if any (declared before /sos/{sos_id} so it isn't taken as an id)."""
    return {"active": sos.active_for(DEMO_USER)}


@app.get("/sos/{sos_id}")
def sos_status(sos_id: str):
    return _sos_call(sos.status_for_victim, sos_id)


@app.post("/sos/{sos_id}/cancel")
def sos_cancel(sos_id: str):
    return _sos_call(sos.cancel, sos_id)


@app.get("/sos/track/{token}")
def sos_track(token: str):
    return _sos_call(sos.track, token)


@app.get("/responders")
def responders():
    return {"responders": sos.list_responders(), "radius_m": sos.RADIUS_M}


@app.get("/responders/{responder_id}/alerts")
def responder_alerts(responder_id: str):
    return {"alerts": _sos_call(sos.alerts_for, responder_id)}


@app.post("/sos/{sos_id}/respond")
def responder_respond(sos_id: str, body: RespondIn):
    return _sos_call(sos.respond, sos_id, body.responder_id, body.action)


@app.post("/sos/demo/reset")
def sos_demo_reset():
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    sos.reset()
    return {"ok": True}


# Mounted last so API routes take precedence over static files.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
