#!/usr/bin/env python3
"""
Outcome Scorer — reads logs/decision_outcomes.jsonl, fetches actual daily bars
from Alpaca for each decision, computes directional returns and stop/target hits,
updates records in place, and prints a summary report.

Directional returns:
  LONG:  positive = correct (price went up)
  SHORT: positive = correct (price went down; computed as -raw_return)
  HOLD:  return is 0; we also compute "opportunity_cost_pct" — what the return
         would have been if the system had followed the literature_winner.

Usage:
    python score_outcomes.py           # score eligible records + print report
    python score_outcomes.py report    # print report only, skip scoring
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)

OUTCOMES_LOG = Path(__file__).parent / "logs" / "decision_outcomes.jsonl"
HORIZONS = [1, 5, 30]  # Calendar-day horizons to score


def _read_records() -> list:
    """Read all decisions — SQL first, JSONL fallback."""
    from db.queries import load_all_decisions
    return load_all_decisions()


def _write_records(records: list):
    """Write updated records back to both JSONL and Postgres.

    Full JSONL rewrite keeps it in sync with Postgres (source of truth).
    This is intentional — JSONL is the fallback mirror, not the primary store.
    """
    # JSONL full rewrite
    OUTCOMES_LOG.parent.mkdir(exist_ok=True)
    with open(OUTCOMES_LOG, "w") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")
    # Postgres per-record update
    try:
        from db.queries import update_decision
        # W4: ensure proxy_outcomes JSONB column exists; idempotent
        try:
            from db.connection import db_cursor
            with db_cursor() as cur:
                cur.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS proxy_outcomes JSONB")
        except Exception as e:
            print(f"  [WARN] ALTER decisions ADD proxy_outcomes failed: {e}")

        for r in records:
            did = r.get("decision_id")
            if not did:
                continue
            updates = {}
            for field in ("price_1d", "price_5d", "price_30d",
                          "return_1d_pct", "return_5d_pct", "return_30d_pct",
                          "hit_stop", "hit_target", "stop_hit_day", "target_hit_day",
                          "opportunity_cost_pct", "scored_at", "stop_loss",
                          "proxy_outcomes"):
                if field in r and r[field] is not None:
                    updates[field] = r[field]
            if updates:
                update_decision(did, updates)
    except Exception:
        pass


def _fetch_bars(symbol: str, start: datetime, end: datetime) -> list:
    """Fetch daily bars from Alpaca. Returns list of {date, open, high, low, close}."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )
    req = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    resp = client.get_stock_bars(req)
    bars = []
    symbol_bars = resp.data.get(symbol, [])
    for b in symbol_bars:
        bars.append({
            "date": b.timestamp.date(),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
        })
    return bars


def _directional_return(direction: str, entry: float, exit_: float) -> float:
    """Return signed pct where positive = directionally correct."""
    raw = (exit_ - entry) / entry * 100
    if direction == "LONG":
        return raw
    if direction == "SHORT":
        return -raw
    return 0.0


def _check_stop_target(direction: str, bars: list, entry: float,
                       stop: Optional[float], target: Optional[float]) -> dict:
    """Walk through bars day-by-day and find first stop/target hit."""
    stop_hit_day = None
    target_hit_day = None

    for i, bar in enumerate(bars, start=1):
        if direction == "LONG":
            if stop is not None and stop_hit_day is None and bar["low"] <= stop:
                stop_hit_day = i
            if target is not None and target_hit_day is None and bar["high"] >= target:
                target_hit_day = i
        elif direction == "SHORT":
            if stop is not None and stop_hit_day is None and bar["high"] >= stop:
                stop_hit_day = i
            if target is not None and target_hit_day is None and bar["low"] <= target:
                target_hit_day = i
        if stop_hit_day and target_hit_day:
            break

    return {
        "hit_stop": stop_hit_day is not None,
        "hit_target": target_hit_day is not None,
        "stop_hit_day": stop_hit_day,
        "target_hit_day": target_hit_day,
    }


