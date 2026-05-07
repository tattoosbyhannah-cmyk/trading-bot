"""
Dual Log Writer — writes to both JSONL (backward compat) and Postgres.

If Postgres is unavailable, silently falls back to JSONL-only.

Usage:
    from db.log_writer import log_reasoning_run, log_decision, log_fill, ...
"""

import json
import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _append_jsonl(filepath: Path, entry: dict):
    try:
        with open(filepath, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def _pg_available():
    try:
        from db.connection import is_available
        return is_available()
    except Exception:
        return False


def log_reasoning_run(entry: dict):
    """Log an agent call to both JSONL and Postgres."""
    _append_jsonl(LOG_DIR / "agent_calls.jsonl", entry)

    if not _pg_available():
        return
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO reasoning_runs
                    (calculation_run_id, symbol, agent, model_tier, status,
                     latency_sec, decision_fields, error, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                entry.get("calculation_run_id"),
                entry.get("symbol"),
                entry.get("agent"),
                entry.get("model_tier"),
                entry.get("status", "ok"),
                entry.get("latency_sec"),
                json.dumps(entry.get("decision_fields", {})),
                entry.get("error"),
                entry.get("timestamp", datetime.now().isoformat()),
            ))
    except Exception as e:
        logging.debug(f"Postgres log_reasoning_run failed: {e}")


_hold_audit_column_ready = False


def _ensure_hold_audit_column():
    """Idempotent: add decisions.hold_audit JSONB if missing. Cached after first success."""
    global _hold_audit_column_ready
    if _hold_audit_column_ready:
        return True
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS hold_audit JSONB")
        _hold_audit_column_ready = True
    except Exception as e:
        logging.debug(f"ALTER decisions ADD hold_audit failed: {e}")
    return _hold_audit_column_ready


def log_decision(entry: dict):
    """Log a decision outcome to both JSONL and Postgres."""
    _append_jsonl(LOG_DIR / "decision_outcomes.jsonl", entry)

    if not _pg_available():
        return
    try:
        _ensure_hold_audit_column()
        from db.connection import db_cursor
        hold_audit = entry.get("hold_audit")
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO decisions
                    (decision_id, calculation_run_id, symbol, asset_class,
                     decision, confidence, entry_price, stop_loss, stop_loss_pct,
                     price_target, price_target_pct, position_size_pct,
                     literature_winner, agent_consensus, hold_audit, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO NOTHING
            """, (
                entry.get("decision_id"),
                entry.get("calculation_run_id"),
                entry.get("symbol"),
                entry.get("asset_class"),
                entry.get("decision"),
                entry.get("confidence"),
                entry.get("entry_price"),
                entry.get("stop_loss"),
                entry.get("stop_loss_pct"),
                entry.get("price_target"),
                entry.get("price_target_pct"),
                entry.get("position_size_pct"),
                entry.get("literature_winner"),
                json.dumps(entry.get("agent_consensus", {}), default=str),
                json.dumps(hold_audit, default=str) if hold_audit else None,
                entry.get("timestamp", datetime.now().isoformat()),
            ))
    except Exception as e:
        logging.debug(f"Postgres log_decision failed: {e}")


def log_fill(entry: dict):
    """Log a fill record to both JSONL and Postgres."""
    _append_jsonl(LOG_DIR / "fill_records.jsonl", entry)

    if not _pg_available():
        return
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO fill_records
                    (calculation_run_id, symbol, side, decision_price,
                     expected_price, filled_price, quantity, slippage_bps,
                     spread_estimate_bps, total_cost_bps, order_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                entry.get("calculation_run_id"),
                entry.get("symbol"),
                entry.get("side"),
                entry.get("decision_price"),
                entry.get("expected_price"),
                entry.get("filled_price"),
                entry.get("quantity"),
                entry.get("slippage_bps"),
                entry.get("spread_estimate_bps"),
                entry.get("total_cost_bps"),
                entry.get("order_id"),
            ))
    except Exception as e:
        logging.debug(f"Postgres log_fill failed: {e}")


def log_stop_ratchet(entry: dict):
    """Log a stop ratchet event."""
    _append_jsonl(LOG_DIR / "stop_ratchets.jsonl", entry)

    if not _pg_available():
        return
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO stop_ratchets
                    (calculation_run_id, symbol, side, old_stop, new_stop,
                     current_price, entry_price, atr_dollars,
                     atr_units_favorable, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                entry.get("calculation_run_id"),
                entry.get("symbol"),
                entry.get("side"),
                entry.get("old_stop"),
                entry.get("new_stop"),
                entry.get("current_price"),
                entry.get("entry_price"),
                entry.get("atr_dollars"),
                entry.get("atr_units_favorable"),
                entry.get("reason"),
            ))
    except Exception as e:
        logging.debug(f"Postgres log_stop_ratchet failed: {e}")


def log_intraday_trade(entry: dict):
    """Log an intraday trade."""
    _append_jsonl(LOG_DIR / "intraday_trades.jsonl", entry)

    if not _pg_available():
        return
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO intraday_trades
                    (symbol, direction, entry_price, exit_price, exit_reason,
                     hold_time_minutes, pnl_pct, signal_strength,
                     signal_components, shares, intraday_atr_pct,
                     take_profit_pct, stop_loss_pct)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                entry.get("symbol"),
                entry.get("direction"),
                entry.get("entry_price"),
                entry.get("exit_price"),
                entry.get("exit_reason"),
                entry.get("hold_time_minutes"),
                entry.get("pnl_pct"),
                entry.get("signal_strength"),
                json.dumps(entry.get("signal_components", {})),
                entry.get("shares"),
                entry.get("intraday_atr_pct"),
                entry.get("take_profit_pct"),
                entry.get("stop_loss_pct"),
            ))
    except Exception as e:
        logging.debug(f"Postgres log_intraday_trade failed: {e}")


def log_intraday_signal(entry: dict):
    """Log an intraday signal."""
    _append_jsonl(LOG_DIR / "intraday_signals.jsonl", entry)

    if not _pg_available():
        return
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO intraday_signals
                    (symbol, direction, strength, components, entry_price, reason)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                entry.get("symbol"),
                entry.get("direction"),
                entry.get("strength"),
                json.dumps(entry.get("components", {})),
                entry.get("entry_price"),
                entry.get("reason"),
            ))
    except Exception as e:
        logging.debug(f"Postgres log_intraday_signal failed: {e}")
