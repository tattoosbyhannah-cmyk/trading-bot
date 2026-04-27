#!/usr/bin/env python3
"""
Audit Trail — trace the full computation chain for a given calculation_run_id.

Searches all JSONL log files for entries matching the run ID and prints
the complete sequence of computations that produced a trading decision.

Usage:
    python audit_trail.py <calculation_run_id>
    python audit_trail.py <partial_id>          # matches prefix
    python audit_trail.py latest                # most recent run
    python audit_trail.py latest USO            # most recent USO run
"""

import json
import sys
from datetime import datetime
from pathlib import Path

BOTDIR = Path(__file__).parent
LOG_FILES = {
    "agent_calls": BOTDIR / "logs" / "agent_calls.jsonl",
    "decisions": BOTDIR / "logs" / "decision_outcomes.jsonl",
    "fills": BOTDIR / "logs" / "fill_records.jsonl",
    "ratchets": BOTDIR / "logs" / "stop_ratchets.jsonl",
    "intraday_trades": BOTDIR / "logs" / "intraday_trades.jsonl",
    "intraday_signals": BOTDIR / "logs" / "intraday_signals.jsonl",
}
PLAYBOOK = BOTDIR / "playbook" / "daily_playbook.json"


def _search_logs(calc_id: str) -> dict:
    """Search all logs for entries matching this calculation_run_id. SQL first."""
    from db.queries import (load_agent_calls_by_calc_id, load_decisions_by_calc_id,
                            load_fills_by_calc_id, load_ratchets_by_calc_id)
    return {
        "agent_calls": load_agent_calls_by_calc_id(calc_id),
        "decisions": load_decisions_by_calc_id(calc_id),
        "fills": load_fills_by_calc_id(calc_id),
        "ratchets": load_ratchets_by_calc_id(calc_id),
        "intraday_trades": [],  # TODO: add SQL query
        "intraday_signals": [],
    }


def _find_latest_id(symbol: str = None) -> str:
    """Find the most recent calculation_run_id. SQL first."""
    try:
        from db.connection import db_cursor
        with db_cursor(commit=False) as cur:
            if symbol:
                cur.execute("""
                    SELECT calculation_run_id FROM decisions
                    WHERE symbol = %s AND calculation_run_id IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                """, (symbol.upper(),))
            else:
                cur.execute("""
                    SELECT calculation_run_id FROM decisions
                    WHERE calculation_run_id IS NOT NULL
                    ORDER BY created_at DESC LIMIT 1
                """)
            row = cur.fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass

    # JSONL fallback
    path = LOG_FILES["decisions"]
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


def _print_chain(calc_id: str):
    results = _search_logs(calc_id)

    total = sum(len(v) for v in results.values())
    if total == 0:
        print(f"No entries found for calculation_run_id: {calc_id}")
        return

    # Header
    print(f"\n{'='*70}")
    print(f"  AUDIT TRAIL: {calc_id}")
    print(f"{'='*70}")

    # Agent calls (chronological)
    agents = sorted(results["agent_calls"], key=lambda e: e.get("timestamp", ""))
    if agents:
        print(f"\n  AGENT CALLS ({len(agents)}):")
        print(f"  {'─'*66}")
        for a in agents:
            lat = a.get("latency_sec", "?")
            fields = a.get("decision_fields", {})
            summary = " ".join(f"{k}={v}" for k, v in list(fields.items())[:4])
            print(f"  {a['timestamp'][11:19]}  {a['agent']:25}  {lat:>6}s  {a['status']:4}  {summary[:40]}")

    # Decision outcome
    decisions = results["decisions"]
    if decisions:
        print(f"\n  DECISION RECORD:")
        print(f"  {'─'*66}")
        for d in decisions:
            print(f"  Symbol:     {d['symbol']}")
            print(f"  Direction:  {d['decision']}")
            print(f"  Confidence: {d.get('confidence')}")
            print(f"  Entry:      ${d.get('entry_price', 0):.2f}")
            print(f"  Stop:       ${d.get('stop_loss', 0) or 0:.2f} ({d.get('stop_loss_pct', '?')}%)")
            print(f"  Target:     ${d.get('price_target', 0) or 0:.2f} ({d.get('price_target_pct', '?')}%)")
            print(f"  Decision ID: {d.get('decision_id', '?')}")
            ret1d = d.get("return_1d_pct")
            if ret1d is not None:
                print(f"  1d Return:  {ret1d:+.2f}%")
            if d.get("hit_stop"):
                print(f"  STOP HIT on day {d.get('stop_hit_day')}")
            if d.get("hit_target"):
                print(f"  TARGET HIT on day {d.get('target_hit_day')}")

    # Fills
    fills = results["fills"]
    if fills:
        print(f"\n  FILL RECORDS ({len(fills)}):")
        print(f"  {'─'*66}")
        for f in fills:
            print(f"  {f['timestamp'][:19]}  {f['symbol']} {f['side']}  "
                  f"${f['filled_price']:.2f}  slip={f['slippage_bps']:.1f}bps  "
                  f"spread={f['spread_estimate_bps']:.1f}bps")

    # Ratchets
    ratchets = results["ratchets"]
    if ratchets:
        print(f"\n  STOP RATCHETS ({len(ratchets)}):")
        print(f"  {'─'*66}")
        for r in ratchets:
            print(f"  {r['timestamp'][:19]}  {r['symbol']}  "
                  f"${r['old_stop']:.2f} → ${r['new_stop']:.2f}  {r.get('reason', '')}")

    # Playbook
    if PLAYBOOK.exists():
        try:
            pb = json.loads(PLAYBOOK.read_text())
            for sym, entry in pb.get("symbols", {}).items():
                if entry.get("calculation_run_id") == calc_id:
                    print(f"\n  PLAYBOOK ENTRY ({sym}):")
                    print(f"  {'─'*66}")
                    print(f"  Direction:  {entry['direction']}")
                    print(f"  Scalping:   {entry.get('allow_scalping')}")
                    print(f"  Max trades: {entry.get('max_intraday_trades')}")
        except Exception:
            pass

    # Summary
    print(f"\n  CHAIN SUMMARY:")
    print(f"  {'─'*66}")
    print(f"  Agent calls:     {len(agents)}")
    print(f"  Decisions:       {len(decisions)}")
    print(f"  Fills:           {len(fills)}")
    print(f"  Ratchets:        {len(ratchets)}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python audit_trail.py <calculation_run_id>")
        print("       python audit_trail.py latest [SYMBOL]")
        sys.exit(1)

    arg = sys.argv[1]
    if arg == "latest":
        symbol = sys.argv[2] if len(sys.argv) > 2 else None
        calc_id = _find_latest_id(symbol)
        if not calc_id:
            print("No calculation_run_id found in decision_outcomes.jsonl")
            sys.exit(1)
        print(f"Latest run: {calc_id}")
    else:
        calc_id = arg

    _print_chain(calc_id)