def _score_record(record: dict) -> bool:
    """Fetch bars, fill in outcomes. Returns True if record was updated."""
    ts = datetime.fromisoformat(record["timestamp"])
    symbol = record["symbol"]
    entry = record.get("entry_price")
    direction = record["decision"]

    if entry is None:
        return False

    # Determine which horizons are eligible based on calendar-day age.
    now = datetime.now(timezone.utc)
    age_days = (now - ts).days
    horizons_to_score = [h for h in HORIZONS if age_days >= h]
    if not horizons_to_score:
        return False

    # Fetch enough bars to cover the longest scorable horizon (+buffer for weekends).
    max_horizon = max(horizons_to_score)
    start = ts
    end = ts + timedelta(days=max_horizon + 5)
    if end > now:
        end = now

    try:
        bars = _fetch_bars(symbol, start, end)
    except Exception as e:
        print(f"  [ERR] Could not fetch bars for {symbol}: {e}")
        return False

    if not bars:
        return False

    # Bars are sorted by date; bar index = trading day count from entry.
    updated = False
    for h in horizons_to_score:
        # NOTE: horizons are in TRADING days, not calendar days.
        # 1d = next trading day, 5d ≈ 1 week, 30d ≈ 6 weeks.
        # Weekends/holidays skipped naturally since Alpaca only returns trading days.
        if len(bars) >= h:
            exit_price = bars[h - 1]["close"]
            record[f"price_{h}d"] = round(exit_price, 2)
            if direction == "HOLD":
                # For HOLD, directional return is 0 — but opportunity_cost
                # captures what literature_winner direction would have yielded.
                record[f"return_{h}d_pct"] = 0.0
            else:
                record[f"return_{h}d_pct"] = round(
                    _directional_return(direction, entry, exit_price), 2
                )
            updated = True

    # Compute opportunity cost for HOLD decisions (uses longest available horizon)
    if direction == "HOLD" and bars:
        lit_winner_raw = (record.get("literature_winner") or "").split("/")[0].upper()
        implied_direction = None
        if lit_winner_raw == "BULL":
            implied_direction = "LONG"
        elif lit_winner_raw == "BEAR":
            implied_direction = "SHORT"
        if implied_direction:
            # Use longest scored horizon for opp cost
            h = max(horizons_to_score)
            if len(bars) >= h:
                exit_price = bars[h - 1]["close"]
                record["opportunity_cost_pct"] = round(
                    _directional_return(implied_direction, entry, exit_price), 2
                )
                updated = True

    # Stop/target hit analysis — walk full bar history up to longest horizon
    if direction in ("LONG", "SHORT") and horizons_to_score:
        was_stopped = record.get("hit_stop", False)
        longest = max(horizons_to_score)
        relevant_bars = bars[:longest]
        hits = _check_stop_target(
            direction, relevant_bars, entry,
            record.get("stop_loss"), record.get("price_target")
        )
        record.update(hits)
        updated = True

        # Alert on newly-detected stop hit
        if hits["hit_stop"] and not was_stopped and record.get("stop_loss"):
            try:
                from alert_manager import alert_stop_hit
                loss_pct = _directional_return(direction, entry, record["stop_loss"])
                alert_stop_hit(symbol, direction, entry, record["stop_loss"],
                               loss_pct, record["timestamp"][:10])
            except Exception:
                pass

    # Bearish-proxy routing dual P&L (W4) — for trades whose actual position is in a proxy.
    # The existing return_{h}d_pct above is the INFERRED direct-thesis P&L (what UNG SHORT
    # would have netted if direct). The block below adds ACTUAL proxy P&L (what the LONG KOLD
    # position actually netted) and the divergence between the two.
    route = record.get("route_taken")
    if isinstance(route, dict) and route.get("executed_symbol") and horizons_to_score:
        proxy_symbol = route["executed_symbol"]
        try:
            proxy_bars = _fetch_bars(proxy_symbol, start, end)
        except Exception as e:
            print(f"  [WARN] proxy bars fetch failed for {proxy_symbol}: {e}")
            proxy_bars = []
        if proxy_bars:
            proxy_entry = proxy_bars[0]["close"]
            proxy_outcomes = {"proxy_entry_price": round(proxy_entry, 2)}
            for h in horizons_to_score:
                if len(proxy_bars) >= h:
                    proxy_exit = proxy_bars[h - 1]["close"]
                    # Routed trades are always executed_direction = LONG by config
                    proxy_ret = _directional_return("LONG", proxy_entry, proxy_exit)
                    proxy_outcomes[f"proxy_return_{h}d_pct"] = round(proxy_ret, 2)
                    inferred = record.get(f"return_{h}d_pct")
                    if inferred is not None:
                        proxy_outcomes[f"route_divergence_{h}d_pct"] = round(proxy_ret - inferred, 2)
            record["proxy_outcomes"] = proxy_outcomes
            updated = True

    if updated:
        record["scored_at"] = datetime.now(timezone.utc).isoformat()

    return updated


def _is_fully_scored(record: dict) -> bool:
    """Check if all applicable horizons have been scored."""
    ts = datetime.fromisoformat(record["timestamp"])
    age_days = (datetime.now(timezone.utc) - ts).days
    applicable = [h for h in HORIZONS if age_days >= h]
    if not applicable:
        return True  # Nothing to score yet
    for h in applicable:
        if record.get(f"return_{h}d_pct") is None:
            return False
    return True


