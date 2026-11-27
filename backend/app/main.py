import asyncio
import logging
from contextlib import asynccontextmanager
from pydantic import BaseModel

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import config, database
from app.agent.guardian import run_agent
from app.agent.schemas import AgentIn
from app.auth import accounts, context
from app.auth.schemas import (
    ForgotPasswordIn,
    LoginIn,
    PasscodeSetIn,
    PasscodeVerifyIn,
    ProfileUpdateIn,
    ResetPasswordIn,
    SignupIn,
)
from app.schemas import AnalyzeRequest, AnalyzeResponse, AutoAlertRequest
from app.shield.classifier import analyze
from app.complaint.drafting import ComplaintError, build_complaint
from app.complaint.pdf import build_complaint_pdf
from app.complaint.retrieval import corpus
from app.complaint.schemas import ComplaintIn, ComplaintPdfIn
from app.sos import crypto as sos_crypto
from app.sos import notify, people
from app.sos import service as sos
from app.sos.schemas import LocationIn, PersonIn, PersonPatch, ProfileIn, RespondIn, SosIn
from app.vault import index as evidence_index
from app.vault import report, store
from app.vault.schemas import EvidenceIn, TamperIn
from app.security.router import router as security_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx logs every request URL, and Telegram API URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)

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


SESSION_COOKIE = "kavach_session"
# Pages that need an account. Responder, live-tracking and connect pages stay open: they carry their own codes.
_PRIVATE_PAGES = {"/", "/index.html"}


