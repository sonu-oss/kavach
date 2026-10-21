"""Evidence Vault: an append-only, hash-chained evidence log in SQLite.

Every record (one chat message, or one screenshot) is sealed with

    hash = SHA256( canonical_json({ "record": <content + metadata>,
                                    "server_timestamp": <UTC, set here>,
                                    "prev_hash": <hash of the previous record> }) )

so changing any stored field, reordering records, or swapping a screenshot file
makes verification fail from that record onwards. The hash is recomputed from
the stored columns, so an edit made straight in the database is caught too.
"""

import base64
import binascii
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app import config
from app.vault.schemas import EvidenceIn

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    id                 INTEGER PRIMARY KEY,
    capture_id         TEXT NOT NULL,
    kind               TEXT NOT NULL,          -- 'message' | 'screenshot'
    platform           TEXT NOT NULL,
    conversation       TEXT NOT NULL,
    url                TEXT,
    sender             TEXT,
    message_text       TEXT,
    message_time       TEXT,
    message_ref        TEXT,
    flagged            INTEGER NOT NULL DEFAULT 0,
    screenshot_kind    TEXT,
    screenshot_sha256  TEXT,
    analysis_json      TEXT,
    client_captured_at TEXT,
    incident_date      TEXT,                   -- YYYY-MM-DD, stated by the complainant
    server_timestamp   TEXT NOT NULL,
    prev_hash          TEXT NOT NULL,
    hash               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_capture ON evidence(capture_id);
