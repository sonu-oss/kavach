# Kavach AI

Detect the threat. Preserve the proof. Take action. Get help.

## Feature 1: Smart Shield (built)

A mock chat page plus a Shield side panel. The panel captures the messages on screen and sends them to `POST /analyze`. That call returns a category, a 0–100 risk score, a severity, a plain-language reason, and the IDs of the flagged messages.

```
panel ─► POST /analyze ─► keyword pre-filter ──(no signals)──► "safe" (no AI call)
                               │
                               ▼
                         PII masking  (phones, emails, UPI, Aadhaar, addresses, PIN codes → [PHONE_1] …)
                               │
                               ▼
                         Groq LLM: openai/gpt-oss-120b (JSON mode, fixed system prompt with a
                         Hinglish / Kannada slang glossary, messages passed as data)
                               │  429 rate limit → retry on openai/gpt-oss-20b (separate quota)
                               │
                               ▼
                         Pydantic validation ─(fail)─► retry once ─(fail)─► keyword verdict
```

### Scan a real chat from a screenshot

Click **Scan a screenshot** (or just press ⌘V / Ctrl+V with a screenshot on the clipboard). You can also drop or choose images, and add several screenshots in order for a long chat.

1. **The text is read in the browser** with Tesseract.js. The screenshot itself is never uploaded; only the extracted text goes to `/analyze`, where personal details are masked as usual.
2. **Bubble position decides the sender:** right-aligned bubbles are "You", left-aligned ones are the other person. Timestamps, read ticks, date dividers, the status bar and the message box are removed. The contact name is taken from the chat header.
3. **She checks the result:** she can switch Them/You, edit misread text (hard-to-read lines are outlined in amber), delete or add lines, then press **Scan with Kavach**.
4. The chat appears in the list like any other conversation, so the Shield panel, highlights and the Vault / Complaint buttons work the same way. The original images are kept on the conversation for the Evidence Vault.

`frontend/samples/` holds two test screenshots: Hinglish in light mode and Kannada-English in dark mode. Load them with **Try a sample**. The first time, the text reader downloads about 10 MB of language data from a CDN, so do one run before the demo while online.

### Run

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # paste your GROQ_API_KEY
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. The backend serves the frontend too. Without a key, the panel runs in keyword-fallback mode and says so.

**Groq free tier:** gpt-oss-120b allows about 8k tokens per minute, which is roughly 6 scans per minute. Unchanged chats are cached on the server, so switching between chats during the demo doesn't spend quota. If the limit is hit, the retry goes to gpt-oss-20b, and the source chip shows which model answered.

**Pre-filter:** a chat skips the AI only when it has no risk words at all. Words like photos, "your house", family members, "or else" and payment terms always send the chat to the AI. A missed threat is worse than one extra API call.

**Prompt injection:** any message that tries to instruct the classifier ("SYSTEM: ignore previous instructions…") is always flagged, and the reason says so. If the AI is fooled into answering "safe", the backend ignores that answer and uses the keyword verdict instead.

### Test

```bash
cd backend && .venv/bin/python -m pytest -q
```

### Demo chats

| Chat | Shows |
|---|---|
| Ananya | Safe chat: the pre-filter skips the AI call |
| rahul_k_07 | Hinglish sextortion that escalates over several messages; a UPI ID gets masked |
| +91 98450 ••••• | Kannada-English stalking; the home address gets masked |
| Vikram | Hinglish threat of violence |
| SBl-Alerts | KYC/OTP phishing scam |
| Meera | Impersonation (a fake profile) |
| arjun.dev | A prompt-injection attempt hidden inside a harassing message |

To show live detection, switch the composer to **Them** and type a message as the other person.

### Layout

```
backend/app/main.py              FastAPI app, /analyze, /health, serves frontend/
backend/app/schemas.py           Strict request/response contract
backend/app/shield/prefilter.py  English / Hinglish / Kannada-English keyword rules
backend/app/shield/pii.py        PII masking with stable tokens
backend/app/shield/classifier.py Groq call, validation, retry, fallback
frontend/app.js                  Mock chat + Shield panel (plain HTML/JS)
frontend/import.js               Screenshot import: on-device OCR, bubble → sender parsing, review list
frontend/vault.js                Save to Vault (html2canvas), Vault screen, Verify animation, demo controls
backend/app/vault/store.py       SQLite store, hash chain, verification, demo tamper/reset
backend/app/vault/report.py      ReportLab PDF with Section 63 BSA certificate template
```

## Feature 2: Evidence Vault (built)

**Save to Vault** on a medium or high risk verdict seals the conversation:

```
panel ─► html2canvas capture of the whole chat (+ original screenshots, if imported)
      ─► POST /evidence ─► server timestamp (UTC, the phone's clock is recorded but not trusted)
                        ─► one record per screenshot + one per message
                        ─► hash = SHA-256(canonical JSON {record fields, server_timestamp, prev_hash})
                        ─► SQLite (backend/data/kavach.db) + screenshot files named by their SHA-256
```

| Endpoint | What it does |
|---|---|
| `POST /evidence` | Seal a capture (messages, screenshots, AI verdict) |
| `GET /evidence` | List records plus the current verification |
| `GET /evidence/verify` | Recompute every seal and link, and re-hash every screenshot file |
| `GET /evidence/report` | PDF: case summary, integrity banner, screenshots, hashed message tables, how-to-verify annex, Section 63 BSA certificate template (Parts A and B) |
| `POST /evidence/demo/tamper` | **Demo only** (`KAVACH_DEMO=1`): edit a flagged message directly in the DB, optionally re-sealing it |
| `POST /evidence/demo/reset` | **Demo only**: empty the Vault |

**What Verify catches:** an edited message, sender or timestamp (`tampered`); an edit where the attacker also recomputes that record's hash (that record is `rehashed`, and the next record shows `broken_link`); a deleted or reordered record; and a replaced or missing screenshot file (`file_changed`). Everything after the first break is marked unverified.

**Stage demo:** save two flagged chats, then open **Evidence Vault** and press **Verify chain** (all green). Press **Edit a message** (the threat now reads "ok sorry, just kidding"), then **Verify chain** again: that record turns red and everything after it turns amber. **Edit + re-seal it** shows that even a smarter attacker who recomputes the hash is caught by the next link. Then press **Export PDF report**. Press **Reset vault** between rehearsals.

**Honest limit:** someone with full server access could rewrite the *entire* chain from the edited record onward. The defence is to publish the **chain head** (shown in the Vault and the PDF) somewhere outside Kavach at save time, for example sent to a trusted contact or emailed to herself. That's on the roadmap, together with encryption at rest.

`window.kavach.results` holds the latest analysis for each chat. That is the hand-off point for **Draft Complaint** (Feature 3).
