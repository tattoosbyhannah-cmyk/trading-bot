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