@app.middleware("http")
async def session(request: Request, call_next):
    """Reads the session cookie or Authorization Bearer header and sets the signed-in account for this request."""
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        auth_h = request.headers.get("Authorization", "")
        if auth_h.startswith("Bearer "):
            raw_token = auth_h[7:].strip()
        elif "X-Kavach-Token" in request.headers:
            raw_token = request.headers["X-Kavach-Token"].strip()

    user = accounts.user_for_session(raw_token) if raw_token else None
    if not user:
        user_header = request.headers.get("X-Kavach-User")
        if user_header:
            user = accounts.user_for_id(user_header)
    if not user and request.headers.get("X-Kavach-Client") == "extension":
        row = database.one("SELECT id, username FROM users ORDER BY created_at ASC LIMIT 1")
        if row:
            user = dict(row)
    if request.method == "GET" and request.url.path in _PRIVATE_PAGES and not user:
        return RedirectResponse("/login.html", status_code=303)
    token = context.set_current(user["id"] if user else None)
    try:
        return await call_next(request)
    finally:
        context.reset(token)


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and not request.url.path.startswith("/api"):
        not_found_file = config.FRONTEND_DIR / "404.html"
        if not_found_file.exists():
            return Response(content=not_found_file.read_text(), status_code=404, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": exc.detail or "Not Found"})


def require_user(request: Request = None) -> str:
    uid = context.current()
    if not uid and request:
        raw_token = request.cookies.get(SESSION_COOKIE)
        if not raw_token:
            auth_h = request.headers.get("Authorization", "")
            if auth_h.startswith("Bearer "):
                raw_token = auth_h[7:].strip()
            elif "X-Kavach-Token" in request.headers:
                raw_token = request.headers["X-Kavach-Token"].strip()
        if raw_token:
            u = accounts.user_for_session(raw_token)
            if u:
                uid = u["id"]
        if not uid:
            user_header = request.headers.get("X-Kavach-User")
            if user_header:
                u = accounts.user_for_id(user_header)
                if u:
                    uid = u["id"]
        if not uid and request.headers.get("X-Kavach-Client") == "extension":
            latest = accounts.latest_active_user()
            if latest:
                uid = latest["id"]
        if uid:
            context.set_current(uid)
    if not uid:
        raise HTTPException(401, "Please log in")
    return uid


signed_in = [Depends(require_user)]


# ---------- accounts ----------

def _set_session(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=accounts.SESSION_TTL_S, httponly=True, samesite="lax",
        secure=_request_base(request).startswith("https://"), path="/",
    )


@app.post("/auth/signup")
def auth_signup(body: SignupIn, request: Request):
    try:
        user = accounts.signup(
            username=body.username,
            password=body.password,
            full_name=body.full_name,
            email=body.email,
            phone=body.phone,
        )
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    token = accounts.new_session(user["id"])
    response = JSONResponse({"user": user, "token": token})
    _set_session(response, request, token)
    return response


@app.post("/auth/login")
def auth_login(body: LoginIn, request: Request):
    try:
        user, token = accounts.login(body.username, body.password)
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    response = JSONResponse({"user": user, "token": token})
    _set_session(response, request, token)
    return response


@app.post("/auth/logout")
def auth_logout(request: Request):
    accounts.logout(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/auth/callback")
def auth_callback_alias(request: Request):
    """Alias for /auth/google/callback — handles Google OAuth redirects regardless of which URI is registered."""
    from fastapi.responses import RedirectResponse
    # Preserve the full query string and fragment (fragment is client-side only, can't redirect it)
    # We serve the same HTML page — just reuse the google callback handler
    return auth_google_callback()


@app.get("/auth/google/callback")
def auth_google_callback():
    """Serves the client-side token receiver for Google OAuth2."""
    html_content = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Kavach — Authenticating with Google...</title>
  <style>
    body { background:#0a0c10; color:#fff; font-family:system-ui,-apple-system,sans-serif; display:flex; height:100vh; align-items:center; justify-content:center; margin:0; }
    .card { background: rgba(18, 24, 38, 0.85); border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 32px; max-width: 400px; width: 90%; text-align: center; box-shadow: 0 16px 40px rgba(0,0,0,0.5); }
    .spinner { display: inline-block; width: 36px; height: 36px; border: 3px solid rgba(255,255,255,0.1); border-top-color: #f2b8c6; border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .btn { display: inline-block; background: #ef4444; color: #fff; padding: 10px 18px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: 600; margin-top: 16px; }
  </style>
</head>
<body>
  <div class="card" id="card">
    <div class="spinner" id="spinner"></div>
    <div style="font-size:16px; font-weight:700; margin-bottom:6px;" id="title">Completing Google Authentication…</div>
    <div style="font-size:12.5px; color:#8c8c8c; line-height:1.5;" id="desc">Verifying your secure credentials with Kavach Safety...</div>
    <div id="err-box" style="display:none; margin-top:16px; color:#ef4444; font-size:13px; background:rgba(239,68,68,0.1); padding:10px; border-radius:6px; border:1px solid rgba(239,68,68,0.3);"></div>
  </div>
  <script>
    (async () => {
      const hash = window.location.hash.substring(1);
      const search = window.location.search.substring(1);
      const params = new URLSearchParams(hash || search);
      const token = params.get("access_token");
      const errBox = document.getElementById("err-box");
      const title = document.getElementById("title");
      const desc = document.getElementById("desc");
      const spinner = document.getElementById("spinner");

      function fail(msg) {
        spinner.style.display = "none";
        title.textContent = "Authentication Error";
        title.style.color = "#ef4444";
        desc.textContent = "Could not complete sign-in.";
        errBox.textContent = msg;
        errBox.style.display = "block";
        const btn = document.createElement("a");
        btn.href = "/login.html";
        btn.className = "btn";
        btn.textContent = "← Back to Login";
        document.getElementById("card").appendChild(btn);
      }

      if (!token) {
        const errorParam = params.get("error_description") || params.get("error");
        return fail(errorParam || "No access token found in Google callback.");
      }

      let email = "", full_name = "", sub = "";
      try {
        const uRes = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
          headers: { Authorization: "Bearer " + token }
        });
        if (uRes.ok) {
          const u = await uRes.json();
          email = u.email || "";
          full_name = u.name || "";
          sub = u.sub || "";
        }
      } catch (e) {
        // Ignored: Brave Shields might block cross-origin client fetch; backend verifies with httpx directly
      }

      try {
        desc.textContent = "Setting up your secure session in Supabase...";
        const res = await fetch("/auth/google/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ access_token: token, email, full_name, sub })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          return fail(body.detail || "Server rejected Google verification");
        }
        if (body.user && body.user.id) {
          try {
            localStorage.setItem("kavach.lastUser", body.user.id);
            localStorage.setItem("kavach.user", JSON.stringify(body.user));
            if (body.token) localStorage.setItem("kavach_token", body.token);
          } catch {}
        }
        title.textContent = "Success!";
        title.style.color = "#10b981";
        desc.textContent = "Redirecting to your Kavach Safety Dashboard...";
        setTimeout(() => {
          window.location.replace("/index.html");
        }, 300);
      } catch (err) {
        fail("Network error reaching Kavach server: " + err.message);
      }
    })();
  </script>
</body>
</html>"""
    return Response(content=html_content, media_type="text/html")


@app.post("/auth/google/verify")
async def auth_google_verify(request: Request):
    data = await request.json()
    token = data.get("access_token")
    email = data.get("email")
    full_name = data.get("full_name", "")
    sub = data.get("sub", "")

    # If email wasn't provided or access_token was passed, verify via Google API from backend
    if not email and token:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {token}"}
                )
                if resp.status_code == 200:
                    info = resp.json()
                    email = info.get("email")
                    full_name = info.get("name", "")
                    sub = info.get("sub", "")
                else:
                    raise HTTPException(400, f"Google token rejected: {resp.text}")
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(502, f"Failed to contact Google servers: {exc}") from exc

    if not email:
        raise HTTPException(400, "Missing email from Google profile")

    try:
        user = accounts.login_or_create_google(email, full_name, sub)
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc

    session_token = accounts.new_session(user["id"])
    response = JSONResponse({"user": user, "token": session_token})
    _set_session(response, request, session_token)
    return response


@app.get("/auth/me")
def auth_me(request: Request):
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        auth_h = request.headers.get("Authorization", "")
        if auth_h.startswith("Bearer "):
            raw_token = auth_h[7:].strip()
        elif "X-Kavach-Token" in request.headers:
            raw_token = request.headers["X-Kavach-Token"].strip()
    user = accounts.user_for_session(raw_token) if raw_token else None
    if not user and request.headers.get("X-Kavach-Client") == "extension":
        user = accounts.latest_active_user()
    if not user:
        raise HTTPException(401, "Please log in")

    user_id = user["id"]
    contacts = people.list_kind("contact", owner=user_id)
    return {
        "user": user,
        "guardians_count": len(contacts),
        "guardians_names": [c.get("name") for c in contacts if c.get("name")],
    }


@app.get("/auth/profile", dependencies=signed_in)
def get_user_profile():
    uid = require_user()
    u = accounts.user_for_id(uid)
    if not u:
        raise HTTPException(404, "User not found")
    return {"user": u}


@app.put("/auth/profile", dependencies=signed_in)
def update_user_profile(body: ProfileUpdateIn):
    uid = require_user()
    data = body.model_dump(exclude_unset=True)
    updated = accounts.update_profile(uid, **data)
    lat = data.get("lat")
    lng = data.get("lng")
    if lat is not None and lng is not None:
        user_email = (updated.get("email") or "").lower().strip()
        user_phone = (updated.get("phone") or "").strip()
        user_name = (updated.get("full_name") or updated.get("username") or "").lower().strip()
        for r in people.list_kind("responder"):
            r_email = (r.get("email") or "").lower().strip()
            r_phone = (r.get("phone") or "").strip()
            r_name = (r.get("name") or "").lower().strip()
            if (user_email and r_email == user_email) or (user_phone and r_phone == user_phone) or (user_name and r_name == user_name):
                people.update(r["id"], lat=lat, lng=lng)
                break
    return {"user": updated}


class LocationSyncIn(BaseModel):
    lat: float
    lng: float


@app.post("/location/sync")
def sync_location(body: LocationSyncIn, request: Request):
    """Sync live device location for user profile and auto-update responder and guardian location."""
    lat = body.lat
    lng = body.lng
    res = {"status": "synced", "lat": lat, "lng": lng, "responder_updated": False, "guardian_updated": False}
    curr_uid = _resolve_request_uid(request)
    user = accounts.user_for_id(curr_uid) if curr_uid else None
    if user:
        accounts.update_profile(user["id"], lat=lat, lng=lng)
        res["user_id"] = user["id"]
        user_email = (user.get("email") or "").lower().strip()
        user_phone = (user.get("phone") or "").strip()
        user_name = (user.get("full_name") or user.get("username") or "").lower().strip()
        for r in people.list_kind("responder"):
            r_owner = r.get("user_id") or r.get("owner")
            r_email = (r.get("email") or "").lower().strip()
            r_uemail = (r.get("user_email") or "").lower().strip()
            r_phone = (r.get("phone") or "").strip()
            r_name = (r.get("name") or "").lower().strip()
            if (curr_uid and r_owner == curr_uid) or (user_email and (r_email == user_email or r_uemail == user_email)) or (user_phone and r_phone == user_phone) or (user_name and r_name == user_name):
                people.update(r["id"], lat=lat, lng=lng)
                res["responder_updated"] = True
                res["responder_id"] = r["id"]
                res["responder_name"] = r.get("name")
                break
        for g in people.list_kind("contact"):
            g_email = (g.get("email") or "").lower().strip()
            g_phone = (g.get("phone") or "").strip()
            if (user_email and g_email == user_email) or (user_phone and g_phone == user_phone):
                people.update(g["id"], lat=lat, lng=lng)
                res["guardian_updated"] = True
                res["guardian_id"] = g["id"]
                res["guardian_name"] = g.get("name")
                break
    return res


@app.get("/api/location/detect")
async def detect_location(request: Request):
    """Network IP location detector when device geolocation is unavailable on insecure LAN contexts or WiFi."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://ip-api.com/json/")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success" and data.get("lat") and data.get("lon"):
                    return {
                        "lat": float(data["lat"]),
                        "lng": float(data["lon"]),
                        "label": f"{data.get('city', 'Bengaluru')}, {data.get('regionName', 'Karnataka')}",
                        "city": data.get("city", "Bengaluru"),
                        "region": data.get("regionName", "Karnataka"),
                        "is_fallback": False,
                        "source": "network_ip"
                    }
    except Exception as exc:
        logging.warning("Network IP location lookup: %s", exc)

    return {
        "lat": 12.9716,
        "lng": 77.5946,
        "label": "Bengaluru, Karnataka",
        "city": "Bengaluru",
        "region": "Karnataka",
        "is_fallback": True,
        "source": "default_coords"
    }


@app.post("/auth/forgot-password")
def auth_forgot_password(body: ForgotPasswordIn):
    try:
        return accounts.forgot_password(body.email_or_username)
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.post("/auth/reset-password")
def auth_reset_password(body: ResetPasswordIn):
    try:
        accounts.reset_password(body.token, body.new_password)
        return {"ok": True, "message": "Your password has been reset successfully. Please log in."}
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.post("/auth/passcode/set")
def auth_set_passcode(body: PasscodeSetIn, user_id: str = Depends(require_user)):
    try:
        accounts.set_passcode(user_id, body.passcode)
        return {"ok": True, "message": "App passcode configured successfully."}
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.post("/auth/passcode/verify")
def auth_verify_passcode(body: PasscodeVerifyIn, user_id: str = Depends(require_user)):
    try:
        valid = accounts.verify_passcode(user_id, body.passcode)
        if not valid:
            raise HTTPException(401, "Incorrect passcode.")
        return {"ok": True, "valid": True}
    except accounts.AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@app.delete("/auth/me")
def auth_delete_me(request: Request, user_id: str = Depends(require_user)):
    accounts.delete_account(user_id)
    response = JSONResponse({"ok": True, "message": "Account and all associated safety data permanently deleted."})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/maps/config")
def maps_config():
    return {
        "google_maps_api_key": config.GOOGLE_MAPS_API_KEY,
        "google_client_id": config.GOOGLE_CLIENT_ID,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "llm_configured": bool(config.GROQ_API_KEY),
        "model": config.GROQ_MODEL,
        "demo_mode": config.DEMO_MODE,
        "telegram_on": notify.telegram_on(),
        "email_on": notify.email_on(),
        "google_maps_configured": bool(config.GOOGLE_MAPS_API_KEY),
    }


@app.post("/analyze", response_model=AnalyzeResponse, dependencies=signed_in)
async def analyze_conversation(req: AnalyzeRequest) -> AnalyzeResponse:
    return await analyze(req)


# ---------- Kavach Guardian (AI agent) ----------

@app.post("/agent", dependencies=signed_in)
async def guardian(req: AgentIn):
    """She asks in plain words; the agent picks Kavach's tools step by step and reports the trail."""
    return await run_agent(req)


# ---------- Evidence Vault ----------

@app.post("/evidence", dependencies=signed_in)
def save_evidence(item: EvidenceIn, background: BackgroundTasks):
    try:
        sealed = store.save_capture(item)
    except store.EvidenceError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Embedding into the vector DB happens after the reply, so sealing never waits on the model.
    background.add_task(evidence_index.index_capture, sealed["capture_id"])
    return sealed


@app.get("/evidence/search", dependencies=signed_in)
def search_evidence(q: str, k: int = 5, conversation: str | None = None, flagged_only: bool = False):
    """Semantic search over sealed messages ("when did he talk about my college?")."""
    if not q.strip():
        raise HTTPException(400, "query is empty")
    try:
        hits = evidence_index.search(q, k=max(1, min(k, 20)), conversation=conversation, flagged_only=flagged_only)
    except Exception as exc:  # embedding model can't load (offline first run)
        raise HTTPException(503, f"semantic search unavailable: {exc}") from exc
    return {"query": q, "results": hits}


@app.get("/evidence", dependencies=signed_in)
def list_evidence():
    rows = store.all_rows()
    return {"records": rows, "verification": store.verify(rows)}


@app.get("/evidence/verify", dependencies=signed_in)
def verify_evidence():
    return store.verify()


@app.get("/evidence/report", dependencies=signed_in)
def evidence_report():
    pdf = report.build_report()
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="kavach-evidence-report.pdf"'},
    )


