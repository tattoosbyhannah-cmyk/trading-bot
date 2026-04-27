#!/usr/bin/env python3
"""
Emergency kill switch for trading bot.

Usage:
    python kill_switch.py engage [reason]        # Stop all trading + cancel orders
    python kill_switch.py engage --liquidate     # Stop + cancel + close all positions
    python kill_switch.py disengage              # Re-enable trading
    python kill_switch.py status                 # Check current state
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

KILL_SWITCH_FILE = Path(__file__).parent / "KILL_SWITCH"
LOG_FILE = Path(__file__).parent / "logs" / "agent_calls.jsonl"


def _log(entry: dict):
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def engage(reason: str, liquidate: bool = False):
    """Engage kill switch: write flag file, cancel orders, optionally liquidate."""
    from brokers.broker_factory import get_broker
    broker = get_broker("alpaca")

    timestamp = datetime.now().isoformat()
    flag_content = f"{timestamp} | {reason}"

    # 1. Write kill switch file FIRST (prevents new orders immediately)
    KILL_SWITCH_FILE.write_text(flag_content)
    print(f"[KILL] Kill switch ENGAGED at {timestamp}")
    print(f"[KILL] Reason: {reason}")

    # 2. Cancel all open orders
    try:
        broker.cancel_all_orders()
        print(f"[KILL] Cancelled all open orders")
    except Exception as e:
        print(f"[KILL] Warning: failed to cancel orders: {e}")

    # 3. Optionally liquidate all positions
    if liquidate:
        try:
            broker.close_all_positions()
            print(f"[KILL] Liquidated all positions")
        except Exception as e:
            print(f"[KILL] Warning: failed to liquidate: {e}")

    _log({
        "timestamp": timestamp,
        "agent": "kill_switch",
        "action": "ENGAGED",
        "reason": reason,
        "liquidated": liquidate,
    })

    # Email alert
    try:
        from alert_manager import alert_kill_switch_engaged
        alert_kill_switch_engaged(reason)
    except Exception:
        pass

    print(f"\n[KILL] Trading is HALTED. Run 'python kill_switch.py disengage' to resume.")


def disengage():
    """Remove kill switch file to re-enable trading."""
    if not KILL_SWITCH_FILE.exists():
        print("[KILL] Kill switch is not engaged. Nothing to do.")
        return

    old_content = KILL_SWITCH_FILE.read_text().strip()
    KILL_SWITCH_FILE.unlink()

    timestamp = datetime.now().isoformat()
    print(f"[KILL] Kill switch DISENGAGED at {timestamp}")
    print(f"[KILL] Was engaged: {old_content}")

    _log({
        "timestamp": timestamp,
        "agent": "kill_switch",
        "action": "DISENGAGED",
        "previous_reason": old_content,
    })


def status():
    """Check kill switch state and show open positions/orders."""
    if KILL_SWITCH_FILE.exists():
        content = KILL_SWITCH_FILE.read_text().strip()
        print(f"[KILL] Status: ENGAGED")
        print(f"[KILL] Details: {content}")
    else:
        print(f"[KILL] Status: DISENGAGED (trading enabled)")

    try:
        from brokers.broker_factory import get_broker
        broker = get_broker("alpaca")

        account = broker.get_account()
        positions = broker.get_all_positions()

        print(f"\n[PORTFOLIO] Equity: ${account['equity']:,.2f}")
        print(f"[PORTFOLIO] Cash: ${account['cash']:,.2f}")
        print(f"[PORTFOLIO] Open positions: {len(positions)}")
        for p in positions:
            print(f"  {p.symbol}: {p.qty} shares @ ${p.avg_entry_price:.2f} "
                  f"(P&L: ${p.unrealized_pnl:.2f})")
    except Exception as e:
        print(f"\n[PORTFOLIO] Could not fetch portfolio: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading bot kill switch")
    parser.add_argument("command", choices=["engage", "disengage", "status"])
    parser.add_argument("reason", nargs="*", default=["Manual kill switch"])
    parser.add_argument("--liquidate", action="store_true",
                        help="Also close all positions")

    args = parser.parse_args()

    if args.command == "engage":
        engage(" ".join(args.reason), liquidate=args.liquidate)
    elif args.command == "disengage":
        disengage()
    elif args.command == "status":
        status()
