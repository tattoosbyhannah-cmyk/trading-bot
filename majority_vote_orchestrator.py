"""
Majority-of-3 Voting Wrapper for Master Trading Orchestrator.

Runs the full pipeline 3 times (default) with varied parameters per run:
  Run 1: temp=0.6, 5 RAG chunks, bull-first debate
  Run 2: temp=0.8, 8 RAG chunks, bull-first debate
  Run 3: temp=0.7, 6 RAG chunks, bear-first debate

Extracts the directional decision (LONG/SHORT/HOLD) from each run,
takes the majority vote, and returns the winning decision.

Usage:
    python3 majority_vote_orchestrator.py USO
    python3 majority_vote_orchestrator.py USO --execute
    python3 majority_vote_orchestrator.py USO --runs 5
"""

import logging
import sys
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from master_orchestrator import run_complete_trading_analysis
from fundamentals_analyst import asset_class

KILL_SWITCH_FILE = Path(__file__).parent / "KILL_SWITCH"
OUTCOMES_LOG = Path(__file__).parent / "logs" / "decision_outcomes.jsonl"
HOLD_DECISIONS_LOG = Path(__file__).parent / "logs" / "hold_decisions.jsonl"
PLAYBOOK_DIR = Path(__file__).parent / "playbook"
PLAYBOOK_FILE = PLAYBOOK_DIR / "daily_playbook.json"

# Intraday position sizing — smaller than daily because intraday churns more.
# Adjust after 30+ scored intraday outcomes.
INTRADAY_SIZE_BY_SYMBOL = {
    "USO": 2.0,   # best intraday performer (48% backtest win rate)
    "UNG": 1.5,
    "GLD": 1.5,
}


def check_kill_switch():
    """Raise if kill switch is engaged. Fail fast before spending inference time."""
    if KILL_SWITCH_FILE.exists():
        reason = KILL_SWITCH_FILE.read_text().strip() or "No reason given"
        raise RuntimeError(
            f"KILL SWITCH ENGAGED: {reason}. "
            f"Remove {KILL_SWITCH_FILE} to re-enable trading."
        )


