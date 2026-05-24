"""
Structured agent call logging.

Writes one JSON line per agent call to logs/agent_calls.jsonl.
Each entry captures: timestamp, agent name, symbol, model, input/output tokens,
latency, raw output (for decision/conviction extraction), exception (if any).

Usage:
    from agent_logger import log_agent_call

    @log_agent_call(agent_name="bull_researcher")
    def generate_rag_bull_case(state):
        ...
        return {"rag_bull_argument": result}
"""

import json
import threading
import time
import functools
import traceback
from datetime import datetime
from pathlib import Path

# Thread-local storage for the current calculation_run_id.
# Set by the pipeline at the start of each run, read by every agent.
_local = threading.local()


def set_calculation_run_id(calc_id: str):
    """Set the current calculation_run_id for this thread."""
    _local.calculation_run_id = calc_id


def get_calculation_run_id() -> str:
    """Get the current calculation_run_id for this thread."""
    return getattr(_local, "calculation_run_id", "")

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "agent_calls.jsonl"


def _extract_symbol(args, kwargs):
    """Pull symbol out of args/kwargs/state dict — agents pass it various ways."""
    if "symbol" in kwargs:
        return kwargs["symbol"]
    for arg in args:
        if isinstance(arg, str) and len(arg) <= 6 and arg.isupper():
            return arg
        if isinstance(arg, dict) and "symbol" in arg:
            return arg["symbol"]
    return None


def _extract_calc_id(args, kwargs):
    """Pull calculation_run_id from args/kwargs/state dict."""
    if "calculation_run_id" in kwargs:
        return kwargs["calculation_run_id"]
    for arg in args:
        if isinstance(arg, dict) and "calculation_run_id" in arg:
            return arg["calculation_run_id"]
    return None


def _extract_decision_fields(result):
    """Pull decision-relevant fields from the agent's return value."""
    fields = {}
    if not result:
        return fields

    # Pydantic models
    candidate = result
    if isinstance(result, dict) and len(result) == 1:
        candidate = next(iter(result.values()))

    for attr in ("final_decision", "winning_side", "approval_status",
                 "trend", "bias", "sentiment", "strength",
                 "conviction", "confidence", "risk_score",
                 "position_size_pct", "stop_loss_pct"):
        val = None
        if hasattr(candidate, attr):
            val = getattr(candidate, attr)
        elif isinstance(candidate, dict) and attr in candidate:
            val = candidate[attr]
        if val is not None:
            fields[attr] = val if isinstance(val, (int, float, str, bool)) else str(val)

    # Capture structured literature citations (LiteratureCitation models)
    lit_cites = None
    if hasattr(candidate, "literature_citations"):
        lit_cites = getattr(candidate, "literature_citations")
    elif isinstance(candidate, dict) and "literature_citations" in candidate:
        lit_cites = candidate["literature_citations"]
    if lit_cites:
        fields["literature_citations"] = [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in lit_cites
        ]

    # Capture sentiment-fetch diagnostics dict (per-query counts, top_headlines, etc.)
    # without stringifying — preserves structure for post-hoc analysis.
    diag = None
    if hasattr(candidate, "diagnostics"):
        diag = getattr(candidate, "diagnostics")
    elif isinstance(candidate, dict) and "diagnostics" in candidate:
        diag = candidate["diagnostics"]
    if diag:
        fields["diagnostics"] = diag if isinstance(diag, dict) else str(diag)

    return fields


def _approx_tokens(text):
    """Quick token estimate — 4 chars per token. Good enough for monitoring."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return len(text) // 4


def log_agent_call(agent_name, model_lane=None):
    """
    Decorator: wrap an agent function and log its invocation as JSON.

    Args:
        agent_name: short identifier ("bull_researcher", "risk_gatekeeper", etc.)
        model_lane: "deep" (all agents on 30B MoE, port 8081)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            symbol = _extract_symbol(args, kwargs)
            calc_id = _extract_calc_id(args, kwargs) or get_calculation_run_id()
            timestamp = datetime.now().isoformat()
            entry = {
                "timestamp": timestamp,
                "agent": agent_name,
                "model_lane": "deep",
                "model_tier": "30b-moe",
                "symbol": symbol,
                "calculation_run_id": calc_id,
                "status": "ok",
            }

            try:
                result = func(*args, **kwargs)
                entry["latency_sec"] = round(time.time() - start, 2)
                entry["output_tokens_est"] = _approx_tokens(result)
                entry["decision_fields"] = _extract_decision_fields(result)
            except Exception as e:
                entry["status"] = "error"
                entry["latency_sec"] = round(time.time() - start, 2)
                entry["error"] = str(e)
                entry["traceback"] = traceback.format_exc()
                _write(entry)
                raise

            _write(entry)
            return result
        return wrapper
    return decorator


def _write(entry):
    """Write to both JSONL and Postgres. Logging must never break the pipeline.

    Backtest isolation: when a backtest_run_id is active, JSONL routes to
    logs/backtest_runs/{run_id}/agent_calls.jsonl. The Postgres write below
    is handled by db.log_writer (which also checks _is_backtest()) and will
    be a no-op in backtest mode."""
    # JSONL (always; routed to backtest dir when active)
    target = LOG_FILE
    try:
        from backtest.run_context import is_backtest_mode, backtest_run_dir
        if is_backtest_mode():
            target = backtest_run_dir() / "agent_calls.jsonl"
    except Exception:
        pass
    try:
        with open(target, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        print(f"[agent_logger] Failed to write JSONL: {e}")

    # Postgres (best-effort)
    try:
        from db.log_writer import log_reasoning_run
        log_reasoning_run(entry)
    except Exception:
        pass  # Postgres not available or not installed yet


def query_logs(agent=None, symbol=None, since_minutes=None, limit=100):
    """Quick log query helper. Use from python REPL or scripts."""
    from datetime import timedelta
    cutoff = None
    if since_minutes:
        cutoff = datetime.now() - timedelta(minutes=since_minutes)

    entries = []
    if not LOG_FILE.exists():
        return entries

    with open(LOG_FILE) as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if agent and e.get("agent") != agent:
                continue
            if symbol and e.get("symbol") != symbol:
                continue
            if cutoff:
                ts = datetime.fromisoformat(e.get("timestamp", ""))
                if ts < cutoff:
                    continue
            entries.append(e)

    return entries[-limit:]
