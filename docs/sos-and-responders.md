# Discreet SOS + Responder Network: handover

Features 4 and 5 of Kavach. They're two sides of one emergency:

- **Discreet SOS (Feature 4):** the woman in danger asks for help without anyone around her noticing.
- **Responder Network (Feature 5):** verified volunteers nearby receive that alert and go to help.

Both are **prototypes**: the security design is real, but calls and SMS are simulated and the responders are mock people.

---

## 1. The story (what to tell judges)

She's in a cab going the wrong way, or being followed, or the harasser is next to her. Taking out her phone to call the police could make it worse.

1. She opens Kavach. It looks like an **incoming call from "Mom"**.
2. **Tap Decline:** an ordinary declined call. Nothing else happens.
3. **Hold Decline for 2 seconds:** the screen shows the same ordinary declined call, but **SOS goes out silently**:
   - her location is read **once, at that moment** (never tracked before),
   - it's **encrypted on her phone** before it's sent,
   - her **trusted contacts** get a call plus a private live-location link,
   - **verified volunteers within 3 km** get an alert showing only an **approximate 1 km area**.
4. A volunteer presses **Accept**, and only that volunteer sees her **exact location** and directions.
5. When she's safe she presses **I'm safe**, and all sharing stops.
6. **Accept** on the fake call shows an in-call screen: a believable excuse to walk away ("Mom says come home now").

---

## 2. The three screens

| Screen | Who sees it | Where |
|---|---|---|
| **SOS** (fake call + presenter panel) | Her | App top bar → **SOS** |
| **Responder dashboard** | Volunteers | App top bar → **Responders ↗** (`/responder.html`) |
| **Live-location link** | Her trusted contacts (Mom, roommate) | `/track.html?t=…` (link shown in the SOS panel) |

The right-hand panel on the SOS screen ("Behind the scenes") is **for the presenter**, not part of her phone. It shows judges what happened silently.

---

## 3. Rules the code enforces

| Rule | Detail |
|---|---|
| Location read once | Only at trigger time. Demo location (Koramangala) by default; real GPS can be switched on in **SOS → Setup**. |
| Encrypted in the browser | RSA-OAEP-256 with the server's public key (`GET /sos/key`). The server decrypts with its private key in `backend/data/sos_key.pem`, which is **never committed**. |
| Who gets alerted | Only responders who are **verified**, **opted in**, and **within 3 km**. |
| Approximate until accepted | Responders see a 1 km circle (location snapped to a ~1 km grid). **Accept** reveals the exact point **to that responder only**. |
| Trusted-contact link | Private random token, shows her exact location and who's on the way. Expires after **2 hours** or when she presses **I'm safe**. |
| Never block her twice | Holding Decline again while an SOS is active **updates** that SOS (new location) instead of creating a new one. |
| Rate limit | Max **3 new SOS per 10 minutes**; the error message tells her to call 112. |
| Survives a reload | Reopening the SOS screen picks up a still-active SOS, so **I'm safe** is always reachable. |
| Auto-expiry | An SOS nobody ended stops after 2 hours. |

### Mock responders (placed around wherever the SOS happens)

| Responder | Gets the alert? |
|---|---|
| Asha K (volunteer, first-aid trained) | Yes · ~0.5 km |
| Campus security desk | Yes · ~1.5 km |
| Meena R (off-duty nurse) | Yes · ~2.5 km |
| Ravi T | No: ID not verified |
| Kiran P | No: has alerts turned off |
| Farah N | No: 5.2 km away |

---

## 4. Where the code is

| File | What it does |
|---|---|
| `frontend/sos.js` | Fake call, 2-second hidden press, reading location, browser encryption, presenter panel, I'm safe, reset |
| `frontend/index.html` | SOS screen markup (`#sos-view`: phone mockup + Setup) |
| `frontend/responder.html` | Responder dashboard: Leaflet + OpenStreetMap map, Accept / Decline, live indicator, new-alert banner |
| `frontend/track.html` | Trusted contact's live-location page |
| `backend/app/sos/service.py` | SOS logic: matching responders, approximate vs exact location, expiry, rate limit (state is in memory) |
| `backend/app/sos/crypto.py` | RSA key creation and location decryption |
| `backend/app/sos/schemas.py` | Request shapes |
| `backend/app/main.py` | API routes (section "Discreet SOS + Responder Network") |
| `backend/tests/test_sos.py` | 11 tests for the rules above |

### API

| Endpoint | Purpose |
|---|---|
| `GET /sos/key` | Public key for encrypting the location |
| `POST /sos` | Trigger SOS: encrypted location + trusted contacts |
| `GET /sos/active` | Her currently running SOS (used after a reload) |
| `GET /sos/{id}` · `POST /sos/{id}/cancel` | Status · "I'm safe" |
| `GET /sos/track/{token}` | Trusted contact's live link |
| `GET /responders` | List of mock responders |
| `GET /responders/{id}/alerts` | A responder's alerts (exact location only after they accept) |
| `POST /sos/{id}/respond` | Responder accepts or declines |
| `POST /sos/demo/reset` | Demo only: clear all SOS state and the rate limit |

---

## 5. How to try it

1. Run the app (see the main README) and open http://localhost:8000.
2. Open **Responders ↗** in a **second window**, side by side with the app. Keep "Signed in as: Asha K".
3. In the app: **SOS → Ring the fake call → press and hold Decline for 2 seconds**.
4. Within ~3 seconds the responder window shows a red **New SOS nearby** banner and a dashed circle.
5. Press **Accept**: the exact pin and **Get directions** appear. Switch "Signed in as" to Campus security: still only the circle.
6. Back in the app, open the **live-location link** from the panel.
7. Press **I'm safe**: the live link says she's safe, and the responder sees "ended".
8. **Reset SOS demo** (bottom of the SOS panel) before the next run.

Run the tests: `cd backend && .venv/bin/python -m pytest -q tests/test_sos.py`

---

## 6. Known issues and next tasks

**Check first:**
- [ ] Walk through section 5 once end to end. The banner, auto-jump to a new alert, Accept → exact pin, and I'm safe → "ended" on the responder page were changed last and need one more full pass.
- [ ] Responder map sometimes shows a grey strip on the left. Likely Leaflet needs `map.invalidateSize()` after the page lays out.

**Good next improvements:**
- [ ] Test **real GPS** on an actual phone (SOS → Setup → real location).
- [ ] Responder page on a phone-width screen (the layout stacks; check it's usable).
- [ ] Location updates while SOS is active: today it's read once; holding Decline again updates it. A periodic update (every 30 s, only while active) would make the live link truly live.
- [ ] Sound/vibration on the responder page when a new alert arrives.

**Roadmap (mention in the pitch, don't build for the demo):**
- Real calls/SMS (e.g. an SMS gateway), real accounts and ID verification for responders, false-alert reporting and suspension, storing SOS in a database instead of memory, a home-screen / power-button shortcut to start the fake call.

**Don't:** commit `backend/.env` (Groq key) or anything in `backend/data/` (contains the SOS private key).
