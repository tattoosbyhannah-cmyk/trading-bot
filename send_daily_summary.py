#!/usr/bin/env python3
"""Helper script called by daily_pipeline.sh to send the daily summary email."""

import json
import sys
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OUTCOMES_LOG = Path(__file__).parent / "logs" / "decision_outcomes.jsonl"


def main():
    errors_str = sys.argv[1] if len(sys.argv) > 1 else ""
    errors = [e for e in errors_str.strip().split("\\n") if e]

    today = datetime.now().strftime("%Y-%m-%d")

    # Gather today's decisions from outcomes log
    decisions = []
    if OUTCOMES_LOG.exists():
        for line in OUTCOMES_LOG.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("timestamp", "")[:10] == today:
                    decisions.append(r)
            except Exception:
                continue

    # Get portfolio state from Alpaca
    positions = []
    equity = 0.0
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            api_key=os.getenv("ALPACA_API_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            paper=True,
        )
        account = client.get_account()
        equity = float(account.equity)
        for p in client.get_all_positions():
            positions.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "pnl": float(p.unrealized_pl),
            })
    except Exception as e:
        errors.append(f"Portfolio fetch failed: {e}")
        equity = 0.0

    from alert_manager import alert_daily_summary
    alert_daily_summary(decisions, positions, equity, errors)


if __name__ == "__main__":
    main()
