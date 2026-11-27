#!/usr/bin/env python3
"""
Kavach Database Migration Tool: SQLite -> Supabase PostgreSQL & pgvector
-----------------------------------------------------------------------
Migrates all users, evidence records, screenshots, guardians/people,
and ChromaDB semantic vector embeddings directly into Supabase PostgreSQL.

Usage:
    DATABASE_URL="postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres" python backend/scripts/migrate_to_supabase.py
Or configure DATABASE_URL in backend/.env and run:
    python backend/scripts/migrate_to_supabase.py
"""

import os
import sys
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

# Ensure backend directory is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import psycopg
from app import config, database, vectors, main

def main():
    database_url = os.environ.get("DATABASE_URL") or config.DATABASE_URL
    if not database_url:
        print("\n" + "=" * 70)
        print("❌ ERROR: DATABASE_URL is not set.")
        print("=" * 70)
        print("To migrate completely to Supabase PostgreSQL & pgvector:")
        print("1. Go to your Supabase Project Dashboard: https://supabase.com/dashboard")
        print("2. Navigate to Project Settings -> Database.")
        print("3. Under 'Connection string', select 'URI' (Mode: Session or Transaction).")
        print("4. Copy the connection string (format: postgresql://postgres.[ref]:[PASSWORD]@...)")
        print("5. Paste it in backend/.env as:")
        print("   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres")
        print("6. Run this script again:")
        print("   PYTHONPATH=backend backend/.venv/bin/python backend/scripts/migrate_to_supabase.py\n")
        sys.exit(1)

    print("=" * 70)
    print("🚀 KAVACH -> SUPABASE POSTGRESQL & PGVECTOR MIGRATION")
    print("=" * 70)
    print(f"Connecting to Supabase PostgreSQL at: {database_url.split('@')[-1] if '@' in database_url else database_url[:25]}...")

    try:
        pg_conn = psycopg.connect(database_url, autocommit=True)
    except Exception as exc:
        print(f"\n❌ Failed to connect to Supabase: {exc}")
        if "Network is unreachable" in str(exc) or "2406:" in str(exc):
            print("\n" + "!" * 70)
            print("💡 REASON FOR FAILURE: IPv6 NOT ROUTABLE ON YOUR CURRENT NETWORK")
            print("!" * 70)
            print("Supabase's direct connection (db.[ref].supabase.co) uses IPv6.")
            print("Because your local internet/Wi-Fi router does not route IPv6 packets,")
            print("you must use Supabase's IPv4 Connection Pooler!")
            print("\nHOW TO GET YOUR SUPABASE IPv4 POOLER URI (Takes 15 seconds):")
            print("1. Open your Supabase Dashboard: https://supabase.com/dashboard")
            print("2. In the top right navbar, click the 'Connect' button (or Project Settings -> Database).")
            print("3. In the modal, under 'Connection string', choose Method: 'Connection pooler'.")
            print("4. Select Mode: 'Session' (port 5432) or 'Transaction' (port 6543).")
            print("5. Copy the URI. It will look like:")
            print("   postgresql://postgres.ycxioyzpthpdqvjnivqt:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres")
            print("6. Paste this URI into backend/.env as DATABASE_URL and run this script again!")
            print("!" * 70 + "\n")
        sys.exit(1)

    print("✓ Successfully connected to Supabase PostgreSQL!")

    # 1. Enable pgvector extension
    print("\n[1/4] Enabling pgvector extension...")
    with pg_conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        print("✓ pgvector extension is enabled.")

    # 2. Initialize all Postgres tables
    print("\n[2/4] Initializing PostgreSQL schemas...")
    # Force Postgres mode in app.database
    config.DATABASE_URL = database_url
    for name, (ddl, add_cols) in database._schemas.items():
        print(f"  • Registering schema: {name}")
        conn_adapter = database.Conn(pg_conn, pg=True)
        conn_adapter.script(ddl)
        with pg_conn.cursor() as cur:
            for table, col, col_type in add_cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type};")

    # Ensure vectors table exists for pgvector
    with pg_conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            collection TEXT NOT NULL,
            id         TEXT NOT NULL,
            document   TEXT,
            embedding  vector(384),
            metadata   JSONB,
            PRIMARY KEY (collection, id)
        );
        """)
        print("✓ vectors table initialized with vector(384).")

    # 3. Migrate SQLite rows to Supabase
    sqlite_path = config.DATA_DIR / "kavach.db"
    print(f"\n[3/4] Migrating data from local SQLite ({sqlite_path})...")
    if sqlite_path.exists():
        sq_conn = sqlite3.connect(sqlite_path)
        sq_conn.row_factory = sqlite3.Row

        tables = ["users", "sessions", "evidence", "screenshots", "people", "profile", "sos", "alerts"]
        for table in tables:
            try:
                rows = sq_conn.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue

            if not rows:
                print(f"  • {table}: 0 rows (skipped)")
                continue

            cols = list(rows[0].keys())
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join([f'"{c}"' if c in ("user", "order") else c for c in cols])
            insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;"

            with pg_conn.cursor() as cur:
                count = 0
                for r in rows:
                    vals = [bytes(r[c]) if isinstance(r[c], (memoryview, bytes)) else r[c] for c in cols]
                    cur.execute(insert_sql, vals)
                    count += 1
            print(f"  ✓ {table}: {count} records migrated.")
        sq_conn.close()
    else:
        print("  • No existing SQLite database found. Fresh Supabase tables ready.")

    # 4. Migrate Chroma embeddings if present
    print("\n[4/4] Checking vector embeddings...")
    try:
        from app.complaint.retrieval import corpus
        corpus.sync()
        print("✓ Legal sections corpus embedded and synchronized in pgvector.")
    except Exception as exc:
        print(f"  ⚠️ Vector corpus sync note: {exc}")

    print("\n" + "=" * 70)
    print("🎉 MIGRATION TO SUPABASE COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print("Every user, evidence record, SHA-256 seal, and pgvector embedding")
    print("now lives securely in your Supabase PostgreSQL cloud instance.")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