@app.get("/evidence/screenshot/{digest}", dependencies=signed_in)
def evidence_screenshot(digest: str):
    shot = store.screenshot(digest) if store.owns_screenshot(digest) else None
    if shot is None:
        raise HTTPException(404, "screenshot not found")
    data, ext = shot
    return Response(data, media_type="image/png" if ext == "png" else "image/jpeg")


@app.delete("/evidence", dependencies=signed_in)
def clear_evidence():
    """Deletes everything in the Vault. Asked for by her, after use."""
    store.clear()
    evidence_index.clear()
    return {"ok": True}


@app.post("/evidence/demo/tamper", dependencies=signed_in)
def demo_tamper(body: TamperIn):
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    try:
        return store.demo_tamper(body.record_id, body.rehash)
    except store.EvidenceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/evidence/demo/reset", dependencies=signed_in)
def demo_reset():
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    store.demo_reset()
    evidence_index.clear()
    return {"ok": True}


# ---------- Complaint Assistant ----------

@app.post("/complaint", dependencies=signed_in)
async def draft_complaint(body: ComplaintIn):
    _own_capture(body.capture_id)
    try:
        return await build_complaint(body.capture_id)
    except ComplaintError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/complaint/pdf", dependencies=signed_in)
def complaint_pdf(body: ComplaintPdfIn):
    _own_capture(body.capture_id)
    return Response(
        build_complaint_pdf(body),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="kavach-complaint.pdf"'},
    )


