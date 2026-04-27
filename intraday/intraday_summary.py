#!/usr/bin/env python3
"""
Intraday Daily Summary — runs after market close to summarize today's
intraday swing trades. Prints to stdout, emails via alert_manager,
and appends a daily summary line to logs/intraday_daily_summary.jsonl.

Usage:
    python intraday/intraday_summary.py           # today
    python intraday/intraday_summary.py 2026-04-21  # specific date
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv

_BOTDIR = Path(__file__).resolve().parent.parent
load_dotenv(_BOTDIR / ".env")

TRADE_LOG = _BOTDIR / "logs" / "intraday_trades.jsonl"
SUMMARY_LOG = _BOTDIR / "logs" / "intraday_daily_summary.jsonl"

sys.path.insert(0, str(_BOTDIR))


def _load_trades(date_str: str) -> list:
    if not TRADE_LOG.exists():
        return []
    trades = []
    for line in TRADE_LOG.read_text().strip().split("\n"):
        if not line:
            continue
        try:
            t = json.loads(line)
            if t.get("timestamp", "")[:10] == date_str:
                trades.append(t)
        except Exception:
            continue
    return trades


def summarize(date_str: str):
    trades = _load_trades(date_str)

    print(f"\n{'='*70}")
    print(f"📊 INTRADAY SUMMARY — {date_str}")
    print(f"{'='*70}")

    if not trades:
        print("  No intraday trades today.")
        summary_record = {
            "date": date_str,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl_pct": 0.0,
            "symbols": {},
        }
        _save_summary(summary_record)
        _send_email(date_str, "No intraday trades today.")
        return summary_record

    wins = [t for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("pnl_pct", 0) <= 0]
    all_pnl = [t["pnl_pct"] for t in trades]
    total_pnl = sum(all_pnl)

    # Per-symbol breakdown
    by_symbol = {}
    for t in trades:
        sym = t["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": []}
        by_symbol[sym]["trades"] += 1
        if t.get("pnl_pct", 0) > 0:
            by_symbol[sym]["wins"] += 1
        by_symbol[sym]["pnl"].append(t["pnl_pct"])

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    # Print
    print(f"\n  Total trades:  {len(trades)}")
    print(f"  Wins:          {len(wins)}")
    print(f"  Losses:        {len(losses)}")
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    print(f"  Win rate:      {win_rate:.1f}%")
    print(f"  Total P&L:     {total_pnl:+.3f}%")
    print(f"  Avg P&L:       {mean(all_pnl):+.3f}%")
    if wins:
        print(f"  Avg win:       {mean([t['pnl_pct'] for t in wins]):+.3f}%")
    if losses:
        print(f"  Avg loss:      {mean([t['pnl_pct'] for t in losses]):+.3f}%")

    print(f"\n  Exit reasons:")
    for reason, count in sorted(exit_reasons.items()):
        print(f"    {reason:12s}: {count}")

    print(f"\n  Per-symbol breakdown:")
    for sym in sorted(by_symbol):
        s = by_symbol[sym]
        sym_pnl = sum(s["pnl"])
        sym_wr = s["wins"] / s["trades"] * 100 if s["trades"] else 0
        print(f"    {sym:5s}: {s['trades']} trades | {s['wins']}W/{s['trades']-s['wins']}L "
              f"({sym_wr:.0f}%) | P&L: {sym_pnl:+.3f}%")

    # Individual trades
    print(f"\n  Trade log:")
    for t in trades:
        emoji = "✅" if t["pnl_pct"] > 0 else "❌"
        hold = t.get("hold_time_minutes", 0)
        print(f"    {emoji} {t['timestamp'][:16]} {t['symbol']:5s} {t['direction']:5s} "
              f"${t['entry_price']:.2f}→${t['exit_price']:.2f} "
              f"{t['pnl_pct']:+.3f}% ({t['exit_reason']}) {hold:.0f}min")

    print(f"{'='*70}\n")

    # Build summary record
    symbol_summary = {}
    for sym, s in by_symbol.items():
        symbol_summary[sym] = {
            "trades": s["trades"],
            "wins": s["wins"],
            "losses": s["trades"] - s["wins"],
            "total_pnl_pct": round(sum(s["pnl"]), 3),
        }

    summary_record = {
        "date": date_str,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "total_pnl_pct": round(total_pnl, 3),
        "avg_pnl_pct": round(mean(all_pnl), 3),
        "exit_reasons": exit_reasons,
        "symbols": symbol_summary,
    }

    _save_summary(summary_record)

    # Build email body
    lines = [
        f"Intraday Summary — {date_str}",
        f"",
        f"Trades: {len(trades)} ({len(wins)}W / {len(losses)}L) "
        f"Win rate: {win_rate:.1f}%",
        f"Total P&L: {total_pnl:+.3f}%",
        f"",
    ]
    for sym in sorted(by_symbol):
        s = by_symbol[sym]
        lines.append(f"  {sym}: {s['trades']} trades | P&L: {sum(s['pnl']):+.3f}%")
    lines.append("")
    for t in trades:
        emoji = "W" if t["pnl_pct"] > 0 else "L"
        lines.append(f"  [{emoji}] {t['symbol']} {t['direction']} "
                      f"${t['entry_price']:.2f}→${t['exit_price']:.2f} "
                      f"{t['pnl_pct']:+.3f}% ({t['exit_reason']})")
    _send_email(date_str, "\n".join(lines))

    return summary_record


def _save_summary(record: dict):
    try:
        SUMMARY_LOG.parent.mkdir(exist_ok=True)
        with open(SUMMARY_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        print(f"  [WARN] Failed to save summary: {e}")


def _send_email(date_str: str, body: str):
    try:
        from alert_manager import _send_email as send
        subject = f"[TRADING BOT] Intraday Summary — {date_str}"
        send(subject, body)
        print(f"  Email sent: {subject}")
    except Exception as e:
        print(f"  [WARN] Email failed: {e}")


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    summarize(date_str)
