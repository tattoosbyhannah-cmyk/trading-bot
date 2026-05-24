"""Backtest harness package.

Foundational primitives for running the daily-pipeline / majority-vote stack
against historical Alpaca bar data:

- portfolio_sim.SimPortfolio: stateful position tracker that produces the
  risk-context dict shape risk_gatekeeper.evaluate_trade_risk expects, in
  place of the live Alpaca portfolio fetch.

Not yet implemented (future work flagged in this session):
- L1 cache for Alpaca daily bars → parquet keyed by (symbol, date_range)
- L2 cache for computed indicators → parquet keyed by (symbol, as_of_date)
- L3 cache for Qwen agent responses → SQLite keyed by sha256 of agent prompt
- Cross-asset price panel for fundamentals_analyst regime context
- Top-level backtest runner that orchestrates SimPortfolio + as_of_date sweep

See the prompt that introduced this scaffold (2026-05-23) for the full design.
"""