def _own_capture(capture_id: str) -> None:
    if not any(r["capture_id"] == capture_id for r in store.all_rows()):
        raise HTTPException(404, "capture not found in your Evidence Vault")


@app.get("/legal/sections")
def legal_sections():
    """The whole corpus, so anyone can see exactly which laws the assistant is allowed to cite."""
    return {"sections": corpus()}


# ---------- Discreet SOS + Responder Network ----------

_tasks: set = set()


def _me() -> str:
    return context.current() or sos.LEGACY_USER


def _own_sos(sos_id: str) -> None:
    s = _sos_call(sos.get_sos, sos_id)
    if s["user"] != _me():
        raise HTTPException(404, "SOS not found")


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


def _resolve_request_uid(request: Request) -> str | None:
    uid = context.current()
    if not uid and request:
        raw_token = request.cookies.get(SESSION_COOKIE)
        if not raw_token:
            auth_h = request.headers.get("Authorization", "")
            if auth_h.startswith("Bearer "):
                raw_token = auth_h[7:].strip()
            elif "X-Kavach-Token" in request.headers:
                raw_token = request.headers["X-Kavach-Token"].strip()
        if raw_token:
            u = accounts.user_for_session(raw_token)
            if u:
                uid = u["id"]
        if not uid:
            user_header = request.headers.get("X-Kavach-User")
            if user_header:
                u = accounts.user_for_id(user_header)
                if u:
                    uid = u["id"]
        if not uid and request.headers.get("X-Kavach-Client") == "extension":
            latest = accounts.latest_active_user()
            if latest:
                uid = latest["id"]
        if uid:
            context.set_current(uid)
    return uid


