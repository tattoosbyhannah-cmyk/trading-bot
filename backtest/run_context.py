"""Backtest-run context — module-level state for "we're running a backtest right now."

Sets a `backtest_run_id` that all write paths consult to decide whether to:
  - skip Postgres INSERTs (don't pollute the live decisions table)
  - route JSONL appends to logs/backtest_runs/{run_id}/*.jsonl instead of the
    live audit files
  - skip the playbook regen (or write to backtest/playbook_snapshots/{run_id}/)
  - fail-loud instead of silently falling back to live Alpaca portfolio fetch

Why a module-level context rather than threading through state: the write
paths (db/log_writer.py, agent_logger.py, majority_vote_orchestrator's playbook
write) are scattered across many call sites that don't pass a state dict.
The contextvar pattern matches the existing agent_logger.calculation_run_id
convention so the codebase reads consistently.

Usage from a backtest runner:

    from backtest.run_context import set_backtest_run_id, clear_backtest_run_id
    run_id = "20260523_USO_5day"
    set_backtest_run_id(run_id)
    try:
        # ... run the pipeline ...
    finally:
        clear_backtest_run_id()

Or as a context manager:

    from backtest.run_context import backtest_run
    with backtest_run("20260523_USO_5day"):
        run_complete_trading_analysis("USO", state_overrides={...})
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Optional


_active_run_id: Optional[str] = None


def set_backtest_run_id(run_id: Optional[str]) -> None:
    """Mark the current process as running a backtest with this run_id.
    Pass None to clear (back to live mode)."""
    global _active_run_id
    _active_run_id = run_id


def get_backtest_run_id() -> Optional[str]:
    """Return the active backtest run_id, or None if in live mode."""
    return _active_run_id


def is_backtest_mode() -> bool:
    return _active_run_id is not None


def clear_backtest_run_id() -> None:
    set_backtest_run_id(None)


@contextmanager
def backtest_run(run_id: str):
    """Context manager: set the run_id, restore prior value on exit."""
    global _active_run_id
    prior = _active_run_id
    _active_run_id = run_id
    try:
        yield run_id
    finally:
        _active_run_id = prior


def backtest_run_dir(run_id: Optional[str] = None) -> Path:
    """Return the per-run output dir, creating it if needed.
    Layout:
      logs/backtest_runs/{run_id}/
        agent_calls.jsonl        (instead of logs/agent_calls.jsonl)
        decisions.jsonl          (instead of logs/decision_outcomes.jsonl)
        playbook_snapshots/{date}/{symbol}.json
        fills.jsonl              (simulated fills from the runner)
    """
    rid = run_id or _active_run_id
    if not rid:
        raise RuntimeError("backtest_run_dir() called outside backtest context")
    base = Path(__file__).resolve().parent.parent / "logs" / "backtest_runs" / rid
    base.mkdir(parents=True, exist_ok=True)
    return base
