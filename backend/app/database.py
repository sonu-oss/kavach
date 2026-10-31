"""The one database behind Kavach: Postgres (e.g. Supabase) when DATABASE_URL is set,
otherwise a SQLite file in DATA_DIR (local runs and tests).

Modules write plain SQL with `?` placeholders and register their tables with `schema()`;
this module adapts both to the engine in use:
- `?` becomes `%s`, `IS ?` (NULL-safe compare) becomes `IS NOT DISTINCT FROM %s`,
  and the reserved column name `user` is quoted.
- In table definitions INTEGER becomes BIGINT (Telegram chat ids exceed 32 bits),
  REAL becomes DOUBLE PRECISION, BLOB becomes BYTEA.
Connections run in autocommit; callers that need a transaction send BEGIN / COMMIT.
"""

import re
import sqlite3
import threading
from contextlib import contextmanager

from app import config

_schemas: dict[str, tuple[str, list[tuple[str, str, str]]]] = {}
_ready: set[tuple[str, str]] = set()
_pools: dict[str, object] = {}
_init_lock = threading.Lock()


def is_postgres() -> bool:
    return bool(config.DATABASE_URL)


def schema(name: str, ddl: str, add_columns: list[tuple[str, str, str]] = ()) -> None:
    """Registers tables (CREATE TABLE IF NOT EXISTS ...) and columns added since release:
    (table, column, type). Created on first use for each database."""
    _schemas[name] = (ddl, list(add_columns))


class Row(dict):
    """A row readable by column name, and by position like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row(r) -> Row | None:
    if r is None:
        return None
    return Row({k: bytes(v) if isinstance(v, memoryview) else v for k, v in dict(r).items()})


class _Cursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self) -> Row | None:
        return _row(self._cur.fetchone())

    def fetchall(self) -> list[Row]:
        return [_row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


_USER_COL = re.compile(r"(?<![\w\-'\".])user(?![\w\-'\"])")
_IS_PARAM = re.compile(r"\bIS\s+\?")


def _pg_sql(sql: str) -> str:
    sql = _IS_PARAM.sub("IS NOT DISTINCT FROM ?", sql)
    sql = _USER_COL.sub('"user"', sql)
    sql = sql.replace("BEGIN IMMEDIATE", "BEGIN")
    return sql.replace("%", "%%").replace("?", "%s")


def _pg_ddl(ddl: str) -> str:
    ddl = re.sub(r"\bINTEGER\b", "BIGINT", ddl)
    ddl = re.sub(r"\bREAL\b", "DOUBLE PRECISION", ddl)
    ddl = re.sub(r"\bBLOB\b", "BYTEA", ddl)
    return _USER_COL.sub('"user"', ddl)


class Conn:
    def __init__(self, raw, pg: bool):
        self.raw, self.pg = raw, pg

    def execute(self, sql: str, params=()) -> _Cursor:
        if self.pg:
            return _Cursor(self.raw.execute(_pg_sql(sql), tuple(params)))
        return _Cursor(self.raw.execute(sql, tuple(params)))

    def script(self, ddl: str) -> None:
        if not self.pg:
            self.raw.executescript(ddl)
            return
        # Comments go first: they may contain ";" (the table definitions have no "--" inside strings).
        ddl = re.sub(r"--[^\n]*", "", ddl)
        for stmt in ddl.split(";"):
            if stmt.strip():
                self.raw.execute(_pg_ddl(stmt))

    def columns(self, table: str) -> set[str]:
        if self.pg:
            return {r[0] for r in self.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=? AND table_schema=current_schema()",
                (table,))}
        return {r["name"] for r in self.raw.execute(f"PRAGMA table_info({table})")}


def _target() -> str:
    return config.DATABASE_URL or str(config.DATA_DIR / "kavach.db")


def _pool(url: str):
    if url not in _pools:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        # prepare_threshold=None: Supabase's pooler can't keep prepared statements between clients.
        _pools[url] = ConnectionPool(
            url, min_size=1, max_size=8, open=True, timeout=20,
            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        )
    return _pools[url]


def _ensure(conn: Conn) -> None:
    target = _target()
    todo = [n for n in _schemas if (target, n) not in _ready]
    if not todo:
        return
    with _init_lock:
        for name in todo:
            if (target, name) in _ready:
                continue
            ddl, add_columns = _schemas[name]
            conn.script(ddl)
            for table, column, kind in add_columns:
                if column not in conn.columns(table):
                    conn.raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} "
                                     f"{_pg_ddl(kind) if conn.pg else kind}")
            _ready.add((target, name))


@contextmanager
def connect():
    if is_postgres():
        with _pool(config.DATABASE_URL).connection() as raw:
            conn = Conn(raw, True)
            _ensure(conn)
            yield conn
    else:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(config.DATA_DIR / "kavach.db", isolation_level=None, timeout=10)
        raw.row_factory = sqlite3.Row
        try:
            conn = Conn(raw, False)
            _ensure(conn)
            yield conn
        finally:
            raw.close()


def rows(sql: str, *args) -> list[Row]:
    with connect() as c:
        return c.execute(sql, args).fetchall()


def one(sql: str, *args) -> Row | None:
    r = rows(sql, *args)
    return r[0] if r else None


def run(sql: str, *args) -> None:
    with connect() as c:
        c.execute(sql, args)


def truncate_all_for_tests() -> None:
    """Postgres test runs share one database: empty every Kavach table between tests."""
    with connect() as c:
        tables = [r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"
            " AND table_type='BASE TABLE'")]
        if tables:
            c.raw.execute("TRUNCATE " + ", ".join(tables))
