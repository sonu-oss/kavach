# Kavach 1.0 — Architecture, Requirements & Implementation Log

This document tracks all requirements, design decisions, architectural components, and implementation details for **Kavach 1.0** (Personal Safety Platform for Women and Children).

---

## Architecture Overview

```
                      ┌──────────────────────────────────────────────────────────┐
                      │              Kavach 1.0 Client Interface                 │
                      │  (Responsive PWA: Mobile First, High-Contrast Emergency) │
                      └─────────────┬───────────────────────────────┬────────────┘
                                    │ (Encrypted Sessions/WebCrypto)│
                                    ▼                               ▼
                      ┌──────────────────────────┐    ┌──────────────────────────┐
                      │   Emergency SOS Space    │    │  Protected Safety Circle │
                      │  - One-tap SOS Trigger   │    │  - Trusted Guardians     │
                      │  - Live Location Watch   │    │  - Community Responders  │
                      │  - Dispatched Alerts     │    │  - App Lock Passcode     │
                      └─────────────┬────────────┘    └─────────────┬────────────┘
                                    │                               │
                                    ▼                               ▼
                      ┌──────────────────────────────────────────────────────────┐
                      │             FastAPI Backend (app/main.py)                │
                      │  - Argon2id Password & Passcode Hasher                   │
                      │  - Session Management (HttpOnly 256-bit SHA-256 tokens) │
                      │  - SOS Dispatch Engine (Telegram & SMTP Alerting)       │
                      │  - Auto-Adapting Database Layer (Postgres / SQLite)      │
                      └─────────────┬───────────────────────────────┬────────────┘
                                    │                               │
                                    ▼                               ▼
                ┌───────────────────────────────────┐ ┌──────────────────────────┐
                │        Database Layer             │ │     Local AI Guard       │
                │  - SQLite (Local development)     │ │  - FastEmbed & ChromaDB  │
                │  - Supabase/Neon Postgres (Prod)  │ │  - Groq LLM (Optional)   │
                │  - User-Scoped Row Isolation      │ │  - Keyword Safety Filter │
                └───────────────────────────────────┘ └──────────────────────────┘
```

---

## Log Entry: Step 1 — Real Authentication, Password Recovery & Owner Passcode

### 1. User Requirements
* Real login, registration, and logout flows (no mock or hardcoded state).
* Support for `email`, `phone`, `full_name`, and optional email verification status.
* Password reset mechanism (`/auth/forgot-password` and `/auth/reset-password`).
* App Lock Passcode protection (`/auth/passcode/set` and `/auth/passcode/verify`) to safeguard sensitive safety settings and contacts from unauthorized device access.
* Full account and data deletion (`DELETE /auth/me`) respecting privacy and right-to-be-forgotten.
* Database flexibility: Run locally on SQLite/DBeaver Postgres without paid cloud bills; seamlessly connect to free cloud Postgres (Supabase/Neon) upon deployment.

### 2. Technologies & Primitives Used
* **Argon2id (`argon2-cffi`):** The gold-standard memory-hard password hashing algorithm (RFC 9106) protecting both user passwords and app passcodes against GPU/ASIC cracking.
* **Cryptographic Token Generator (`secrets.token_urlsafe`):** For secure reset tokens and session IDs.
* **Dual-Engine DB Schema Migration (`app.database.schema`):** Auto-migrates database schemas across both SQLite and PostgreSQL using column introspection without downtime.
* **Strict Pydantic Validation (`app.auth.schemas`):** Form sanitization, length bounds, and structure verification.

### 3. Why This Approach?
* **Zero-Cost Cloud Deployment:** Using standard connection URLs allows developers to run on free SQLite locally and migrate to free-tier cloud PostgreSQL (Supabase or Neon) with zero code changes.
* **App Passcode vs OS Restrictions:** An app-level passcode safely locks access to emergency guardian configurations, personal evidence, and live tracker links without falsely claiming to bypass Android OS permission controls.
* **Isolated User Context:** Every query filters by `context.current()` so no user's vault, contacts, or SOS alerts are ever exposed to another account.

### 4. How It Works
1. **Registration:** Hashes password with Argon2id, assigns a unique `uid`, stores user attributes (`full_name`, `email`, `phone`), and issues a secure 256-bit cookie session.
2. **Login:** Accepts username or email, checks lockout threshold (max 5 failed attempts in 15 mins), verifies Argon2id hash, and issues a session cookie.
3. **Password Reset:** Generates a single-use 15-minute token (`reset_token`, `reset_expires`) that resets the user's password once verified.
4. **Owner Passcode:** Sets an independent 4-6 digit numerical PIN hashed with Argon2id for locking the interface.
5. **Account Deletion:** Atomically wipes sessions, vault evidence, trusted contacts, and SOS logs associated with `user_id`.

---