def _write_playbook_entry(symbol: str, majority_direction: str, best_run: dict):
    """Write/update daily_playbook.json with this symbol's intraday parameters."""
    try:
        PLAYBOOK_DIR.mkdir(exist_ok=True)

        # Load existing playbook or start fresh
        playbook = {"generated_at": None, "valid_until": None, "symbols": {}}
        if PLAYBOOK_FILE.exists():
            try:
                playbook = json.loads(PLAYBOOK_FILE.read_text())
            except (json.JSONDecodeError, KeyError):
                pass

        now = datetime.now()
        # valid_until = 4:00 PM ET today. ET = UTC-4 (EDT) or UTC-5 (EST).
        # Use 20:00 UTC as a safe approximation for 4 PM ET during EDT.
        valid_until = now.replace(hour=16, minute=0, second=0, microsecond=0)
        if now.hour >= 16:
            valid_until += timedelta(days=1)

        playbook["generated_at"] = now.isoformat()
        playbook["valid_until"] = valid_until.strftime("%Y-%m-%dT16:00:00-04:00")

        consensus = best_run.get("agent_consensus") or {}
        confidence = best_run.get("confidence", 0) or 0

        # Parse sub-signals from consensus strings like "bearish/strong"
        def _parse_bias(val):
            if not val:
                return "neutral"
            return str(val).split("/")[0].lower()

        def _parse_sentiment_conf(val):
            if not val:
                return 3
            parts = str(val).split("/")
            try:
                return int(parts[1]) if len(parts) > 1 else 3
            except (ValueError, IndexError):
                return 3

        # Intraday trade limits based on conviction
        if confidence >= 8:
            max_trades = 3
        elif confidence >= 6:
            max_trades = 2
        else:
            max_trades = 0

        is_hold = majority_direction == "HOLD"
        ac = asset_class(symbol)
        daily_atr_pct = consensus.get("atr_pct") or 0

        # Run intraday profiler (RAG-informed, replaces all hardcoded gates)
        try:
            from intraday.asset_profiler import profile_asset
            profile = profile_asset(
                symbol=symbol,
                asset_class=ac,
                technical_data={
                    "atr_pct": daily_atr_pct,
                    "rsi_14": consensus.get("rsi_14"),
                    "volume_trend": consensus.get("volume_trend"),
                    "latest_close": consensus.get("current_market_price"),
                },
                fundamentals_bias=_parse_bias(consensus.get("fundamentals")),
                sentiment_data={
                    "sentiment": _parse_bias(consensus.get("sentiment")),
                    "confidence": _parse_sentiment_conf(consensus.get("sentiment")),
                    "news_volume": consensus.get("news_volume", 0),
                },
                daily_direction=majority_direction,
            )
            # Force allow_intraday=False if daily says HOLD
            if is_hold:
                profile.allow_intraday = False
                profile.max_trades = 0

            intraday_profile = profile.model_dump()
            print(f"📋 Intraday profile: allow={profile.allow_intraday} "
                  f"max_trades={profile.max_trades} "
                  f"signal>={profile.min_signal_strength} "
                  f"vol_spike>={profile.volume_spike_threshold}x "
                  f"| {profile.reason[:60]}")
        except Exception as e:
            logging.warning(f"Profiler failed for {symbol}: {e}")
            intraday_profile = {
                "allow_intraday": False,
                "reason": f"profiler error: {e}",
                "min_signal_strength": 0.6,
                "volume_spike_threshold": 3.0,
                "max_trades": 0,
                "stop_atr_multiple": 2.0,
                "target_atr_multiple": 3.0,
                "preferred_entry_window": "10:00-15:00",
                "catalyst_times": [],
            }

        entry = {
            "calculation_run_id": consensus.get("calculation_run_id", ""),
            "direction": majority_direction,
            "conviction": confidence,
            "entry_price": best_run.get("entry_price") or consensus.get("current_market_price"),
            "stop_loss": best_run.get("stop_loss"),
            "price_target": best_run.get("price_target"),
            "position_size_pct": 5.0,  # Base size — risk gatekeeper Python scaling adjusts this
            "intraday_position_size_pct": INTRADAY_SIZE_BY_SYMBOL.get(symbol, 1.5),
            "risk_status": consensus.get("risk_status", "UNKNOWN"),
            "sentiment_bias": _parse_bias(consensus.get("sentiment")),
            "sentiment_confidence": _parse_sentiment_conf(consensus.get("sentiment")),
            "fundamentals_bias": _parse_bias(consensus.get("fundamentals")),
            "technicals_bias": _parse_bias(consensus.get("technical")),
            "literature_winner": consensus.get("literature_winner"),
            "asset_class": ac,
            "daily_atr_pct": daily_atr_pct,
            # Intraday params from profiler (replaces all hardcoded gates)
            "allow_scalping": intraday_profile["allow_intraday"],
            "max_intraday_trades": intraday_profile["max_trades"],
            "max_intraday_loss_pct": 0.5,
            "intraday_profile": intraday_profile,
            "agent_consensus": consensus,
        }

        playbook["symbols"][symbol] = entry

        PLAYBOOK_FILE.write_text(json.dumps(playbook, indent=2, default=str))
        print(f"📋 Playbook updated: {symbol} → {majority_direction} "
              f"(max_trades={entry['max_intraday_trades']})")
    except Exception as e:
        logging.warning(f"Playbook write failed for {symbol}: {e}")
        print(f"⚠️  Playbook write failed: {e}")


def _build_hold_audit(symbol: str, results: list, vote_counts: dict,
                       directions: list, as_of_date: str = None) -> dict:
    """Diagnose WHICH gate caused this HOLD. Priority order:
      1. no_valid_runs   — every run errored
      2. whipsaw         — _detect_whipsaw flagged direction instability
      3. risk_gatekeeper — majority of completed runs had risk_status REJECTED
      4. agent_disagreement — runs split across 2+ directions, no clear majority
      5. majority_vote   — HOLD won the plurality outright
    """
    valid_runs = [r for r in results if r.get("status") == "ok"]
    n_valid = len(valid_runs)
    risk_statuses = []
    for r in valid_runs:
        consensus = r.get("agent_consensus") or {}
        rs = consensus.get("risk_status", "UNKNOWN")
        risk_statuses.append(rs)

    # Whipsaw — same detector master_orchestrator uses for the LLM warning
    whipsaw_active = False
    try:
        from master_orchestrator import _detect_whipsaw
        whipsaw_active = bool(_detect_whipsaw(symbol, as_of_date=as_of_date))
    except Exception:
        pass

    # Determine blocking_gate
    if n_valid == 0:
        blocking_gate = "no_valid_runs"
    elif whipsaw_active:
        blocking_gate = "whipsaw"
    elif risk_statuses.count("REJECTED") > n_valid / 2:
        blocking_gate = "risk_gatekeeper"
    elif len(set(d for d in directions if d != "UNKNOWN")) > 1:
        # Multiple distinct directions across runs and HOLD won → genuine disagreement
        blocking_gate = "agent_disagreement"
    else:
        blocking_gate = "majority_vote"

    # Sample thesis from the first valid HOLD run for context
    hold_runs = [r for r in valid_runs if r.get("direction") == "HOLD"]
    sample_thesis = None
    if hold_runs:
        sample_thesis = (hold_runs[0].get("key_thesis") or "")[:300]

    return {
        "vote_tally": dict(vote_counts),
        "blocking_gate": blocking_gate,
        "risk_statuses": risk_statuses,
        "whipsaw_active": whipsaw_active,
        "agent_disagreement": len(set(d for d in directions if d != "UNKNOWN")) > 1,
        "n_valid_runs": n_valid,
        "n_errored_runs": len(results) - n_valid,
        "sample_thesis": sample_thesis,
    }


