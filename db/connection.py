"""
Database connection pool for the trading bot.

Usage:
    from db.connection import get_conn, put_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        put_conn(conn)

Or as context manager:
    from db.connection import db_cursor

    with db_cursor() as cur:
        cur.execute("SELECT 1")
"""

import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(_ENV if _ENV.exists() else None)

# Map of DB column names → canonical dict keys used by the rest of the bot.
_DB_TO_CANONICAL = {
    "created_at": "timestamp",
    # add future renames here, one place
}


def _normalize_row(row: dict) -> dict:
    """Translate DB column names and types to canonical forms.

    Column renames: created_at → timestamp (via _DB_TO_CANONICAL)
    Type coercions: Decimal → float, datetime → ISO string (for timestamp key)
    """
    if row is None:
        return None
    from decimal import Decimal
    out = {}
    for k, v in row.items():
        canon = _DB_TO_CANONICAL.get(k, k)
        # Coerce Decimal → float at the boundary (all NUMERIC columns)
        if isinstance(v, Decimal):
            v = float(v)
        # Convert datetime objects to ISO strings for the timestamp key
        elif hasattr(v, "isoformat") and canon == "timestamp":
            v = v.isoformat()
        out[canon] = v
    return out


def _normalize_rows(rows: list) -> list:
    return [_normalize_row(r) for r in rows]


_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        import psycopg2
        from psycopg2 import pool as pg_pool
        database_url = os.getenv("DATABASE_URL",
                                  "postgresql://trading:trading_dev_2026@localhost:5432/trading")
        _pool = pg_pool.ThreadedConnectionPool(1, 10, database_url)
    return _pool


def get_conn():
    return _get_pool().getconn()


def put_conn(conn):
    _get_pool().putconn(conn)


@contextmanager
def db_cursor(commit=True):
    """Context manager that yields a cursor, commits on success, rolls back on error."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def is_available() -> bool:
    """Check if Postgres is available."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        put_conn(conn)
        return True
    except Exception:
        return False
