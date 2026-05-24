"""
SQL-first query functions for the trading bot.

Every function tries Postgres first, falls back to JSONL if DB is unavailable.
SQL is the source of truth. JSONL is the emergency fallback.

All SQL results pass through _normalize_rows() from db.connection, which maps
DB column names (e.g. created_at) to canonical keys (e.g. timestamp).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOGS = Path(__file__).resolve().parent.parent / "logs"


def _pg_available():
    try:
        from db.connection import is_available
        return is_available()
    except Exception:
        return False


def _fetch_rows(sql: str, params: tuple = ()) -> list:
    """Execute SQL, return list of normalized dicts. Returns [] on failure."""
    from db.connection import db_cursor, _normalize_rows
    with db_cursor(commit=False) as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return _normalize_rows([dict(zip(cols, row)) for row in rows])


# ── 1. Recent decisions for a symbol ─────────────────────────────────────────

def load_recent_decisions(symbol: str, days: int = 3, as_of_date: str = None) -> list:
    """Load recent decisions. SQL first, JSONL fallback.

    When as_of_date is provided (ISO 'YYYY-MM-DD'), the window is
    [as_of - days, as_of] — used in backtest to avoid leaking future decisions
    into context for the day being simulated.
    """
    # Anchor the window: backtest uses as_of_date end-of-day; live uses now()
    if as_of_date:
        try:
            anchor = datetime.fromisoformat(as_of_date).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        except Exception:
            anchor = datetime.now(timezone.utc)
    else:
        anchor = datetime.now(timezone.utc)

    try:
        lo = (anchor - timedelta(days=days)).isoformat()
        hi = anchor.isoformat()
        rows = _fetch_rows("""
            SELECT decision_id, calculation_run_id, symbol, decision,
                   confidence, entry_price, stop_loss, stop_loss_pct,
                   price_target, price_target_pct, position_size_pct,
                   literature_winner, hit_stop, hit_target,
                   return_1d_pct, created_at
            FROM decisions
            WHERE symbol = %s AND created_at >= %s AND created_at <= %s
            ORDER BY created_at
        """, (symbol, lo, hi))
        if rows:
            return rows
    except Exception:
        pass

    # JSONL fallback
    path = LOGS / "decision_outcomes.jsonl"
    if not path.exists():
        return []
    lo_str = (anchor - timedelta(days=days)).isoformat()
    hi_str = anchor.isoformat()
    results = []
    try:
        for line in path.read_text().strip().split("\n"):
            if not line:
                continue
            r = json.loads(line)
            ts = r.get("timestamp", "")
            if r.get("symbol") == symbol and lo_str <= ts <= hi_str:
                results.append(r)
    except Exception:
        pass
    return results


# ── 2. All decisions (for scoring) ───────────────────────────────────────────

def load_all_decisions() -> list:
    """Load all decision outcomes. SQL first, JSONL fallback."""
    try:
        rows = _fetch_rows("""
            SELECT decision_id, calculation_run_id, symbol, asset_class,
                   decision, confidence, entry_price, stop_loss, stop_loss_pct,
                   price_target, price_target_pct, position_size_pct,
                   literature_winner, agent_consensus,
                   price_1d, price_5d, price_30d,
                   return_1d_pct, return_5d_pct, return_30d_pct,
                   hit_stop, hit_target, stop_hit_day, target_hit_day,
                   opportunity_cost_pct, scored_at, created_at
            FROM decisions ORDER BY created_at
        """)
        if rows:
            for d in rows:
                # Parse agent_consensus JSON string
                if isinstance(d.get("agent_consensus"), str):
                    try:
                        d["agent_consensus"] = json.loads(d["agent_consensus"])
                    except Exception:
                        pass
            return rows
    except Exception:
        pass

    # JSONL fallback
    path = LOGS / "decision_outcomes.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


# ── 3. Update a decision (scoring) ──────────────────────────────────────────

def update_decision(decision_id: str, updates: dict):
    """Update a decision record in Postgres. Also updates JSONL via rewrite."""
    try:
        from db.connection import db_cursor
        set_clauses = []
        params = []
        for key, val in updates.items():
            set_clauses.append(f"{key} = %s")
            params.append(val)
        params.append(decision_id)
        with db_cursor() as cur:
            cur.execute(
                f"UPDATE decisions SET {', '.join(set_clauses)} WHERE decision_id = %s",
                params
            )
    except Exception:
        pass


# ── 4. Agent calls for audit trail ───────────────────────────────────────────

def load_agent_calls_by_calc_id(calc_id: str) -> list:
    """Load agent calls for a calculation_run_id. SQL first."""
    try:
        rows = _fetch_rows("""
            SELECT agent, symbol, model_tier, status, latency_sec,
                   decision_fields, error, created_at, calculation_run_id
            FROM reasoning_runs
            WHERE calculation_run_id LIKE %s
            ORDER BY created_at
        """, (calc_id + "%",))
        if rows:
            for d in rows:
                if isinstance(d.get("decision_fields"), str):
                    try:
                        d["decision_fields"] = json.loads(d["decision_fields"])
                    except Exception:
                        pass
            return rows
    except Exception:
        pass

    # JSONL fallback
    path = LOGS / "agent_calls.jsonl"
    if not path.exists():
        return []
    results = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
            cid = r.get("calculation_run_id", "")
            if cid and cid.startswith(calc_id):
                results.append(r)
        except Exception:
            continue
    return results


# ── 5. Decisions by calc_id (audit trail) ────────────────────────────────────

def load_decisions_by_calc_id(calc_id: str) -> list:
    """Load decisions for a calculation_run_id."""
    try:
        return _fetch_rows("""
            SELECT * FROM decisions
            WHERE calculation_run_id LIKE %s
            ORDER BY created_at
        """, (calc_id + "%",))
    except Exception:
        pass

    # JSONL fallback
    path = LOGS / "decision_outcomes.jsonl"
    if not path.exists():
        return []
    results = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("calculation_run_id", "").startswith(calc_id):
                results.append(r)
        except Exception:
            continue
    return results


# ── 6. Fills by calc_id (audit trail) ────────────────────────────────────────

def load_fills_by_calc_id(calc_id: str) -> list:
    try:
        return _fetch_rows("SELECT * FROM fill_records WHERE calculation_run_id LIKE %s",
                           (calc_id + "%",))
    except Exception:
        pass

    path = LOGS / "fill_records.jsonl"
    if not path.exists():
        return []
    results = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("calculation_run_id", "").startswith(calc_id):
                results.append(r)
        except Exception:
            continue
    return results


# ── 7. Ratchets by calc_id ───────────────────────────────────────────────────

def load_ratchets_by_calc_id(calc_id: str) -> list:
    try:
        return _fetch_rows("SELECT * FROM stop_ratchets WHERE calculation_run_id LIKE %s",
                           (calc_id + "%",))
    except Exception:
        pass

    path = LOGS / "stop_ratchets.jsonl"
    if not path.exists():
        return []
    results = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("calculation_run_id", "").startswith(calc_id):
                results.append(r)
        except Exception:
            continue
    return results


# ── 8. Latest calculation_run_id ──────────────────────────────────────────────

def find_latest_calc_id(symbol: str = None) -> str:
    """Find the most recent calculation_run_id. SQL first, JSONL fallback."""
    try:
        if symbol:
            rows = _fetch_rows("""
                SELECT calculation_run_id FROM decisions
                WHERE symbol = %s AND calculation_run_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """, (symbol.upper(),))
        else:
            rows = _fetch_rows("""
                SELECT calculation_run_id FROM decisions
                WHERE calculation_run_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
            """)
        if rows and rows[0].get("calculation_run_id"):
            return rows[0]["calculation_run_id"]
    except Exception:
        pass

    # JSONL fallback
    path = LOGS / "decision_outcomes.jsonl"
    if not path.exists():
        return ""
    for line in reversed(path.read_text().strip().split("\n")):
        if not line:
            continue
        try:
            r = json.loads(line)
            cid = r.get("calculation_run_id", "")
            if cid and (not symbol or r.get("symbol") == symbol.upper()):
                return cid
        except Exception:
            continue
    return ""


# ── 9. Today's agent call counts ─────────────────────────────────────────────

def load_todays_agent_calls() -> list:
    """Load today's agent calls for health check."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        rows = _fetch_rows("""
            SELECT agent, symbol, model_tier, status, latency_sec,
                   decision_fields, calculation_run_id, created_at
            FROM reasoning_runs
            WHERE created_at::date = %s
            ORDER BY created_at
        """, (today,))
        if rows is not None:
            for d in rows:
                if isinstance(d.get("decision_fields"), str):
                    try:
                        d["decision_fields"] = json.loads(d["decision_fields"])
                    except Exception:
                        pass
            return rows
    except Exception:
        pass

    # JSONL fallback
    path = LOGS / "agent_calls.jsonl"
    if not path.exists():
        return []
    results = []
    for line in path.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("timestamp", "").startswith(today):
                results.append(r)
        except Exception:
            continue
    return results
