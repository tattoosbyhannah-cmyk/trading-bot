"""
Backtest Runner — iterate trading days × symbols, replay the decision pipeline.

Glue layer that ties together everything:
  - run_context.backtest_run() flips the global isolation switch
  - SimPortfolio holds simulated positions and cash
  - load_eod_prices marks the portfolio each day (via L1 cache)
  - run_complete_trading_analysis is invoked per (symbol, day) with the
    portfolio_context already built, so risk_gatekeeper sees the simulated
    portfolio instead of live Alpaca
  - decision_to_fills converts each decision into fills the sim ingests

Usage:
    from backtest.runner import run_backtest
    summary = run_backtest(
        symbols=["USO"],
        start="2026-04-15", end="2026-04-20",
        run_id="20260427_uso_5day",
    )

Outputs land under logs/backtest_runs/{run_id}/:
    decisions.jsonl, fills.jsonl, agent_calls.jsonl, summary.json
"""

import json
from dataclasses import asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from backtest.run_context import backtest_run, backtest_run_dir
from backtest.portfolio_sim import SimPortfolio
from backtest.eod_prices import load_eod_prices
from backtest.fill_converter import decision_to_fills


def _iter_trading_days(start: date, end: date) -> Iterable[date]:
    """Yield weekdays in [start, end]. We don't filter NYSE holidays here —
    Alpaca will simply return no bar, and load_eod_prices skips empties."""
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            yield d
        d += timedelta(days=1)


def _to_date(x) -> date:
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    return datetime.fromisoformat(str(x)).date()


def run_backtest(symbols, start, end, run_id: str,
                  starting_cash: float = 100_000.0,
                  num_runs: int = 1,
                  verbose: bool = True) -> dict:
    """Replay the pipeline for each (symbol, trading_day) and return a summary.

    Args:
        symbols: iterable of tickers.
        start, end: ISO date strings or date objects (inclusive).
        run_id: identifier used for the output dir and isolation flag.
        starting_cash: initial portfolio cash.
        num_runs: passed through to majority-vote (1 = no vote, single run).
        verbose: print per-day progress.

    Returns:
        {symbols, days, decisions, fills, ending_value, ending_cash}
    """
    start = _to_date(start)
    end = _to_date(end)
    symbols = [s.upper() for s in symbols]

    sim = SimPortfolio(starting_cash=starting_cash)

    decisions_log = []
    fills_log = []

    with backtest_run(run_id):
        out_dir = backtest_run_dir()
        decisions_path = out_dir / "decisions.jsonl"
        fills_path = out_dir / "fills.jsonl"

        for day in _iter_trading_days(start, end):
            day_iso = day.isoformat()
            prices = load_eod_prices(symbols, day)
            if verbose:
                print(f"\n📅 {day_iso}  prices: "
                      + ", ".join(f"{s}=${p:.2f}" for s, p in prices.items()))

            for sym in symbols:
                if sym not in prices:
                    if verbose:
                        print(f"  ⏭  {sym}: no EOD price for {day_iso} (holiday?)")
                    continue

                ctx = sim.to_risk_context(sym, as_of_date=day, prices=prices)

                # Lazy import so risk_gatekeeper/master_orchestrator load only
                # when the runner actually runs (faster CLI startup).
                if num_runs > 1:
                    from majority_vote_orchestrator import run_majority_vote
                    decision = run_majority_vote(
                        sym, num_runs=num_runs, execute=False,
                        as_of_date=day_iso, portfolio_context=ctx,
                    )
                else:
                    from master_orchestrator import run_complete_trading_analysis
                    decision = run_complete_trading_analysis(
                        sym, as_of_date=day_iso, portfolio_context=ctx,
                    )

                # Persist the decision
                dec_record = {
                    "date": day_iso,
                    "symbol": sym,
                    "final_decision": getattr(decision, "final_decision", None),
                    "confidence": getattr(decision, "confidence", None),
                    "position_size": getattr(decision, "position_size", None),
                    "entry_price": getattr(decision, "entry_price", None),
                    "stop_loss": getattr(decision, "stop_loss", None),
                    "price_target": getattr(decision, "price_target", None),
                    "price_used_for_fill": prices.get(sym),
                }
                decisions_log.append(dec_record)
                with open(decisions_path, "a") as f:
                    f.write(json.dumps(dec_record, default=str) + "\n")

                # Convert + apply fills using the day's close as fill price
                fills = decision_to_fills(decision, sim, as_of_date=day,
                                          price=prices.get(sym))
                for fill in fills:
                    sim.apply_fill(fill)
                    fill_record = {
                        "date": day_iso,
                        "symbol": fill.symbol,
                        "qty": fill.qty,
                        "price": fill.price,
                        "side": fill.side,
                    }
                    fills_log.append(fill_record)
                    with open(fills_path, "a") as f:
                        f.write(json.dumps(fill_record, default=str) + "\n")

                if verbose:
                    fdir = getattr(decision, "final_decision", "?")
                    print(f"  ▶ {sym}: {fdir}  size={getattr(decision, 'position_size', 0)}%  "
                          f"fills={len(fills)}")

        # End-of-run summary
        final_prices = load_eod_prices(symbols, end)
        ending_value = sim.total_portfolio_value(final_prices)
        summary = {
            "run_id": run_id,
            "symbols": symbols,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "starting_cash": starting_cash,
            "ending_cash": round(sim.cash, 2),
            "ending_value": round(ending_value, 2),
            "return_pct": round((ending_value - starting_cash) / starting_cash * 100, 2),
            "num_decisions": len(decisions_log),
            "num_fills": len(fills_log),
            "open_positions": {
                sym: {"qty": pos.qty, "avg_price": pos.avg_price}
                for sym, pos in sim.positions.items()
            },
        }
        with open(out_dir / "summary.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)

        if verbose:
            print(f"\n{'='*70}")
            print(f"BACKTEST SUMMARY  run_id={run_id}")
            print(f"  {len(decisions_log)} decisions, {len(fills_log)} fills")
            print(f"  starting=${starting_cash:,.0f}  ending=${ending_value:,.0f}  "
                  f"({summary['return_pct']:+.2f}%)")
            print(f"  output: {out_dir}")
            print(f"{'='*70}")

    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--starting-cash", type=float, default=100_000.0)
    ap.add_argument("--num-runs", type=int, default=1,
                     help="1 = single run, >1 = majority vote")
    args = ap.parse_args()
    run_backtest(args.symbols, args.start, args.end, args.run_id,
                 starting_cash=args.starting_cash, num_runs=args.num_runs)
