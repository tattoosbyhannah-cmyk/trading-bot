#!/usr/bin/env python3
"""
Ratcheting Stop Loss — tightens stops as positions move in our favor.

Rules (ATR-based):
  +1x ATR from entry → stop moves to breakeven (entry price)
  +2x ATR from entry → stop locks in 1x ATR of profit
  +3x ATR from entry → stop locks in 2x ATR of profit
  Stop NEVER moves backward — only tightens.

Usage:
    python ratchet_stops.py           # check and ratchet all open positions
    python ratchet_stops.py --dry-run # show what would change without executing

Called by score_outcomes.py at 5:15 PM ET, or run standalone anytime.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)

RATCHET_LOG = Path(__file__).parent / "logs" / "stop_ratchets.jsonl"
OUTCOMES_LOG = Path(__file__).parent / "logs" / "decision_outcomes.jsonl"


def _log_ratchet(entry: dict):
    try:
        RATCHET_LOG.parent.mkdir(exist_ok=True)
        with open(RATCHET_LOG, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def _get_open_positions() -> list:
    """Get current open positions from Alpaca."""
    from alpaca.trading.client import TradingClient
    client = TradingClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
        paper=True,
    )
    positions = []
    for p in client.get_all_positions():
        positions.append({
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": "LONG" if float(p.qty) > 0 else "SHORT",
            "avg_entry": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
        })
    return positions


def _get_decision_for_symbol(symbol: str) -> dict:
    """Load the most recent decision for a symbol. SQL first, JSONL fallback.

    For proxy-routed positions (the position's symbol is the executed proxy, e.g.
    KOLD, while the originating decision was filed under the original symbol, e.g.
    UNG), fall back to looking up the originating decision via route_taken and
    return a synthesized "virtual decision" in proxy price space so the existing
    ratchet logic just works.
    """
    from db.queries import load_recent_decisions
    recent = load_recent_decisions(symbol, days=7)
    if recent:
        # Walk back through recent decisions newest → oldest and pick the first
        # one with a real LONG/SHORT direction and a non-null stop_loss. HOLD
        # decisions no longer carry a fabricated stop (per master_orchestrator
        # fix 2026-05-06); falling back this way preserves the prior real stop
        # as the ratchet's comparison basis. See docs/hold_behavior_audit.md.
        for d in reversed(recent):
            direction = (d.get("decision") or "").upper()
            stop = d.get("stop_loss")
            if direction in ("LONG", "SHORT") and stop is not None:
                return d
        # Only HOLDs in the window — fall through to other lookups (proxy / JSONL)

    # Proxy fallback: the position symbol may be the executed proxy of a routed
    # SHORT decision filed under the original symbol. Look it up via route_taken.
    proxy_decision = _get_proxy_decision(symbol)
    if proxy_decision:
        return proxy_decision

    # Extra fallback for very old decisions
    if not OUTCOMES_LOG.exists():
        return {}
    latest = None
    for line in OUTCOMES_LOG.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("symbol") == symbol:
                latest = r
        except Exception:
            continue
    return latest or {}


def _get_proxy_decision(proxy_symbol: str) -> dict:
    """Find the most recent originating decision whose route_taken.executed_symbol
    matches this proxy symbol, and return a synthesized decision in proxy price
    space. Returns {} if no such decision exists.

    The synthesized record has symbol=<proxy>, decision='LONG' (routed proxies
    are always LONG), entry_price=<proxy fill price>, stop_loss=<computed proxy
    stop>, stop_loss_pct=<distance in proxy space>. Original calc_run_id and
    decision_id are preserved so audit trails still resolve.
    """
    try:
        from db.connection import db_cursor
        with db_cursor(commit=False) as cur:
            cur.execute(
                """SELECT decision_id, calculation_run_id, symbol, route_taken, created_at
                   FROM decisions
                   WHERE route_taken->>'executed_symbol' = %s
                     AND created_at >= NOW() - INTERVAL '7 days'
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (proxy_symbol,),
            )
            row = cur.fetchone()
        if not row:
            return {}
        decision_id, calc_run_id, orig_symbol, route_taken, created_at = row
    except Exception:
        return {}

    if not isinstance(route_taken, dict):
        return {}
    proxy_stop_payload = route_taken.get("proxy_stop") or {}
    proxy_entry = proxy_stop_payload.get("proxy_entry")
    proxy_stop = proxy_stop_payload.get("computed_proxy_stop")
    if not proxy_entry or not proxy_stop:
        # Routed trade exists but proxy stop wasn't computed (e.g., no original
        # stop_loss on the decision). Cannot ratchet; return empty.
        return {}
    proxy_entry = float(proxy_entry)
    proxy_stop = float(proxy_stop)
    stop_loss_pct_proxy = round(abs(proxy_entry - proxy_stop) / proxy_entry * 100, 2)
    return {
        "decision_id": decision_id,
        "calculation_run_id": calc_run_id,
        "symbol": proxy_symbol,
        "decision": "LONG",  # routed proxies are always LONG
        "entry_price": proxy_entry,
        "stop_loss": proxy_stop,
        "stop_loss_pct": stop_loss_pct_proxy,
        "_proxy_routed_from": orig_symbol,  # diagnostic; ratchet ignores
    }