## Log Entry: Step 2 — Redesigned High-Trust Authentication & Guided Onboarding (`login.html`)

### 1. User Requirements
* Replace demo aesthetics with a real-world, trustworthy personal safety portal.
* Display the prominent security guarantee:
  > *"Your evidence vault, trusted contacts and SOS stay private to your account. Nobody else on this app can see them. Passwords are protected with Argon2id hashing. We never store or see your actual password."*
* Support real login with email or registered username, show/hide password toggle, and loading state.
* Support real registration with Full Name, Email, Mobile number with country code (+91 default), password strength criteria checklist, confirm password, and Terms & Privacy Policy acceptance.
* Add full password recovery flow (token request + instant token password update).
* Guided Onboarding Modal triggered immediately after signup:
  - Step 1: Welcome & Safety Circle Explanation.
  - Step 2: Transparent Permission Explanations (Location, Critical Notifications, Calling).
  - Step 3: Owner App Passcode Setup (numeric PIN saved via `/auth/passcode/set`).
  - Step 4: Add First Trusted Guardian (saved via `/people`).
  - Step 5: Dashboard Entry.

### 2. Technologies & Primitives Used
* **Responsive HTML5 / CSS3 (CSS Variables, Flexbox, CSS Grid):** Mobile-first layout with high-contrast emergency typography.
* **Fetch API with Credentials (`same-origin`):** Secure transmission of HttpOnly cookies without exposing raw session tokens to JavaScript.
* **Real-Time Client-Side Validation:** Instant entropy scoring, password matching, and regex sanitization.
* **Accessible Dialog Modals:** Modal overlays for password recovery and onboarding steps with keyboard and focus management.

