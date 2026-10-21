import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
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
from app.sos import notify, people
from app.sos import service as sos
from app.sos.schemas import LocationIn, PersonIn, PersonPatch, ProfileIn, RespondIn, SosIn
from app.vault import report, store
from app.vault.schemas import EvidenceIn, TamperIn

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

@asynccontextmanager
async def lifespan(_app):
    # Telegram bot: links contacts/responders and handles Accept / Decline buttons.
    poller = asyncio.create_task(notify.poll_forever())
    yield
    poller.cancel()


app = FastAPI(title="Kavach AI", version="0.1.0", lifespan=lifespan)

# Lets a browser extension or a separately served frontend call the API during the demo.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def remember_public_address(request, call_next):
    # Remember the https address the app was last opened on, for links sent from a laptop SOS.
    global _last_public_base
    base = _request_base(request)
    if base.startswith("https://") and not base.split("://", 1)[1].startswith(_LOCAL_HOSTS):
        _last_public_base = base
    return await call_next(request)


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
        "telegram_on": notify.telegram_on(),
        "email_on": notify.email_on(),
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


# ---------- Discreet SOS + Responder Network ----------

DEMO_USER = "demo-user"  # no accounts yet; real build: the signed-in user
_tasks: set = set()


def _background(coro) -> None:
    """Send alerts without making her wait; keep a reference so the task isn't collected.
    Only call from async endpoints: create_task needs the running event loop."""
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
_last_public_base: str | None = None


def _request_base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    if "trycloudflare.com" in host:  # the tunnel is always https, even if the header says otherwise
        proto = "https"
    return f"{proto}://{host}"


def _public_base(request: Request) -> str:
    """The address put into links we send out. Links must open on phones, so never localhost
    when the app has been reached over a public address (e.g. the https tunnel)."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    base = _request_base(request)
    is_local = base.split("://", 1)[1].startswith(_LOCAL_HOSTS)
    return (_last_public_base or base) if is_local else base


def _sos_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except sos.SosError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except people.PeopleError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def _decrypt(ciphertext: str) -> dict:
    try:
        return sos_crypto.decrypt_location(ciphertext)
    except Exception as exc:
        raise HTTPException(400, "could not decrypt location") from exc


@app.get("/sos/key")
def sos_key():
    """RSA-OAEP public key the browser uses to encrypt her location before sending it."""
    return {"alg": "RSA-OAEP-256", "spki": sos_crypto.public_key_spki_b64()}


@app.get("/sos/setup")
async def sos_setup():
    """What the SOS setup screen needs: her name, contacts, and which alert channels work."""
    return {
        "name": people.her_name(),
        "contacts": people.list_kind("contact"),
        "telegram": {"on": notify.telegram_on(), "bot": await notify.bot_username()},
        "email_on": notify.email_on(),
        "radius_m": sos.RADIUS_M,
    }


@app.put("/sos/profile")
def sos_profile(body: ProfileIn):
    return {"name": people.set_her_name(body.name)}


_EVENT = {"started": "sos_started", "first_fix": "location_found", "updated": "location_updated"}


@app.post("/sos")
async def sos_trigger(body: SosIn, request: Request):
    loc = _decrypt(body.ciphertext) if body.ciphertext else None
    source = body.location_source if loc else "none"
    result = _sos_call(sos.trigger, DEMO_USER, loc, body.trigger, source, _public_base(request))
    _background(getattr(notify, _EVENT[result.pop("event")])(result["sos_id"]))
    return result


@app.get("/sos/active")
def sos_active():
    """Her running SOS, if any (declared before /sos/{sos_id} so it isn't taken as an id)."""
    return {"active": sos.active_for(DEMO_USER)}


@app.get("/sos/{sos_id}")
def sos_status(sos_id: str):
    return _sos_call(sos.summary, sos_id)


@app.post("/sos/{sos_id}/location")
async def sos_location(sos_id: str, body: LocationIn):
    """Live GPS from her phone while the SOS is active."""
    result = _sos_call(sos.update_location, sos_id, _decrypt(body.ciphertext))
    _background(notify.location_found(sos_id) if result["first_fix"] else notify.location_updated(sos_id))
    return result


@app.post("/sos/{sos_id}/cancel")
async def sos_cancel(sos_id: str):
    result = _sos_call(sos.cancel, sos_id)
    _background(notify.sos_ended(sos_id))
    return result


@app.get("/sos/track/{token}")
def sos_track(token: str):
    return _sos_call(sos.track, token)


@app.post("/sos/{sos_id}/respond")
async def responder_respond(sos_id: str, body: RespondIn):
    result = _sos_call(sos.respond, sos_id, body.responder_id, body.action)
    if result["status"] == "accepted" and result["changed"]:
        _background(notify.responder_accepted(sos_id, body.responder_id))
    return result


@app.post("/sos/demo/reset")
def sos_demo_reset():
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    sos.reset()
    return {"ok": True}


# ---------- people: her trusted contacts and registered responders ----------

@app.get("/people")
def people_list(kind: str = "contact"):
    return {"people": people.list_kind(kind)}


@app.post("/people")
async def people_create(body: PersonIn):
    p = _sos_call(people.create, body.kind, body.name, body.phone, body.email, body.lat, body.lng)
    return {**p, "telegram_link": await notify.link_for(p)}


@app.patch("/people/{pid}")
def people_update(pid: str, body: PersonPatch):
    return _sos_call(people.update, pid, **body.model_dump())


@app.delete("/people/{pid}")
def people_delete(pid: str):
    _sos_call(people.delete, pid)
    return {"ok": True}


@app.get("/people/{pid}/telegram")
async def people_telegram(pid: str):
    p = _sos_call(people.get, pid)
    return {"link": await notify.link_for(p), "linked": p["telegram_chat_id"] is not None}


@app.post("/people/{pid}/verify")
def people_verify(pid: str, verified: bool = True):
    """Admin approval of a responder. Demo mode only until there are real admin accounts."""
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    return _sos_call(people.verify, pid, verified)


@app.get("/responders")
def responders():
    return {"responders": people.list_kind("responder"), "radius_m": sos.RADIUS_M}


@app.get("/responders/{responder_id}/alerts")
def responder_alerts(responder_id: str):
    return {"alerts": _sos_call(sos.alerts_for, responder_id)}


# Mounted last so API routes take precedence over static files.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
