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
3. **She checks the result:** she can switch Them/You, edit misread text (hard-to-read lines are outlined in amber), and delete or add lines. She must also choose **which app** the chat is from, and can set **when it happened** (defaults to today, can't be in the future). Then she presses **Scan with Kavach**. The app and date are sealed into every Vault record and used in the evidence PDF, the complaint letter and the portal fields; without a stated date, the portal tab says the date shown is only the save date.
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
backend/app/complaint/           Legal corpus, hybrid retrieval, rerank + draft + citation checker, complaint PDF
backend/app/llm.py               Shared Groq JSON call with rate-limit fallback
frontend/complaint.js            Complaint screen, her details, laws tab, cybercrime.gov.in hand-off
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

## Feature 3: Complaint Assistant (built)

Press **Draft Complaint** in the Shield panel (it seals the chat in the Vault first if needed) or **Draft complaint from this evidence** on a Vault capture.

```
sealed Vault capture ─► PII masked ─► hybrid retrieval over legal_corpus.json (top 8)
   BM25 (keywords) + embeddings (fastembed bge-small, local) + threat-type prior, fused with RRF
─► AI reranker scores each candidate 0-100 against the facts; only 40+ is cited, below 65 says "Consult a lawyer"
─► AI drafts the complaint, allowed to cite ONLY those section ids
─► citation checker strips any other section or act it mentions (including IPC numbers) and reports them
─► letter + evidence list with SHA-256 seals + cybercrime.gov.in hand-off
```

| Endpoint | What it does |
|---|---|
| `POST /complaint` `{capture_id}` | Draft from sealed evidence: sections with confidence, removed citations, retrieval trace, portal fields |
| `POST /complaint/pdf` | Her edited letter as a PDF with an annex of cited sections and the evidence chain head |
| `GET /legal/sections` | The whole corpus the assistant is allowed to cite |

**Privacy:** her name, phone, email and address live only in her browser (localStorage) and are filled into the letter there. The AI sees masked chat text only.

**The cybercrime.gov.in hand-off** (the **File the report** tab): 112, 1930 and 181 call buttons, the suggested category route, the incident date, platform, a portal-safe description (at least 200 characters, none of the characters the portal rejects), suspect identifiers pulled from the chat (handle, UPI ID, phone, email, links), and the evidence PDF to upload. Kavach can't submit for her: the portal needs her own OTP login.

**Legal corpus** (`backend/app/complaint/legal_corpus.json`): 13 BNS 2023 and 6 IT Act 2000 sections, written as plain-language summaries with source links. **Before the demo, check every section number, title and punishment against indiacode.nic.in and set `"verified": true`.** The UI shows "Not yet checked against the official Gazette" until then. Some sections are there on purpose so the reranker can reject them; for example, BNS 124 (acid attack) must not be cited for a *threat* to throw acid.

**First run:** the embedding model (~130 MB) downloads to `backend/data/models/`. Without internet, retrieval falls back to BM25 plus the threat-type prior. If Groq is down, a template draft is produced with every section marked "consult a lawyer".