### 3. Why This Approach?
* **First-Run Friction Reduction:** Rather than dropping a newly registered user onto a blank page, the guided onboarding flow ensures their safety circle (PIN passcode + first guardian) is configured before danger occurs.
* **Zero Fake States:** All buttons communicate with real backend endpoints (`/auth/signup`, `/auth/login`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/passcode/set`, `/people`).

---

## Log Entry: Step 3 — Real-World Safety Dashboard (`index.html`)

### 1. User Requirements
* Transform `index.html` from the poetic "bloom garden" demo into a high-trust, productive women and children safety command center.
* Prominently feature the security guarantee box:
  > *"Your evidence vault, trusted contacts and SOS stay private to your account. Nobody else on this app can see them. Passwords are protected with Argon2id hashing. We never store or see your actual password."*
* Include live safety status bar with direct national helpline dials (112, 1091, 1930, 181).
* Display trusted guardians directly on the home dashboard with Telegram connection status.
* 3-Step "How Kavach Protects You" section:
  1. Configure your safety circle.
  2. Add trusted guardians.
  3. Trigger SOS when in danger.
* Deep-dive safety feature cards (Discreet SOS, Encrypted GPS, Verified Responders, Evidence Vault, Legal Drafts, Battery-Conscious Operation).
* Parent & Device Security controls (Owner Passcode, OS sandboxing explanation, and Permanent Account Deletion).
* Real footer with working links to Privacy Policy (`/privacy.html`), Terms (`/terms.html`), and Support.

### 2. Technologies & Primitives Used
* **Modular Screen Navigation (`nav.js`):** Coordinates switching between Dashboard, Threat Shield, Evidence Vault, and SOS Space without page reloads.
* **Dynamic Guardian Fetching:** Automatically populates trusted contacts from `GET /people?kind=contact`.
* **Permanent Data Wipe:** Integrated with `DELETE /auth/me` to atomically erase user records upon confirmation.

---

## Log Entry: Step 4 — Dynamic 15-Minute Responder Location Auto-Update (`responder.html`)

### 1. User Requirements
* Better responder portal layout.
* Location must not remain fixed or static; it should automatically update wherever the responder travels every 15 minutes.

### 2. Technologies & Primitives Used
* **HTML5 Geolocation API (`navigator.geolocation.getCurrentPosition`):** Queries device GPS with `enableHighAccuracy: true`.
* **Periodic Background Sync Interval:** `setInterval(updateMyLocation, 15 * 60 * 1000)` sends coordinates to `PATCH /people/{id}`.
* **Leaflet & OpenStreetMap:** Displays interactive map pins and calculates dynamic distances from active SOS emergencies.

### 3. Why This Approach?
* Responders move around cities; fixed coordinates caused alerts to target volunteers who had already left the area. Automatic periodic updates ensure dispatch accuracy without draining battery.

---

## Log Entry: Step 5 — Google Services & Original Bloom UI/UX Restoration

### 1. User Requirements
* Revert the homepage (`index.html`) layout back to the original beloved Bloom Scene UI/UX with glowing SVG orbits, interactive flowers, and status sparkles.
* Update only the description on the homepage with:
  > *"Your evidence vault, trusted contacts and SOS stay private to your account. Nobody else on this app can see them. Passwords are protected with Argon2id hashing. We never store or see your actual password."*
* In `login.html`, preserve the original glowing 3D WebGL lotus animation and add the **"Continue with Google"** button with official branding.
* Add Google Maps support so the user can paste their Google Maps API Key into `backend/.env`.

### 2. Technologies & Primitives Used
* **Bloom Design System & SVG Scene:** Restored the 4-flower orbit scene (Accounts, Devices, Privacy, Passwords) with high-contrast accessibility support.
* **Three.js 3D Lotus Animation:** Preserved the WebGL renderer on `login.html`.
* **Google Identity Services:** Added the "Continue with Google" action button on `login.html`.
* **Google Maps API Integration:**
  - Added `GOOGLE_MAPS_API_KEY` and `GOOGLE_CLIENT_ID` configuration in `backend/app/config.py` and `backend/.env`.
  - Added endpoint `GET /maps/config` in `backend/app/main.py`.
  - Updated `frontend/responder.html` and `frontend/track.html` to automatically load high-resolution Google Maps layers when `GOOGLE_MAPS_API_KEY` is provided, falling back gracefully to OpenStreetMap if not set.

---

## Log Entry: Step 6 — Meta-Grade Responders Console, Multi-Guardian Network & Production Hardening

### 1. User Requirements
* Expand guardian management to support full details for at least 2 guardians: Name, Relationship (Mother, Father, Parent, Guardian, Friend), Phone, WhatsApp, and Email.
* 30-Second Emergency Escalation workflow: First alert dispatched to primary guardian; after 30 seconds, secondary alerts and live tracking links dispatch to WhatsApp/Telegram contacts.
* Owner Passcode Protection: PIN keypad modal safeguarding safety configurations and preventing unauthorized contact deletions.
* Remove demo defaults: Set `KAVACH_DEMO=0` for production hardening.
* Meta-Grade Responders Dashboard: Triage queue, live distance and timestamp telemetry, one-click Google Maps turn-by-turn navigation, and emergency service dialing (112).
* Custom 404 Error page (`frontend/404.html`).
* Comprehensive Google Cloud setup guide for Maps API & OAuth credentials.

### 2. Technologies & Primitives Used
* **Dual-Engine Map Layer:** OpenStreetMap + Google Maps JavaScript tiles using client-side fallback.
* **Escalation Timer Pipeline:** High-precision timers in `sos.js` managing progressive emergency alert dispatch.
* **Modal Dialog Architecture (`dialog.js`):** Web component `<dialog class="kdialog">` with PIN input, Argon2id verification via `POST /auth/passcode/verify`.
* **Database Schema Migrations:** Auto-registered `relationship` and `whatsapp` columns on `people` table.

---


---

## Log Entry: Step 7 — Google Cloud AI Platform (Gemini Primary + Fallback), AppSec Intelligence Pipeline & Audio Dispatch Responders Console

### 1. User Requirements
* **Google Cloud Dual-System Priority:** Google Cloud services (Gemini models, Google Maps, Google OAuth) as first preference. When quota is exhausted or credentials are unset, automatically and silently fall back to local/open-source engines (Groq, FastEmbed, OpenStreetMap) without downtime.
* **AppSec Security & Compliance Architecture:** Build an AI-assisted security and compliance engineering platform:
  1. Static analysis (Python AST visitor detecting SQL Injection, Command Injection, Insecure Deserialization, Broken Cryptography, Hardcoded Secrets).
  2. Dependency auditing against CVE database (OSV format).
  3. Security Knowledge & RAG (CWE, OWASP Top 10, SOC 2 Type II, ISO 27001, NIST CSF 2.0).
  4. Multi-agent orchestration (Investigator, RAG Researcher, Risk Prioritizer, Remediation, Verification, Compliance).
  5. Deterministic verification: Prove the vulnerability is eliminated without regressions.
* **Meta-Grade Responder Dashboard (`responder.html`):**
  - Synthesized Web Audio API emergency alert tone / audio dispatch chime.
  - Active duty status toggle (On-Duty receiving dispatches / Off-Duty standby).
  - Proximity triage badges (Critical Proximity < 1.2km, In Vicinity).
  - "Accept & En Route" and "Mark Arrived On-Scene" status updates.
  - One-click Google Maps turn-by-turn navigation and emergency 112 relay.

### 2. Technologies & Primitives Used
* **Google Cloud Gemini (`gemini-2.5-flash` / `gemini-1.5-flash`):** Primary LLM engine via REST API with automatic failover to Groq (`openai/gpt-oss-120b`).
* **Google Cloud `text-embedding-004` (Matryoshka 384 dimensions):** Primary vector embedding engine with automatic fallback to local `BAAI/bge-small-en-v1.5` FastEmbed.
* **Python AST (`ast.NodeVisitor`):** Deterministic parser inspecting syntax trees for taint flows, format strings in database queries, and unsafe subprocess parameters.
* **Web Audio API (`AudioContext`, `OscillatorNode`):** Client-side synthesized multi-tone emergency chime.
* **SOC 2 Type II / ISO 27001:2022 / NIST CSF 2.0 Control Matrix:** Enterprise compliance mapping engine in `app.security.knowledge`.

### 3. How It Works
1. **Security Scan (`POST /security/scan`):** AST visitor walks Python code, detects high-risk vulnerabilities, scans requirements against CVE tables, and returns structured findings.
2. **6-Agent Workflow (`POST /security/pipeline`):**
   - *Investigator:* Extracts taint paths and surrounding code context.
   - *RAG Researcher:* Pulls CWE and OWASP remediation guidelines.
   - *Risk Prioritizer:* Calculates CVSS scores and urgency ratings.
   - *Remediation Agent:* Generates AST-preserving code patch and diff using Gemini.
   - *Verification Agent:* Deterministically re-parses patched code to prove the vulnerability is resolved.
   - *Compliance Agent:* Maps findings to SOC 2, ISO 27001, and NIST CSF audit controls.
3. **Emergency Dispatch Console:** Responder connects via WebSocket/polling, hears audio chime upon new incident, reviews triage proximity, and navigates directly with Google Maps.

---

## Log Entry: Step 8 — Automated Real-Time Chat Ingestion (WhatsApp Web & Telegram Shield)

### 1. User Requirements
* Automatically read WhatsApp Web and Telegram chat messages in real time without requiring the victim or parent to manually take screenshots, crop images, or wait for OCR parsing.
* Eliminate manual friction by embedding a background observer that watches incoming messages and screens them against the Kavach Smart Shield API (`/analyze`).
* Provide an in-chat visual indicator and action banner whenever a threat (harassment, stalking, blackmail) is detected.

### 2. Technologies & Primitives Used
* **Chrome / Chromium Browser Extension (Manifest V3):**
  - Host permissions on `web.whatsapp.com` and `web.telegram.org`.
  - DOM `MutationObserver` on chat container nodes (`.message-in`, `.copyable-text`, `.bubble:not(.is-out)`).
  - High-efficiency debounce algorithm (1.4s) to aggregate conversational message batches without flooding the backend API.
  - Injected CSS & DOM Banner: High-contrast alert badge displaying threat categorization, risk score (0-100), and one-click actions:
    - `🔒 Save Tamper-Proof Evidence`: Directly commits the incident to Kavach Evidence Vault.
    - `🚨 Trigger SOS to Guardians`: Instant safety escalation.
* **Telegram Bot & Webhook Live Listener (`scripts/telegram_live_monitor.py`):**
  - Direct asynchronous ingestion pipeline using Telegram Bot / MTProto API for background terminal or server-side monitoring.
* **Expanded Session Auth Middleware (`backend/app/main.py`):**
  - Supports `Authorization: Bearer <token>` and `X-Kavach-Token` alongside standard HttpOnly cookies, enabling extensions and headless daemons to authenticate cleanly.

### 3. How It Works
1. **WhatsApp Web Monitoring:**
   - As the user chats on `web.whatsapp.com`, the content script monitors incoming message nodes.
   - When a new message arrives, it extracts the contact name and text, checks the cache fingerprint, and sends a structured JSON payload to `http://localhost:8000/analyze`.
   - If the risk score is $\ge 50$ or severity is `medium`/`high`, the script injects the safety banner above the message and triggers a desktop push notification.
2. **Telegram Web / Bot Monitoring:**
   - Either via the same browser extension on `web.telegram.org` or headless via `python scripts/telegram_live_monitor.py`.

---

## Log Entry: Step 9 — 100% Free & Zero-Billing Architecture as Primary Default

### 1. User Decision & Requirements
* Google Cloud Console requires credit/debit card billing verification even for trial credits.
* Remove Google Cloud as a primary dependency so the user is never blocked, forced into payment verifications, or charged fees.
* Re-orient the architecture around the **100% free, zero-card, local-first stack**:
  - Primary LLM: **Groq** (`openai/gpt-oss-120b` / `openai/gpt-oss-20b` via `console.groq.com/keys` - 100% free with no credit card).
  - Primary Vectors: **Local FastEmbed (`BAAI/bge-small-en-v1.5`) & ChromaDB/SQLite** (runs 100% locally on CPU/RAM with zero network costs).
  - Primary Maps: **OpenStreetMap (Leaflet)** (100% free, zero keys, zero billing accounts).
  - Primary Database: **Local SQLite file** in `backend/data/` (or free-tier Supabase/Neon).
* Keep Google Gemini / Maps as an optional plug-in if the user ever adds keys later, but ensure zero features require it to run.

---
