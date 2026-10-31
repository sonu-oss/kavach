"""Evidence Vault: an append-only, hash-chained evidence log in SQLite.

Every record (one chat message, or one screenshot) is sealed with

    hash = SHA256( canonical_json({ "record": <content + metadata>,
                                    "server_timestamp": <UTC, set here>,
                                    "prev_hash": <hash of the previous record> }) )

so changing any stored field, reordering records, or swapping a screenshot file
makes verification fail from that record onwards. The hash is recomputed from
the stored columns, so an edit made straight in the database is caught too.

Each account has its own chain (records are linked to the previous record of the same
owner), so one person's Vault never shows, or depends on, anyone else's.
"""

import base64
import binascii
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import config, database
from app.auth import context
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
    hash               TEXT NOT NULL,
    owner              TEXT                    -- account id; NULL for records sealed before accounts
);
CREATE INDEX IF NOT EXISTS evidence_capture ON evidence(capture_id);
CREATE TABLE IF NOT EXISTS screenshots (
    sha256 TEXT PRIMARY KEY,                   -- the file's SHA-256, also sealed in the evidence row
    ext    TEXT NOT NULL,                      -- png | jpg
    data   BLOB NOT NULL
);
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

database.schema("vault", _SCHEMA, add_columns=[
    ("evidence", "incident_date", "TEXT"),  # vaults created before this column existed
    ("evidence", "owner", "TEXT"),  # vaults created before accounts (not hashed: old seals stay valid)
])
connect = database.connect


def shots_dir() -> Path:
    """Where screenshots were kept as files before they moved into the database (still read)."""
    return config.DATA_DIR / "screenshots"


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

    # Decode screenshots before touching the chain; their bytes are stored with the records.
    shots, images = [], {}
    for s in item.screenshots:
        raw, ext = _decode_image(s.data_url)
        digest = hashlib.sha256(raw).hexdigest()
        images[digest] = (ext, raw)
        shots.append((s.kind, digest))

    owner = context.current()
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
            if conn.pg:  # one sealer at a time across server processes, so chains never fork
                conn.execute("SELECT pg_advisory_xact_lock(7261)")
            for digest, (ext, raw) in images.items():
                conn.execute("INSERT INTO screenshots (sha256, ext, data) VALUES (?, ?, ?) ON CONFLICT (sha256) DO NOTHING",
                             (digest, ext, raw))
            # Ids are global; the chain links to this account's own previous record.
            top = conn.execute("SELECT MAX(id) AS m FROM evidence").fetchone()["m"]
            next_id = (top or 0) + 1
            last = conn.execute("SELECT hash FROM evidence WHERE owner IS ? ORDER BY id DESC LIMIT 1", (owner,)).fetchone()
            prev = last["hash"] if last else GENESIS
            ts = _now()  # one server timestamp for the whole capture
            for row in rows:
                row.update(id=next_id, server_timestamp=ts, prev_hash=prev, owner=owner)
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
    """The signed-in account's records, in chain order."""
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM evidence WHERE owner IS ? ORDER BY id", (context.current(),))]


def owns_screenshot(digest: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 AS x FROM evidence WHERE screenshot_sha256=? AND owner IS ? LIMIT 1",
                            (digest, context.current())).fetchone() is not None


def claim_unowned(owner: str) -> None:
    """Hands records sealed before accounts existed to the first account."""
    with _lock, connect() as conn:
        conn.execute("UPDATE evidence SET owner=? WHERE owner IS NULL", (owner,))


def screenshot(digest: str) -> tuple[bytes, str] | None:
    """(image bytes, "png" | "jpg") for a sealed screenshot, or None."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        return None
    with connect() as conn:
        r = conn.execute("SELECT ext, data FROM screenshots WHERE sha256=?", (digest,)).fetchone()
    if r:
        return bytes(r["data"]), r["ext"]
    for ext in ("png", "jpg"):  # sealed before screenshots moved into the database
        p = shots_dir() / f"{digest}.{ext}"
        if p.exists():
            return p.read_bytes(), ext
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
            shot = screenshot(row["screenshot_sha256"] or "")
            if shot is None or hashlib.sha256(shot[0]).hexdigest() != row["screenshot_sha256"]:
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
    owner = context.current()
    with _lock, connect() as conn:
        if record_id is None:
            # Never the newest record: a re-sealed last record has nothing after it to expose it.
            msgs = conn.execute(
                "SELECT id FROM evidence WHERE kind='message' AND owner IS ?"
                " AND id < (SELECT MAX(id) FROM evidence WHERE owner IS ?) ORDER BY flagged DESC, id", (owner, owner)
            ).fetchall()
            if not msgs:
                raise EvidenceError("vault has no messages to tamper with")
            record_id = msgs[0]["id"]
        row = conn.execute("SELECT * FROM evidence WHERE id=? AND owner IS ?", (record_id, owner)).fetchone()
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
    """Deletes this account's records and its screenshots. Her choice, e.g. after the case is closed."""
    with _lock, connect() as conn:
        conn.execute("DELETE FROM evidence WHERE owner IS ?", (context.current(),))
        # The same image sealed by another account stays.
        conn.execute("DELETE FROM screenshots WHERE sha256 NOT IN"
                     " (SELECT screenshot_sha256 FROM evidence WHERE screenshot_sha256 IS NOT NULL)")
        still_used = {r[0] for r in conn.execute("SELECT screenshot_sha256 FROM evidence WHERE screenshot_sha256 IS NOT NULL")}
    for f in shots_dir().glob("*"):
        if f.stem not in still_used:
            f.unlink()


demo_reset = clear