@app.get("/sos/setup")
async def sos_setup(request: Request):
    """What the SOS setup screen needs: her name, contacts, and which alert channels work."""
    uid = _resolve_request_uid(request)
    if not uid:
        return {
            "name": "Someone you know",
            "telegram": {
                "on": notify.telegram_on(),
                "bot": await notify.bot_username(),
                "problem": notify.problems.get("telegram"),
            },
            "email_on": notify.email_on(),
            "email_problem": notify.problems.get("email"),
            "radius_m": sos.RADIUS_M,
        }
    return {
        "name": people.her_name(owner=uid),
        "contacts": people.list_kind("contact", owner=uid),
        "telegram": {
            "on": notify.telegram_on(),
            "bot": await notify.bot_username(),
            "problem": notify.problems.get("telegram"),
        },
        "email_on": notify.email_on(),
        "email_problem": notify.problems.get("email"),
        "radius_m": sos.RADIUS_M,
    }


@app.get("/sos/invite", dependencies=signed_in)
async def sos_invite():
    """The shared Telegram link: anyone who opens it and taps Start becomes a trusted contact."""
    return {"link": await notify.invite_link()}


@app.post("/sos/invite/reset", dependencies=signed_in)
async def sos_invite_reset():
    """New invite link; the old one stops adding people (contacts already added stay)."""
    people.invite_code(reset=True)
    return {"link": await notify.invite_link()}


@app.post("/sos/test-channels", dependencies=signed_in)
async def sos_test_channels():
    """Sends a test over Telegram (to connected contacts) and email (to the Gmail account itself)."""
    return await notify.test_channels()


@app.put("/sos/profile", dependencies=signed_in)
def sos_profile(body: ProfileIn):
    return {"name": people.set_her_name(body.name)}


_EVENT = {"started": "sos_started", "first_fix": "location_found", "repeated": "sos_repeated"}


@app.post("/sos", dependencies=signed_in)
async def sos_trigger(body: SosIn, request: Request):
    loc = _decrypt(body.ciphertext) if body.ciphertext else None
    source = body.location_source if loc else "none"
    result = _sos_call(sos.trigger, _me(), loc, body.trigger, source, _public_base(request))
    _background(getattr(notify, _EVENT[result.pop("event")])(result["sos_id"]))
    return result


@app.post("/sos/safe", dependencies=signed_in)
async def sos_safe():
    """'I'm safe' from any screen: ends her running SOS, if any, and tells contacts and responders."""
    active = sos.active_for(_me())
    if not active:
        return {"ended": False}
    sos.cancel(active["sos_id"])
    _background(notify.sos_ended(active["sos_id"]))
    return {"ended": True, "sos_id": active["sos_id"]}