def score():
    records = _read_records()
    if not records:
        print("No decision records to score.")
        return 0

    updated_count = 0
    eligible_count = 0

    for record in records:
        if _is_fully_scored(record):
            continue
        eligible_count += 1
        if _score_record(record):
            updated_count += 1
            print(f"  [OK] Scored {record['symbol']} {record['decision']} "
                  f"({record['decision_id'][:8]})")

    if updated_count > 0:
        _write_records(records)
        print(f"\nScored {updated_count}/{eligible_count} eligible records")
    else:
        print(f"No updates. {eligible_count} records eligible but none scoreable.")
    return updated_count


def report():
    records = _read_records()
    if not records:
        print("No decision records yet.")
        return

    print(f"\n{'='*70}")
    print(f"📊 OUTCOME REPORT — {len(records)} total decisions")
    print(f"{'='*70}")

    # Split by direction
    by_direction = {"LONG": [], "SHORT": [], "HOLD": []}
    for r in records:
        d = r.get("decision", "?")
        if d in by_direction:
            by_direction[d].append(r)

    # Scored subset (has at least one populated return)
    def _is_scored(r):
        return any(r.get(f"return_{h}d_pct") is not None for h in HORIZONS)

    scored = [r for r in records if _is_scored(r)]
    unscored = [r for r in records if not _is_scored(r)]

    print(f"\nDecisions by direction:")
    for d in ("LONG", "SHORT", "HOLD"):
        print(f"  {d:5s}: {len(by_direction[d])}")

    print(f"\nScoring status: {len(scored)} scored, {len(unscored)} pending")

    if not scored:
        print("\n(No scored records yet — come back after 1+ calendar day.)")
        return

    # Per-horizon accuracy and returns
    print(f"\n{'─'*70}")
    print(f"DIRECTIONAL PERFORMANCE (trading-day horizons, excludes HOLD)")
    print(f"{'─'*70}")
    directional = [r for r in scored if r["decision"] in ("LONG", "SHORT")]
    for h in HORIZONS:
        returns = [r[f"return_{h}d_pct"] for r in directional
                   if r.get(f"return_{h}d_pct") is not None]
        if not returns:
            continue
        correct = sum(1 for r in returns if r > 0)
        accuracy = correct / len(returns) * 100
        print(f"  {h:2d}d: n={len(returns):3d}  "
              f"accuracy={accuracy:5.1f}%  "
              f"avg={mean(returns):+6.2f}%  "
              f"median={median(returns):+6.2f}%")

    # Per-asset-class breakdown
    print(f"\n{'─'*70}")
    print(f"BY ASSET CLASS (5-day horizon)")
    print(f"{'─'*70}")
    by_ac = {}
    for r in directional:
        ac = r.get("asset_class", "unknown")
        by_ac.setdefault(ac, []).append(r)
    for ac in sorted(by_ac):
        rs = by_ac[ac]
        returns = [r["return_5d_pct"] for r in rs
                   if r.get("return_5d_pct") is not None]
        if not returns:
            continue
        correct = sum(1 for r in returns if r > 0)
        print(f"  {ac:12s}: n={len(returns):3d}  "
              f"accuracy={correct/len(returns)*100:5.1f}%  "
              f"avg={mean(returns):+6.2f}%")

    # Stop/target hit rates
    print(f"\n{'─'*70}")
    print(f"STOP/TARGET HIT RATES (LONG/SHORT only)")
    print(f"{'─'*70}")
    checked = [r for r in directional if r.get("hit_stop") is not None]
    if checked:
        stop_hits = sum(1 for r in checked if r["hit_stop"])
        target_hits = sum(1 for r in checked if r["hit_target"])
        both = sum(1 for r in checked if r["hit_stop"] and r["hit_target"])
        print(f"  Hit stop:   {stop_hits}/{len(checked)} ({stop_hits/len(checked)*100:.1f}%)")
        print(f"  Hit target: {target_hits}/{len(checked)} ({target_hits/len(checked)*100:.1f}%)")
        print(f"  Hit both:   {both}  (earliest wins in practice)")

        # Avg days to hit
        stop_days = [r["stop_hit_day"] for r in checked if r.get("stop_hit_day")]
        target_days = [r["target_hit_day"] for r in checked if r.get("target_hit_day")]
        if stop_days:
            print(f"  Avg days to stop:   {mean(stop_days):.1f}")
        if target_days:
            print(f"  Avg days to target: {mean(target_days):.1f}")

    # HOLD opportunity cost
    holds = [r for r in scored if r["decision"] == "HOLD"
             and r.get("opportunity_cost_pct") is not None]
    if holds:
        print(f"\n{'─'*70}")
        print(f"HOLD OPPORTUNITY COST (what lit_winner direction would have yielded)")
        print(f"{'─'*70}")
        opp = [r["opportunity_cost_pct"] for r in holds]
        missed_gains = sum(1 for v in opp if v > 0)
        print(f"  n={len(holds)}  "
              f"avg missed={mean(opp):+6.2f}%  "
              f"median={median(opp):+6.2f}%  "
              f"profitable skips avoided={sum(1 for v in opp if v < 0)}/{len(opp)}")
        print(f"  Times we should have traded: {missed_gains}/{len(opp)} "
              f"({missed_gains/len(opp)*100:.1f}%)")

    print(f"\n{'='*70}\n")


