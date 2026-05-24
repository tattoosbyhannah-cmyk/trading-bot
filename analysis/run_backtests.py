#!/usr/bin/env python3
"""Run intraday-signal backtests across the active universe and write
structured JSON results to analysis/backtest_results/{YYYY-MM-DD}/.

Loads the active symbols list from config/instrument_registry.py (same source
the daily pipeline uses). Falls back to USO/UNG/GLD if the registry lookup fails.

Usage:
    python analysis/run_backtests.py                 # 10-day backtest, all active symbols
    python analysis/run_backtests.py --days 30       # extend horizon
    python analysis/run_backtests.py --symbols USO,UNG  # subset
"""

import json
import sys
from datetime import datetime
from pathlib import Path

TRADING_BOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADING_BOT_DIR))

from intraday.signal_engine import _run_backtest


def _active_symbols():
    try:
        from config.instrument_registry import registry
        syms = registry.get_active_symbols()
        if syms:
            return syms
    except Exception as e:
        print(f"[WARN] instrument registry lookup failed: {e}; falling back")
    return ["USO", "UNG", "GLD"]


def main():
    days = 10
    symbols = None
    if "--days" in sys.argv:
        i = sys.argv.index("--days")
        if i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
    if "--symbols" in sys.argv:
        i = sys.argv.index("--symbols")
        if i + 1 < len(sys.argv):
            symbols = [s.strip().upper() for s in sys.argv[i + 1].split(",")]
    if not symbols:
        symbols = _active_symbols()

    date_tag = datetime.now().strftime("%Y-%m-%d")
    out_dir = TRADING_BOT_DIR / "analysis" / "backtest_results" / date_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=" * 70)
    print(f"BACKTEST RUNNER — {datetime.now().isoformat(timespec='seconds')}")
    print(f"  symbols:   {symbols}")
    print(f"  days:      {days}")
    print(f"  output:    {out_dir}/")
    print(f"=" * 70)
    print()

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "symbols": symbols,
        "horizon_bars": 15,
        "results": {},
    }

    for sym in symbols:
        print(f"\n{'─' * 70}")
        print(f"Running backtest: {sym}")
        print(f"{'─' * 70}")
        out_path = out_dir / f"{sym}.json"
        try:
            result = _run_backtest(sym, days=days, output_path=str(out_path))
        except Exception as e:
            print(f"[ERROR] {sym}: {type(e).__name__}: {e}")
            summary["results"][sym] = {"status": "error", "error": str(e)}
            continue
        if not result:
            summary["results"][sym] = {"status": "no_data"}
            continue
        # Compact per-symbol summary for the index file
        summary["results"][sym] = {
            "status": "ok",
            "n_bars": result["n_bars"],
            "date_range": result["date_range"],
            "by_direction": {
                d: {
                    "n_signals": v["n_signals"],
                    "wins": v.get("wins"),
                    "win_rate_pct": v.get("win_rate_pct"),
                    "avg_directional_return_pct": v.get("avg_directional_return_pct"),
                }
                for d, v in result["by_direction"].items()
            },
        }

    summary_path = out_dir / "_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print()
    print("=" * 70)
    print(f"SUMMARY → {summary_path}")
    print("=" * 70)
    for sym, r in summary["results"].items():
        if r["status"] != "ok":
            print(f"  {sym}: {r['status']}")
            continue
        ld = r["by_direction"].get("LONG", {})
        sd = r["by_direction"].get("SHORT", {})
        print(f"  {sym:4}  LONG: {ld.get('n_signals', 0):>3} sigs "
              f"win_rate={ld.get('win_rate_pct', 0):>5}%  ret={ld.get('avg_directional_return_pct', 0):>+6.3f}%  |  "
              f"SHORT: {sd.get('n_signals', 0):>3} sigs "
              f"win_rate={sd.get('win_rate_pct', 0):>5}%  ret={sd.get('avg_directional_return_pct', 0):>+6.3f}%")


if __name__ == "__main__":
    main()