@app.get("/sos/active", dependencies=signed_in)
def sos_active():
    """Her running SOS, if any (declared before /sos/{sos_id} so it isn't taken as an id)."""
    return {"active": sos.active_for(_me())}


@app.get("/sos/{sos_id}", dependencies=signed_in)
def sos_status(sos_id: str):
    _own_sos(sos_id)
    return _sos_call(sos.summary, sos_id)


@app.post("/sos/{sos_id}/location", dependencies=signed_in)
async def sos_location(sos_id: str, body: LocationIn):
    """Live GPS from her phone while the SOS is active."""
    _own_sos(sos_id)
    result = _sos_call(sos.update_location, sos_id, _decrypt(body.ciphertext))
    _background(notify.location_found(sos_id) if result["first_fix"] else notify.location_updated(sos_id))
    return result


@app.post("/sos/{sos_id}/cancel", dependencies=signed_in)
async def sos_cancel(sos_id: str):
    _own_sos(sos_id)
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


@app.post("/reset", dependencies=signed_in)
async def reset_everything():
    """One button before a demo: ends any running SOS (pins stop), then clears SOS history and the Vault.
    Trusted contacts, responders and her name stay, so the demo is ready to run again."""
    active = sos.active_for(_me())
    if active:
        sos.cancel(active["sos_id"])
        await notify.sos_ended(active["sos_id"])  # before the history goes, so live pins are stopped
    sos.reset(_me())
    store.clear()
    evidence_index.clear()
    return {"ok": True, "ended_sos": bool(active)}


@app.delete("/sos/history", dependencies=signed_in)
def sos_clear_history():
    _sos_call(sos.clear_history, _me())
    return {"ok": True}


@app.post("/sos/demo/reset", dependencies=signed_in)
def sos_demo_reset():
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    sos.reset(_me())
    return {"ok": True}


# ---------- people: her trusted contacts and registered responders ----------

def _person(pid: str, request: Request = None) -> dict:
    """A contact only for the account it belongs to; responders manage themselves (responder page)."""
    p = _sos_call(people.get, pid)
    curr_uid = context.current()
    if not curr_uid and request:
        curr_uid = _resolve_request_uid(request)
    if p["kind"] == "contact":
        if not curr_uid:
            if not config.DEMO_MODE:
                raise HTTPException(404, "person not found")
        elif not people.owns(p):
            raise HTTPException(404, "person not found")
    return p


@app.get("/people")
def people_list(kind: str = "contact"):
    if kind == "contact":
        require_user()
    return {"people": people.list_kind(kind)}


@app.post("/people")
async def people_create(body: PersonIn, request: Request):
    curr_uid = _resolve_request_uid(request)
    if body.kind == "contact":
        require_user(request)
        if not curr_uid:
            curr_uid = require_user(request)

    owner = curr_uid if (body.kind == "contact" or curr_uid) else None
    user_email = None
    if curr_uid:
        u = accounts.user_for_id(curr_uid)
        if u:
            user_email = (u.get("email") or "").strip().lower()

    p = _sos_call(
        people.create,
        body.kind,
        body.name,
        body.phone,
        body.email,
        body.lat,
        body.lng,
        owner,
        body.relationship,
        body.whatsapp,
        user_email,
    )
    tg_link = await notify.link_for(p)
    if body.kind == "contact" and body.email:
        raw_token = request.cookies.get(SESSION_COOKIE)
        curr_user = accounts.user_for_session(raw_token) if raw_token else None
        user_name = curr_user.get("full_name") or curr_user.get("username") if curr_user else "A Kavach user"
        asyncio.create_task(notify.send_guardian_welcome_email(body.email, body.name, user_name, tg_link))
    return {**p, "telegram_link": tg_link}


@app.post("/people/{pid}/siren")
async def people_siren(pid: str, request: Request):
    p = _person(pid)
    raw_token = request.cookies.get(SESSION_COOKIE)
    curr_user = accounts.user_for_session(raw_token) if raw_token else None
    user_name = (curr_user.get("full_name") or curr_user.get("username") if curr_user else None) or "Your ward"

    lat = None
    lng = None
    try:
        data = await request.json()
        if isinstance(data, dict):
            lat = data.get("lat")
            lng = data.get("lng")
    except Exception:
        lat = request.query_params.get("lat")
        lng = request.query_params.get("lng")

    parsed_lat = float(lat) if lat is not None else None
    parsed_lng = float(lng) if lng is not None else None

    email_status = "no email"
    if p.get("email"):
        email_status = await notify.send_siren_alert_email(
            p["email"], p["name"], user_name, lat=parsed_lat, lng=parsed_lng
        )

    tg_status = "not linked"
    if p.get("telegram_chat_id"):
        try:
            loc_msg = ""
            if parsed_lat is not None and parsed_lng is not None:
                loc_msg = f"\n\n📍 Live GPS Location:\nhttps://maps.google.com/?q={parsed_lat:.6f},{parsed_lng:.6f}\nCoordinates: {parsed_lat:.6f}, {parsed_lng:.6f}"
            await notify.tg(
                "sendMessage",
                chat_id=p["telegram_chat_id"],
                text=f"🚨 URGENT EMERGENCY SIREN: {user_name} has triggered an emergency distress alarm right now!{loc_msg}\nPlease call or assist immediately."
            )
            tg_status = "sent"
        except Exception as exc:
            tg_status = f"failed: {exc}"
    return {"ok": True, "email": email_status, "telegram": tg_status}