def model_comparison():
    """Fix 3: Compare deep orchestrator vs fast-lane consensus accuracy."""
    records = _read_records()
    scored = [r for r in records if r.get("return_1d_pct") is not None
              and r.get("decision") in ("LONG", "SHORT")]
    if not scored:
        print("No scored LONG/SHORT records for model comparison.")
        return

    print(f"\n{'='*70}")
    print(f"🔬 MODEL TIER COMPARISON — Deep vs Fast-Lane Consensus")
    print(f"{'='*70}")

    # For each decision, infer fast-lane consensus from agent_consensus
    # Fast lane: technical + fundamentals + sentiment → 3 votes
    agree_correct = 0
    agree_wrong = 0
    disagree_deep_correct = 0
    disagree_deep_wrong = 0
    total = 0

    for r in scored:
        consensus = r.get("agent_consensus") or r.get("consensus") or {}
        if not consensus:
            continue

        # Parse direction from each fast-lane agent
        def _dir_from(val):
            if not val:
                return None
            bias = str(val).split("/")[0].lower()
            if bias in ("bullish", "bull"):
                return "LONG"
            if bias in ("bearish", "bear"):
                return "SHORT"
            return None

        tech_dir = _dir_from(consensus.get("technical"))
        fund_dir = _dir_from(consensus.get("fundamentals"))
        sent_dir = _dir_from(consensus.get("sentiment"))

        fast_votes = [d for d in [tech_dir, fund_dir, sent_dir] if d]
        if len(fast_votes) < 2:
            continue

        # Fast-lane consensus = majority of the 3
        from collections import Counter
        fast_counts = Counter(fast_votes)
        fast_consensus = fast_counts.most_common(1)[0][0]

        deep_dir = r["decision"]
        ret = r["return_1d_pct"]
        deep_correct = ret > 0

        if deep_dir == fast_consensus:
            if deep_correct:
                agree_correct += 1
            else:
                agree_wrong += 1
        else:
            if deep_correct:
                disagree_deep_correct += 1
            else:
                disagree_deep_wrong += 1
        total += 1

    if total == 0:
        print("  Insufficient data for comparison.")
        return

    agree = agree_correct + agree_wrong
    disagree = disagree_deep_correct + disagree_deep_wrong
    agree_rate = agree / total * 100

    print(f"\n  Decisions analyzed: {total}")
    print(f"  Agreement rate: {agree}/{total} ({agree_rate:.1f}%)")

    if agree > 0:
        agree_acc = agree_correct / agree * 100
        print(f"  When AGREEING:  {agree_correct}/{agree} correct ({agree_acc:.1f}%)")
    if disagree > 0:
        disagree_acc = disagree_deep_correct / disagree * 100
        print(f"  When DISAGREEING (deep was right): {disagree_deep_correct}/{disagree} ({disagree_acc:.1f}%)")
        disagree_fast_acc = disagree_deep_wrong / disagree * 100
        print(f"  When DISAGREEING (fast was right): {disagree_deep_wrong}/{disagree} ({disagree_fast_acc:.1f}%)")

    print(f"\n  Interpretation:")
    if disagree > 0 and disagree_deep_correct > disagree_deep_wrong:
        print(f"  → Deep model ADDS VALUE: when it overrides fast consensus, it's right more often")
    elif disagree > 0 and disagree_deep_correct < disagree_deep_wrong:
        print(f"  → Deep model SUBTRACTS VALUE: fast consensus was better when they disagreed")
    else:
        print(f"  → Insufficient disagreements to determine deep model value-add")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "score"

    if mode == "report":
        report()
    elif mode == "model-comparison":
        model_comparison()
    else:
        print("Scoring eligible records...")
        score()
        # Also ratchet stops on open positions
        try:
            from ratchet_stops import ratchet_all
            print("\nChecking stop ratchets...")
            ratchet_all()
        except Exception as e:
            print(f"Ratchet check skipped: {e}")
        report()
