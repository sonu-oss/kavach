# Discreet SOS + Responder Network: handover

Features 4 and 5 of Kavach. They're two sides of one emergency, and both are **real**: real people, real Telegram alerts, real live location.

- **Discreet SOS (Feature 4):** the woman in danger asks for help without anyone around her noticing.
- **Responder Network (Feature 5):** verified volunteers nearby receive that alert and go to help.

---

## 1. The story (what to tell judges)

She's in a cab going the wrong way, or being followed, or the harasser is next to her. Taking out her phone to call the police could make it worse.

1. She opens Kavach. It looks like an **incoming call from "Mom"**.
2. **Tap Decline:** an ordinary declined call. Nothing else happens.
3. **Hold Decline for 2 seconds:** the screen shows the same ordinary declined call, but **SOS goes out silently**:
   - her **GPS location** is read and **encrypted on her phone** before it's sent,
   - her **trusted contacts** get a Telegram message plus a **live location pin that moves with her**, and an email with the live link,
   - **verified responders within 3 km** get a Telegram alert showing only an **approximate 1 km area**, with **Accept / Decline** buttons.
4. A responder taps **Accept** (in Telegram or on the responder page), and only that responder gets her **exact live location**. Her contacts are told "X is on the way".
5. While the SOS is on, her location updates every 15 seconds.
6. When she's safe she taps **I'm safe**: everyone is told and the live pins stop.
7. **Accept** on the fake call shows an in-call screen: a believable excuse to walk away.

---

## 2. One-time setup

**On the server (`backend/.env`):**
```
TELEGRAM_BOT_TOKEN=...   # from @BotFather → /newbot
SMTP_USER=...            # optional email backup: a Gmail address
SMTP_PASSWORD=...        # its App Password (Google Account → Security → App passwords)
SOS_RADIUS_M=3000        # responders within this distance are alerted
```
Restart the server after editing `.env`. The SOS **Setup** panel shows "Telegram on · Email on/not set up".

**Her trusted contacts:** SOS → **Setup** → add name (and email) → **Connect Telegram** shows a QR code / link. The contact scans it and taps **Start** on the bot once.

**Responders:** each opens `/responder.html` on their phone → **Become a responder** (name, email, location) → **Connect Telegram** → tap **Start**. Then an admin approves them under **Approve responders** (shown while `KAVACH_DEMO=1`).

> If someone has already chatted with the bot, opening a second link may just reopen the chat. Then type `/start <code>` yourself (the code is the part after `start=` in their link).

---

## 3. Rules the code enforces

| Rule | Detail |
|---|---|
| Location only on SOS | Read when SOS fires, then live every 15 s while it's on and the page stays open. Never before. |
| Encrypted on her phone | RSA-OAEP-256 with the server's public key (`GET /sos/key`). Private key in `backend/data/sos_key.pem`, **never committed**. |
| Who gets alerted | Responders who are **verified**, **opted in**, have **shared a location**, and are **within `SOS_RADIUS_M`**. |
| Approximate until accepted | Responders see a 1 km circle. Accepting reveals the exact point **to that responder only**. Only that person's own Telegram chat can press their Accept button. |
| Trusted contacts are trusted | They get her exact live location straight away. |
| Never block her twice | Holding Decline again while an SOS is active **updates** it instead of creating a new one. |
| Rate limit | Max **3 new SOS per 10 minutes**; the error tells her to call 112. |
| Survives restarts and reloads | Everything is in SQLite (`backend/data/kavach.db`). Reopening the SOS screen picks up an active SOS so **I'm safe** is always reachable. |
| Auto-expiry | An SOS nobody ended stops after 2 hours. |

---

## 4. Where the code is

| File | What it does |
|---|---|
| `frontend/sos.js` | Fake call, hidden 2-second press, GPS + live updates, browser encryption, Setup (her name, contacts, Telegram QR), presenter panel, I'm safe |
| `frontend/responder.html` | Responder sign-up, Telegram connect, opt in/out, admin approval, alerts map, Accept / Decline |
| `frontend/track.html` | Trusted contact's live-location page |
| `backend/app/sos/service.py` | SOS rules: who's alerted, approximate vs exact, expiry, rate limit |
| `backend/app/sos/people.py` | Trusted contacts and responders (create, verify, opt in, Telegram link codes) |
| `backend/app/sos/notify.py` | Telegram bot (long-polling, `/start` linking, Accept buttons, live pins) and email backup |
| `backend/app/sos/db.py` | SQLite tables: `people`, `profile`, `sos`, `alerts` |
| `backend/app/sos/crypto.py` | RSA key + location decryption |
| `backend/app/main.py` | API routes (section "Discreet SOS + Responder Network") |
| `backend/tests/test_sos.py` | 18 tests (Telegram is faked in tests; nothing is sent) |

### API

| Endpoint | Purpose |
|---|---|
| `GET /sos/key` | Public key for encrypting the location |
| `GET /sos/setup` · `PUT /sos/profile` | Her name, contacts, which channels are on · set her name |
| `POST /sos` | Trigger: encrypted location |
| `POST /sos/{id}/location` | Live location update |
| `GET /sos/active` · `GET /sos/{id}` · `POST /sos/{id}/cancel` | Running SOS · status with delivery results · I'm safe |
| `GET /sos/track/{token}` | Trusted contact's live link |
| `GET/POST /people` · `PATCH/DELETE /people/{id}` | Contacts and responders |
| `GET /people/{id}/telegram` | Their personal Telegram link |
| `POST /people/{id}/verify` | Approve a responder (demo mode) |
| `GET /responders` · `GET /responders/{id}/alerts` · `POST /sos/{id}/respond` | Responder dashboard |
| `POST /sos/demo/reset` | Demo only: clear SOS history (keeps people) |

---

## 5. How to test

1. Add yourself as a trusted contact (SOS → Setup) and connect Telegram.
2. Open `/responder.html`, sign up as a responder, connect Telegram, then approve yourself under **Approve responders**.
3. SOS → **Ring the fake call** → hold **Decline** 2 s.
4. Telegram: you get the contact alert (live pin) and the responder alert (Accept / Decline).
5. Tap **Accept**: exact pin arrives; the contact side gets "on the way".
6. Tap **I'm safe** in the app: both get "marked herself safe", pins stop.
7. **Reset SOS demo** before the next run.

Use **Setup → Location → Demo location** when testing on a laptop; use **Real GPS** on a phone.

Run tests: `cd backend && .venv/bin/python -m pytest -q tests/test_sos.py`

---

## 6. Known limits and next tasks

- [ ] **Phone access over https** (in progress): needed for GPS, encryption and Telegram links on a phone. Links in messages point to wherever the app was opened from; on `localhost` they won't open on a phone.
- [ ] Live location only updates while her SOS screen stays open (browsers pause pages in the background).
- [ ] No user accounts yet: one "her" per server, and admin approval is open in demo mode.
- [ ] Email backup needs a Gmail App Password in `.env`.
- Roadmap: SMS/phone calls (paid, Indian DLT registration), ID verification for responders, false-alert reporting, a home-screen / power-button shortcut for the fake call.

**Don't:** commit `backend/.env` (keys) or anything in `backend/data/` (database, SOS private key).