@app.post("/sos/auto-alert")
async def sos_auto_alert(req: AutoAlertRequest, request: Request):
    """Automatically dispatches critical threat warnings with live GPS & LLM guardian advice when threat score >= 95."""
    user_id = context.current()
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        auth_h = request.headers.get("Authorization", "")
        if auth_h.startswith("Bearer "):
            raw_token = auth_h[7:].strip()
        elif "X-Kavach-Token" in request.headers:
            raw_token = request.headers["X-Kavach-Token"].strip()

    curr_user = accounts.user_for_session(raw_token) if raw_token else None
    if not curr_user and user_id:
        curr_user = accounts.user_for_id(user_id)
    if not curr_user:
        curr_user = accounts.latest_active_user()

    if curr_user:
        user_id = curr_user["id"]
        context.set_current(user_id)
        user_name = curr_user.get("full_name") or curr_user.get("username") or "Your ward"
    else:
        user_name = "Your ward"

    contacts = people.list_kind("contact", owner=user_id) if user_id else []
    if not contacts and curr_user:
        u_email = (curr_user.get("email") or "").strip().lower()
        if u_email:
            contacts = people.list_kind("contact", owner=u_email)
    if not contacts:
        contacts = people.list_kind("contact")

    # Record active emergency SOS in Kavach system so dashboards and maps reflect it
    sos_id = None
    if user_id:
        try:
            loc = {"lat": req.lat, "lng": req.lng, "accuracy": 10.0} if req.lat is not None and req.lng is not None else None
            res = _sos_call(sos.trigger, user_id, loc, "auto_threat_95", "browser_extension", _public_base(request))
            sos_id = res.get("sos_id")
        except Exception as e:
            log.warning("Could not create SOS session for auto-alert: %s", e)

    advice = req.guardian_advice or (
        "Approach your ward gently and calmly without anger, panic, or victim-blaming. "
        "Reassure them: 'You did nothing wrong, I am here with you, and we will handle this together.' "
        "Do not transfer any money. Contact Cyber Crime Helpline 1930 or 112 if in danger."
    )

    loc_tg = ""
    loc_mail = ""
    if req.lat is not None and req.lng is not None:
        gmaps = f"https://maps.google.com/?q={req.lat:.6f},{req.lng:.6f}"
        osm = f"https://www.openstreetmap.org/?mlat={req.lat:.6f}&mlon={req.lng:.6f}#map=16/{req.lat:.6f}/{req.lng:.6f}"
        loc_tg = f"\n📍 LIVE GPS LOCATION:\nGoogle Maps: {gmaps}\nOSM: {osm}\nCoordinates: {req.lat:.6f}, {req.lng:.6f}\n"
        loc_mail = f"\n• Live GPS Location: {gmaps}\n• Exact Coordinates: {req.lat:.6f}, {req.lng:.6f}\n"

    tg_sent = 0
    mail_sent = 0

    tg_text = (
        f"🚨 KAVACH CRITICAL AUTO-ALERT (Threat Score: {req.risk_score}/100)\n\n"
        f"An urgent safety threat was detected for {user_name} from sender: '{req.sender}'.\n"
        f"Threat Type: {req.category.replace('_', ' ').upper()}\n\n"
        f"⚠️ THREAT SUMMARY:\n{req.reason}\n"
        f"{loc_tg}\n"
        f"💙 HOW TO APPROACH {user_name.upper()} POLITELY & CALMLY:\n{advice}\n\n"
        f"Immediate Action Steps:\n"
        f"1. Reach out calmly with care and affection.\n"
        f"2. Say: 'You did nothing wrong. I am here to help you.'\n"
        f"3. Do NOT transfer money or comply with extortion.\n"
        f"4. Need police help? Dial 112 or 1930."
    )

    for c in contacts:
        if c.get("telegram_chat_id") and notify.telegram_on():
            try:
                await notify.tg("sendMessage", chat_id=c["telegram_chat_id"], text=tg_text)
                tg_sent += 1
            except Exception as e:
                log.warning("Auto-alert telegram to %s failed: %s", c.get("name"), e)

        if c.get("email") and notify.email_on():
            subject = f"🚨 URGENT KAVACH AUTO-ALERT: Safety Threat Detected for {user_name} ({req.risk_score}/100)"
            body = (
                f"Hello {c.get('name', 'Guardian')},\n\n"
                f"Kavach Smart Shield automatically detected an urgent online safety threat for {user_name}.\n\n"
                f"INCIDENT DETAILS:\n"
                f"• Threat Category: {req.category.replace('_', ' ').upper()}\n"
                f"• Threat Risk Score: {req.risk_score}/100 (CRITICAL)\n"
                f"• Sender / Account: {req.sender}\n"
                f"• Threat Summary: {req.reason}\n"
                f"{loc_mail}\n"
                f"GUIDANCE FOR GUARDIANS (HOW TO APPROACH POLITELY & SUPPORTIVELY):\n"
                f"{advice}\n\n"
                f"NEXT STEPS:\n"
                f"1. Reach out to {user_name} calmly. Avoid showing panic or anger.\n"
                f"2. Explicitly state: 'You are safe, you did nothing wrong, I am by your side.'\n"
                f"3. Do not engage with the blackmailer or transfer funds.\n"
                f"4. File a complaint via Kavach or call 1930 / 112.\n\n"
                f"Kavach Automated Emergency Network"
            )
            try:
                res = await notify.email(c["email"], subject, body)
                if res == "sent":
                    mail_sent += 1
            except Exception as e:
                log.warning("Auto-alert email to %s failed: %s", c.get("email"), e)

    return {
        "ok": True,
        "dispatched_to_guardians": len(contacts),
        "telegram_sent": tg_sent,
        "email_sent": mail_sent,
    }