def _get_current_price(symbol: str) -> float:
    """Fetch latest price from Alpaca."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestTradeRequest
    client = StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )
    trade = client.get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=symbol))
    return float(trade[symbol].price)


def ratchet_all(dry_run: bool = False) -> int:
    """Check all open positions and ratchet stops where appropriate."""
    positions = _get_open_positions()
    if not positions:
        print("[RATCHET] No open positions.")
        return 0

    ratcheted = 0

    for pos in positions:
        symbol = pos["symbol"]
        entry = pos["avg_entry"]
        current = pos["current_price"]
        side = pos["side"]

        # Get ATR from the decision record
        decision = _get_decision_for_symbol(symbol)
        atr_pct = decision.get("stop_loss_pct")  # This was set from 2x ATR
        if not atr_pct or atr_pct <= 0:
            continue
        # Convert back to 1x ATR (stop was 2x ATR)
        atr_1x_pct = atr_pct / 2.0
        atr_dollars = entry * (atr_1x_pct / 100)

        current_stop = decision.get("stop_loss")
        if not current_stop:
            continue

        # Compute how far price has moved in our favor (in ATR units)
        if side == "LONG":
            favorable_move = current - entry
        else:
            favorable_move = entry - current

        atr_units = favorable_move / atr_dollars if atr_dollars > 0 else 0

        # Determine new stop level
        new_stop = None
        reason = None

        if atr_units >= 3.0:
            # Lock in 2x ATR of profit
            if side == "LONG":
                new_stop = round(entry + 2 * atr_dollars, 2)
            else:
                new_stop = round(entry - 2 * atr_dollars, 2)
            reason = f"+{atr_units:.1f} ATR → lock 2x ATR profit"
        elif atr_units >= 2.0:
            # Lock in 1x ATR of profit
            if side == "LONG":
                new_stop = round(entry + atr_dollars, 2)
            else:
                new_stop = round(entry - atr_dollars, 2)
            reason = f"+{atr_units:.1f} ATR → lock 1x ATR profit"
        elif atr_units >= 1.0:
            # Move to breakeven
            new_stop = round(entry, 2)
            reason = f"+{atr_units:.1f} ATR → breakeven stop"

        if new_stop is None:
            print(f"  {symbol} {side}: +{atr_units:.1f} ATR — no ratchet needed")
            continue

        # Check that new stop is tighter (never moves backward)
        if side == "LONG":
            if new_stop <= current_stop:
                print(f"  {symbol} LONG: new stop ${new_stop} <= current ${current_stop} — skip")
                continue
        else:
            if new_stop >= current_stop:
                print(f"  {symbol} SHORT: new stop ${new_stop} >= current ${current_stop} — skip")
                continue

        if dry_run:
            print(f"  [DRY RUN] {symbol} {side}: ${current_stop} → ${new_stop} "
                  f"(current ${current:.2f}, {reason})")
        else:
            print(f"  [RATCHET] {symbol} {side}: ${current_stop} → ${new_stop} ({reason})")
            _log_ratchet({
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "old_stop": current_stop,
                "new_stop": new_stop,
                "current_price": current,
                "entry_price": entry,
                "atr_dollars": round(atr_dollars, 2),
                "atr_units_favorable": round(atr_units, 2),
                "reason": reason,
            })

            # Update the decision record (JSONL + Postgres)
            _update_decision_stop(symbol, new_stop)

            # Submit the new stop order to the broker
            try:
                from paper_trading_executor import PaperTradingManager
                manager = PaperTradingManager()
                manager.set_stop_loss(symbol, new_stop)
            except Exception as e:
                print(f"  [RATCHET] Warning: broker stop update failed: {e}")

        ratcheted += 1

    return ratcheted


def _update_decision_stop(symbol: str, new_stop: float):
    """Update the most recent decision's stop_loss in both Postgres and JSONL."""
    # Postgres (primary)
    try:
        from db.queries import load_recent_decisions, update_decision
        recent = load_recent_decisions(symbol, days=7)
        if recent:
            did = recent[-1].get("decision_id")
            if did:
                update_decision(did, {"stop_loss": new_stop})
    except Exception:
        pass

    # JSONL (backup)
    if not OUTCOMES_LOG.exists():
        return
    lines = OUTCOMES_LOG.read_text().strip().split("\n")
    updated = []
    last_idx = None
    for i, line in enumerate(lines):
        if not line:
            updated.append(line)
            continue
        try:
            r = json.loads(line)
            if r.get("symbol") == symbol:
                last_idx = i
            updated.append(line)
        except Exception:
            updated.append(line)

    if last_idx is not None:
        r = json.loads(updated[last_idx])
        r["stop_loss"] = new_stop
        updated[last_idx] = json.dumps(r, default=str)
        OUTCOMES_LOG.write_text("\n".join(updated) + "\n")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    print(f"[RATCHET] Checking stops {'(dry run)' if dry_run else ''}...")
    count = ratchet_all(dry_run=dry_run)
    print(f"[RATCHET] {count} stops ratcheted.")