def _persist_hold_audit(symbol: str, decision_id: str, calc_run_id: str,
                         hold_audit: dict, timestamp: str):
    """Write hold_audit to dedicated logs/hold_decisions.jsonl. Postgres
    persistence happens via log_decision (which now includes the hold_audit
    column on the decisions row)."""
    record = {
        "timestamp": timestamp,
        "decision_id": decision_id,
        "calculation_run_id": calc_run_id,
        "symbol": symbol,
        "hold_audit": hold_audit,
    }
    try:
        HOLD_DECISIONS_LOG.parent.mkdir(exist_ok=True)
        with open(HOLD_DECISIONS_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logging.warning(f"Failed to write hold_decisions.jsonl: {e}")


def extract_direction(final_decision: str) -> str:
    """Extract the directional action (LONG/SHORT/HOLD) from the decision string."""
    first_word = final_decision.strip().split()[0].upper()
    if first_word in ("LONG", "SHORT", "HOLD"):
        return first_word
    # Fallback: scan the full string for keywords
    upper = final_decision.upper()
    for direction in ("LONG", "SHORT", "HOLD"):
        if direction in upper:
            return direction
    return "UNKNOWN"


def run_majority_vote(symbol: str, num_runs: int = 3, execute: bool = False,
                       as_of_date: str = None, portfolio_context: dict = None):
    """Run the trading pipeline multiple times and take majority vote.

    If execute=True, LONG/SHORT decisions are sent to the paper trading
    executor after the vote resolves. HOLD and ERROR results are skipped.

    Backtest args:
        as_of_date: ISO 'YYYY-MM-DD' cutoff for all data fetches. When set,
                    pipeline runs in backtest mode and execute=True is rejected
                    (backtest fills are produced by the backtest runner).
        portfolio_context: pre-built risk-context dict from SimPortfolio.
    """
    if as_of_date and execute:
        raise ValueError("execute=True is not allowed with as_of_date — "
                         "backtest runs must not place live trades.")
    print(f"\n{'='*70}")
    print(f"MAJORITY VOTE ORCHESTRATOR — {symbol}")
    print(f"Runs: {num_runs} | Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if execute:
        print(f"Mode: EXECUTE (paper trades will be placed for LONG/SHORT)")
    print(f"{'='*70}")

    # Variation profiles — each run explores different evidence/reasoning
    VOTE_VARIATIONS = [
        {"rag_chunks_limit": 5, "rag_query_n": 2, "debate_order": "bull_first",
         "temperature_override": 0.6},
        {"rag_chunks_limit": 8, "rag_query_n": 3, "debate_order": "bull_first",
         "temperature_override": 0.8},
        {"rag_chunks_limit": 6, "rag_query_n": 2, "debate_order": "bear_first",
         "temperature_override": 0.7},
    ]

    results = []
    directions = []
    decisions = {}  # run number -> MasterTradingDecision object

    for i in range(1, num_runs + 1):
        variation = VOTE_VARIATIONS[(i - 1) % len(VOTE_VARIATIONS)]
        print(f"\n{'─'*70}")
        print(f"▶ VOTE RUN {i}/{num_runs} "
              f"(chunks={variation['rag_chunks_limit']}, "
              f"n={variation['rag_query_n']}, "
              f"order={variation['debate_order']}, "
              f"temp={variation['temperature_override']})")
        print(f"{'─'*70}")

        start = time.time()
        try:
            check_kill_switch()
            decision = run_complete_trading_analysis(
                symbol, variation=variation,
                as_of_date=as_of_date,
                portfolio_context=portfolio_context,
            )
            elapsed = time.time() - start
            direction = extract_direction(decision.final_decision)
            decisions[i] = decision

            run_record = {
                "run": i,
                "direction": direction,
                "confidence": decision.confidence,
                "position_size": decision.position_size,
                "entry_price": decision.entry_price,
                "stop_loss": decision.stop_loss,
                "price_target": decision.price_target,
                "stop_loss_pct": getattr(decision, "stop_loss_pct", None),
                "price_target_pct": getattr(decision, "price_target_pct", None),
                "key_thesis": decision.key_thesis,
                "agent_consensus": decision.agent_consensus,
                "elapsed_sec": round(elapsed, 1),
                "status": "ok",
            }
            directions.append(direction)

        except Exception as e:
            elapsed = time.time() - start
            print(f"\n❌ Run {i} failed after {elapsed:.1f}s: {e}")
            run_record = {
                "run": i,
                "direction": "ERROR",
                "error": str(e),
                "elapsed_sec": round(elapsed, 1),
                "status": "error",
            }

        results.append(run_record)

    # ── Tally votes ──────────────────────────────────────────────────────
    valid_directions = [d for d in directions if d != "UNKNOWN"]
    vote_counts = Counter(valid_directions)

    if not vote_counts:
        majority_direction = "HOLD"
        majority_count = 0
        print("\n⚠️  No valid runs — defaulting to HOLD")
    else:
        majority_direction, majority_count = vote_counts.most_common(1)[0]

    # Pick the representative decision: highest-confidence run that matches majority
    majority_runs = [r for r in results if r.get("direction") == majority_direction]
    best_run = max(majority_runs, key=lambda r: r.get("confidence", 0)) if majority_runs else None

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"📊 MAJORITY VOTE RESULT — {symbol}")
    print(f"{'='*70}")

    for r in results:
        status = "✅" if r.get("status") == "ok" else "❌"
        conf = r.get("confidence", "—")
        print(f"  {status} Run {r['run']}: {r['direction']}  confidence={conf}  ({r['elapsed_sec']}s)")

    print(f"\n🗳️  VOTE TALLY: {dict(vote_counts)}")
    print(f"🎯 MAJORITY DECISION: {majority_direction}  ({majority_count}/{num_runs} runs)")

    if best_run:
        print(f"📋 Representative thesis (Run {best_run['run']}, confidence {best_run['confidence']}):")
        print(f"   {best_run.get('key_thesis', 'N/A')}")
        print(f"\n🤖 Agent consensus from representative run:")
        for agent, conclusion in best_run.get("agent_consensus", {}).items():
            print(f"   {agent}: {conclusion}")

    # ── Save log ─────────────────────────────────────────────────────────
    log_entry = {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "num_runs": num_runs,
        "majority_direction": majority_direction,
        "majority_count": majority_count,
        "vote_counts": dict(vote_counts),
        "runs": results,
    }

    log_path = f"/tmp/{symbol}_majority_vote_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(log_path, "w") as f:
        json.dump(log_entry, f, indent=2, default=str)
    print(f"\n💾 Full log saved to: {log_path}")

    # ── HOLD audit (Priority 6) ──────────────────────────────────────────
    # When the vote resolves to HOLD, capture which gate caused it. This data
    # is essential for understanding the system's filtering behavior over time
    # ("why did 90% of trades die at gate X?"). Empty dict for non-HOLD runs.
    hold_audit_payload = None
    if majority_direction == "HOLD":
        hold_audit_payload = _build_hold_audit(
            symbol, results, vote_counts, directions, as_of_date=as_of_date
        )
        print(f"\n🛑 HOLD AUDIT — blocking_gate: {hold_audit_payload['blocking_gate']}  "
              f"vote_tally: {hold_audit_payload['vote_tally']}")

    # ── Outcome tracking record ──────────────────────────────────────────
    if best_run:
        consensus = best_run.get("agent_consensus") or {}
        entry_price = (
            best_run.get("entry_price")
            or consensus.get("current_market_price")
        )
        calc_id = consensus.get("calculation_run_id", "")
        outcome_record = {
            "decision_id": str(uuid.uuid4()),
            "calculation_run_id": calc_id,
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "asset_class": asset_class(symbol),
            "decision": majority_direction,
            "confidence": best_run.get("confidence"),
            "entry_price": entry_price,
            "stop_loss": best_run.get("stop_loss"),
            "stop_loss_pct": best_run.get("stop_loss_pct"),
            "price_target": best_run.get("price_target"),
            "price_target_pct": best_run.get("price_target_pct"),
            "position_size_pct": 5.0,  # Base size — deterministic, not from LLM
            "literature_winner": consensus.get("literature_winner"),
            "agent_consensus": consensus,
            "hold_audit": hold_audit_payload,  # populated only when direction == HOLD
            # Outcome fields — populated later by score_outcomes.py
            "price_1d": None,
            "price_5d": None,
            "price_30d": None,
            "return_1d_pct": None,
            "return_5d_pct": None,
            "return_30d_pct": None,
            "hit_stop": None,
            "hit_target": None,
            "stop_hit_day": None,
            "target_hit_day": None,
            "opportunity_cost_pct": None,
            "scored_at": None,
        }
        try:
            # Dual write: JSONL + Postgres
            from db.log_writer import log_decision
            log_decision(outcome_record)
            print(f"📊 Outcome record logged: {outcome_record['decision_id']}")
        except Exception as e:
            # Fallback: JSONL only
            try:
                OUTCOMES_LOG.parent.mkdir(exist_ok=True)
                with open(OUTCOMES_LOG, "a") as f:
                    f.write(json.dumps(outcome_record, default=str) + "\n")
                print(f"📊 Outcome record logged (JSONL only): {outcome_record['decision_id']}")
            except Exception as e2:
                print(f"⚠️  Failed to write outcome record: {e2}")

        # Dedicated HOLD-audit JSONL (Priority 6) — written for analysis tools
        # that just want the "why no trade" stream without the full decision body.
        if hold_audit_payload is not None:
            _persist_hold_audit(
                symbol=symbol,
                decision_id=outcome_record["decision_id"],
                calc_run_id=outcome_record["calculation_run_id"],
                hold_audit=hold_audit_payload,
                timestamp=outcome_record["timestamp"],
            )

    # ── Playbook for intraday system ────────────────────────────────────
    # Backtest isolation: skip live playbook regen. The live playbook drives
    # the swing executor's intraday symbol-selection — overwriting it from a
    # backtest run would corrupt tomorrow's actual trading. A future runner
    # may want to snapshot the would-be entry to logs/backtest_runs/{run_id}/
    # playbook_snapshots/{date}/{symbol}.json; for now we just skip.
    if best_run:
        try:
            from backtest.run_context import is_backtest_mode
            _bt = is_backtest_mode()
        except Exception:
            _bt = False
        if _bt:
            print(f"📋 Playbook write skipped (backtest mode)")
        else:
            _write_playbook_entry(symbol, majority_direction, best_run)

    # ── Paper trade execution ────────────────────────────────────────────
    if execute and best_run and majority_direction in ("LONG", "SHORT"):
        best_decision = decisions.get(best_run["run"])
        if best_decision:
            print(f"\n{'─'*70}")
            print(f"📈 EXECUTING PAPER TRADE: {majority_direction} {symbol}")
            print(f"{'─'*70}")
            try:
                check_kill_switch()
                from paper_trading_executor import execute_master_decision
                exec_result = execute_master_decision(best_decision)
                log_entry["execution"] = {
                    "status": "ok" if exec_result.get("success") or
                              (exec_result.get("trade") and exec_result["trade"].success)
                              else "failed",
                    "current_price": exec_result.get("current_price"),
                    "spread_bps": exec_result.get("spread_bps"),
                    "timestamp": exec_result.get("timestamp"),
                }
                print(f"✅ Paper trade execution complete")
            except RuntimeError as e:
                print(f"🛑 {e}")
                log_entry["execution"] = {"status": "killed", "reason": str(e)}
            except Exception as e:
                logging.warning(f"Paper trade execution failed for {symbol}: {e}")
                print(f"❌ Execution failed: {e}")
                log_entry["execution"] = {"status": "error", "error": str(e)}
        else:
            print(f"\n⚠️  No decision object for representative run — skipping execution")
    elif execute and majority_direction == "HOLD":
        print(f"\n📊 Decision is HOLD — no paper trade to execute")
    elif execute and majority_direction in ("ERROR", "UNKNOWN"):
        print(f"\n⚠️  Decision is {majority_direction} — skipping execution")

    return log_entry


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"
    num_runs = 3
    execute = "--execute" in sys.argv

    # Optional: --runs N
    if "--runs" in sys.argv:
        idx = sys.argv.index("--runs")
        if idx + 1 < len(sys.argv):
            num_runs = int(sys.argv[idx + 1])

    result = run_majority_vote(symbol, num_runs, execute=execute)