@app.patch("/people/{pid}")
def people_update(pid: str, body: PersonPatch, request: Request):
    curr_uid = _resolve_request_uid(request)
    p = _person(pid, request)

    owner = p.get("user_id") or p.get("owner")
    if p["kind"] == "responder" and owner:
        if not curr_uid or curr_uid != owner:
            raise HTTPException(403, "You can only edit your own responder details")
    elif p["kind"] == "contact" and owner:
        if not people.owns(p):
            raise HTTPException(403, "You can only edit your own guardians")

    return _sos_call(people.update, pid, **body.model_dump(exclude_unset=True))


@app.delete("/people/{pid}")
def people_delete(pid: str, request: Request):
    curr_uid = _resolve_request_uid(request)
    p = _person(pid, request)
    owner = p.get("user_id") or p.get("owner")
    if owner and curr_uid and curr_uid != owner:
        if not people.owns(p):
            raise HTTPException(403, "You can only delete your own contacts or responder registrations")
    _sos_call(people.delete, pid)
    return {"ok": True}


@app.get("/people/{pid}/telegram")
async def people_telegram(pid: str, request: Request = None):
    p = _person(pid, request)
    return {"link": await notify.link_for(p), "linked": p["telegram_chat_id"] is not None}


@app.post("/people/{pid}/verify")
def people_verify(pid: str, verified: bool = True):
    """Admin approval of a responder. Demo mode only until there are real admin accounts."""
    if not config.DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    return _sos_call(people.verify, pid, verified)


@app.get("/responders")
def responders(lat: float | None = None, lng: float | None = None, request: Request = None):
    curr_uid = _resolve_request_uid(request) if request else context.current()
    user_email = ""
    if curr_uid:
        u = accounts.user_for_id(curr_uid)
        if u:
            user_email = (u.get("email") or "").lower().strip()

    all_resp = people.list_kind("responder")
    for r in all_resp:
        r_owner = r.get("user_id") or r.get("owner")
        r_email = (r.get("email") or "").lower().strip()
        r["is_self"] = bool(
            (curr_uid and r_owner == curr_uid) or
            (user_email and r_email == user_email)
        )
        if lat is not None and lng is not None and r.get("lat") is not None and r.get("lng") is not None:
            dist = round(sos.haversine_m(lat, lng, r["lat"], r["lng"]))
            r["distance_m"] = dist
            r["distance_km"] = round(dist / 1000, 1)
            r["is_nearby"] = dist <= sos.RADIUS_M
        else:
            r["distance_m"] = None
            r["distance_km"] = None
            r["is_nearby"] = True
    if lat is not None and lng is not None:
        all_resp.sort(key=lambda x: (not x.get("is_self", False), not x.get("is_nearby", True), x.get("distance_m") or 999999999))
    else:
        all_resp.sort(key=lambda x: not x.get("is_self", False))
    return {"responders": all_resp, "radius_m": sos.RADIUS_M, "current_user_id": curr_uid}


@app.get("/responders/{responder_id}/alerts")
def responder_alerts(responder_id: str):
    return {"alerts": _sos_call(sos.alerts_for, responder_id)}


app.include_router(security_router)

# Mounted last so API routes take precedence over static files.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
