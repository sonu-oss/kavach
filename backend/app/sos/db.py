"""SQLite storage for SOS: her trusted contacts, registered responders, SOS alerts and deliveries.

Lives in the same data/kavach.db file as the Evidence Vault, in separate tables.
"""

import sqlite3
import threading
from contextlib import contextmanager

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,              -- 'contact' (her trusted contact) | 'responder'
    name             TEXT NOT NULL,
    phone            TEXT,
    email            TEXT,
    telegram_chat_id INTEGER,                    -- set when they tap Start on the bot
    link_code        TEXT NOT NULL UNIQUE,       -- one-time code inside their Telegram link
    verified         INTEGER NOT NULL DEFAULT 0, -- responders: approved by an admin
    opted_in         INTEGER NOT NULL DEFAULT 1, -- responders: want to receive alerts
    lat              REAL,                       -- responders: where they usually are
    lng              REAL,
    location_at      TEXT,
    created_at       TEXT NOT NULL,
    owner            TEXT                        -- contacts: the account they belong to (NULL: before accounts)
);
CREATE TABLE IF NOT EXISTS profile (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sos (
    id            TEXT PRIMARY KEY,
    user          TEXT NOT NULL,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL,
    accuracy      REAL NOT NULL,
    approx_lat    REAL NOT NULL,
    approx_lng    REAL NOT NULL,
    trigger       TEXT,
    source        TEXT,
    status        TEXT NOT NULL,                 -- active | cancelled | expired
    track_token   TEXT NOT NULL UNIQUE,
    token_expires REAL NOT NULL,
    base_url      TEXT,                          -- public address used in links sent out
    has_location  INTEGER NOT NULL DEFAULT 1,    -- 0 until her phone gets a GPS fix
    created_ts    REAL NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    sos_id          TEXT NOT NULL,
    person_id       TEXT NOT NULL,
    role            TEXT NOT NULL,               -- 'contact' | 'responder'
    status          TEXT NOT NULL,               -- contacts: notified · responders: pending | accepted | declined
    at              TEXT,
    distance_m      REAL,
    telegram        TEXT,                        -- delivery status shown to her
    email           TEXT,
    live_message_id INTEGER,                     -- Telegram live-location message to keep updating
    PRIMARY KEY (sos_id, person_id)
);
"""

lock = threading.RLock()


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA_DIR / "kavach.db", isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sos)")}
        if "has_location" not in cols:  # databases created before this column existed
            conn.execute("ALTER TABLE sos ADD COLUMN has_location INTEGER NOT NULL DEFAULT 1")
        if "owner" not in {r["name"] for r in conn.execute("PRAGMA table_info(people)")}:  # before accounts
            conn.execute("ALTER TABLE people ADD COLUMN owner TEXT")
        yield conn
    finally:
        conn.close()


def rows(sql: str, *args) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args)]


def one(sql: str, *args) -> dict | None:
    r = rows(sql, *args)
    return r[0] if r else None


def run(sql: str, *args) -> None:
    with connect() as c:
        c.execute(sql, args)