"""

_HASHED_FIELDS = (
    "id", "capture_id", "kind", "platform", "conversation", "url", "sender", "message_text",
    "message_time", "message_ref", "flagged", "screenshot_kind", "screenshot_sha256",
    "analysis_json", "client_captured_at",
)
# Added after the first release: hashed only when present, so records sealed before it still verify.
_OPTIONAL_HASHED_FIELDS = ("incident_date",)

_lock = threading.Lock()
_DATA_URL = re.compile(r"^data:image/(png|jpeg);base64,(.+)$", re.DOTALL)


class EvidenceError(ValueError):
    pass


# ---------- hashing ----------

def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def record_hash(row: dict) -> str:
    record = {f: row[f] for f in _HASHED_FIELDS}
    for f in _OPTIONAL_HASHED_FIELDS:
        if row.get(f) is not None:
            record[f] = row[f]
    record["flagged"] = bool(record["flagged"])
    sealed = {"record": record, "server_timestamp": row["server_timestamp"], "prev_hash": row["prev_hash"]}
    return hashlib.sha256(canonical(sealed)).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- storage ----------

def _db_path() -> Path:
    return config.DATA_DIR / "kavach.db"


def shots_dir() -> Path:
    return config.DATA_DIR / "screenshots"


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(evidence)")}
        if "incident_date" not in cols:  # vaults created before this column existed
            conn.execute("ALTER TABLE evidence ADD COLUMN incident_date TEXT")
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _decode_image(data_url: str) -> tuple[bytes, str]:
    m = _DATA_URL.match(data_url)
    if not m:
        raise EvidenceError("screenshot must be a PNG or JPEG data URL")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except binascii.Error as exc:
        raise EvidenceError("screenshot is not valid base64") from exc
    return raw, "png" if m.group(1) == "png" else "jpg"


def save_capture(item: EvidenceIn) -> dict:
    """Seal one 'Save to Vault' action: its screenshots first, then each message."""
    capture_id = uuid.uuid4().hex[:12]
    analysis_json = canonical(item.analysis).decode() if item.analysis else None
    base = {
        "capture_id": capture_id,
        "platform": item.platform,
        "conversation": item.conversation,
        "url": item.url,
        "analysis_json": analysis_json,
        "client_captured_at": item.client_captured_at,
        "incident_date": item.incident_date.isoformat() if item.incident_date else None,
    }

    # Decode and store screenshot files before touching the chain.
    shots = []
    shots_dir().mkdir(parents=True, exist_ok=True)
    for s in item.screenshots:
        raw, ext = _decode_image(s.data_url)
        digest = hashlib.sha256(raw).hexdigest()
        (shots_dir() / f"{digest}.{ext}").write_bytes(raw)
        shots.append((s.kind, digest))

    rows = [
        {**base, "kind": "screenshot", "sender": None, "message_text": None, "message_time": None,
         "message_ref": None, "flagged": 0, "screenshot_kind": kind, "screenshot_sha256": digest}
        for kind, digest in shots
    ] + [
        {**base, "kind": "message", "sender": m.sender, "message_text": m.text, "message_time": m.time,
         "message_ref": m.ref, "flagged": int(m.flagged), "screenshot_kind": None, "screenshot_sha256": None}
        for m in item.messages
    ]

    with _lock, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            last = conn.execute("SELECT id, hash FROM evidence ORDER BY id DESC LIMIT 1").fetchone()
            next_id = (last["id"] + 1) if last else 1
            prev = last["hash"] if last else GENESIS
            ts = _now()  # one server timestamp for the whole capture
            for row in rows:
                row.update(id=next_id, server_timestamp=ts, prev_hash=prev)
                row["hash"] = record_hash(row)
                cols = ", ".join(row)
                conn.execute(f"INSERT INTO evidence ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
                prev = row["hash"]
                next_id += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return {"capture_id": capture_id, "server_timestamp": ts, "records": len(rows), "head_hash": prev}


def all_rows() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM evidence ORDER BY id")]


def screenshot_path(digest: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    for ext in ("png", "jpg"):
        p = shots_dir() / f"{digest}.{ext}"
        if p.exists():
            return p
    return None


# ---------- verification ----------

def verify(rows: list[dict] | None = None) -> dict:
    """Recompute the whole chain. The first failure marks everything after it as unverified."""
    rows = all_rows() if rows is None else rows
    results = []
    prev = GENESIS
    first_break = None
    for row in rows:
        problem = None
        if row["prev_hash"] != prev:
            problem = "broken_link"  # previous record was changed and re-hashed, or records reordered
        elif record_hash(row) != row["hash"]:
            problem = "tampered"  # this record's content no longer matches its seal
        elif row["kind"] == "screenshot":
            path = screenshot_path(row["screenshot_sha256"] or "")
            if path is None or sha256_file(path) != row["screenshot_sha256"]:
                problem = "file_changed"
        if problem == "broken_link" and results and first_break is None:
            # The previous record still matches its own (recomputed) hash, but not the
            # hash this record sealed in: it was edited and re-hashed after the fact.
            results[-1]["status"] = "rehashed"
            first_break = results[-1]["id"]
        if problem and first_break is None:
            first_break = row["id"]
        status = problem or ("after_break" if first_break is not None else "ok")
        results.append({"id": row["id"], "status": status})
        prev = row["hash"]

    return {
        "ok": first_break is None,
        "count": len(rows),
        "first_broken_id": first_break,
        "head_hash": rows[-1]["hash"] if rows else None,
        "checked_at": _now(),
        "records": results,
    }


# ---------- demo controls (KAVACH_DEMO=1 only) ----------

def demo_tamper(record_id: int | None, rehash: bool) -> dict:
    with _lock, connect() as conn:
        if record_id is None:
            # Never the newest record: a re-sealed last record has nothing after it to expose it.
            msgs = conn.execute(
                "SELECT id FROM evidence WHERE kind='message' AND id < (SELECT MAX(id) FROM evidence)"
                " ORDER BY flagged DESC, id"
            ).fetchall()
            if not msgs:
                raise EvidenceError("vault has no messages to tamper with")
            record_id = msgs[0]["id"]
        row = conn.execute("SELECT * FROM evidence WHERE id=?", (record_id,)).fetchone()
        if row is None or row["kind"] != "message":
            raise EvidenceError("record not found or not a message")
        row = dict(row)
        before = row["message_text"]
        row["message_text"] = "ok sorry, just kidding 🙂"  # attacker softens the threat
        conn.execute("UPDATE evidence SET message_text=? WHERE id=?", (row["message_text"], record_id))
        if rehash:
            conn.execute("UPDATE evidence SET hash=? WHERE id=?", (record_hash(row), record_id))
    return {"record_id": record_id, "before": before, "after": row["message_text"], "rehash": rehash}


def clear() -> None:
    """Deletes every record and screenshot. Her choice, e.g. after the case is closed."""
    with _lock, connect() as conn:
        conn.execute("DELETE FROM evidence")
    for f in shots_dir().glob("*"):
        f.unlink()


demo_reset = clear
