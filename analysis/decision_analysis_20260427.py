#!/usr/bin/env python3
"""
Decision Analysis — descriptive stats on paper trading performance.
Analysis only, no production code changes.

Run: python analysis/decision_analysis_20260427.py
"""

import sys
from pathlib import Path
from collections import Counter
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.queries import load_all_decisions


def main():
    decisions = load_all_decisions()
    scored = [d for d in decisions if d.get("return_1d_pct") is not None]
    directional = [d for d in scored if d["decision"] in ("LONG", "SHORT")]
    holds = [d for d in scored if d["decision"] == "HOLD"]

    print("=" * 70)
    print("  DECISION ANALYSIS — Paper Trading Performance")
    print("=" * 70)

    # ── Q1: Sample Composition ───────────────────────────────────────────
    print("\n1. SAMPLE COMPOSITION")
    print("─" * 50)
    print(f"  Total decisions:  {len(decisions)}")
    print(f"  Scored:           {len(scored)}")
    print(f"  Unscored:         {len(decisions) - len(scored)}")

    dates = [d.get("timestamp", "")[:10] for d in decisions if d.get("timestamp")]
    if dates:
        print(f"  Date range:       {min(dates)} to {max(dates)}")

    print(f"\n  By symbol:")
    sym_counts = Counter(d["symbol"] for d in decisions)
    for sym, count in sorted(sym_counts.items()):
        scored_n = sum(1 for d in scored if d["symbol"] == sym)
        print(f"    {sym:5} {count:3} total, {scored_n:3} scored")

    print(f"\n  By direction:")
    dir_counts = Counter(d["decision"] for d in decisions)
    for direction in ("LONG", "SHORT", "HOLD"):
        count = dir_counts.get(direction, 0)
        scored_n = sum(1 for d in scored if d["decision"] == direction)
        print(f"    {direction:5} {count:3} total, {scored_n:3} scored")

    # Why are unscored ones unscored?
    unscored = [d for d in decisions if d.get("return_1d_pct") is None]
    if unscored:
        print(f"\n  Unscored breakdown:")
        for d in unscored:
            date = d.get("timestamp", "?")[:10]
            print(f"    {date} {d['symbol']:5} {d['decision']:5} — likely too recent or bar fetch failed")

    # ── Q2: Hit Rate Analysis ────────────────────────────────────────────
    print(f"\n\n2. HIT RATE ANALYSIS (scored directional only, n={len(directional)})")
    print("─" * 50)

    if not directional:
        print("  No scored directional decisions.")
    else:
        has_stop_data = [d for d in directional if d.get("hit_stop") is not None]
        if has_stop_data:
            targets_hit = sum(1 for d in has_stop_data if d.get("hit_target"))
            stops_hit = sum(1 for d in has_stop_data if d.get("hit_stop"))
            neither = sum(1 for d in has_stop_data
                          if not d.get("hit_target") and not d.get("hit_stop"))
            n = len(has_stop_data)

            print(f"  Overall (n={n}):")
            print(f"    Target hit:  {targets_hit:3} ({targets_hit/n*100:.0f}%)")
            print(f"    Stop hit:    {stops_hit:3} ({stops_hit/n*100:.0f}%)")
            print(f"    Neither:     {neither:3} ({neither/n*100:.0f}%)")

            print(f"\n  By symbol:")
            for sym in sorted(set(d["symbol"] for d in has_stop_data)):
                sym_d = [d for d in has_stop_data if d["symbol"] == sym]
                t = sum(1 for d in sym_d if d.get("hit_target"))
                s = sum(1 for d in sym_d if d.get("hit_stop"))
                print(f"    {sym:5} n={len(sym_d):2}  target={t} ({t/len(sym_d)*100:.0f}%)  "
                      f"stop={s} ({s/len(sym_d)*100:.0f}%)")

            print(f"\n  By direction:")
            for direction in ("LONG", "SHORT"):
                dir_d = [d for d in has_stop_data if d["decision"] == direction]
                if dir_d:
                    t = sum(1 for d in dir_d if d.get("hit_target"))
                    s = sum(1 for d in dir_d if d.get("hit_stop"))
                    print(f"    {direction:5} n={len(dir_d):2}  target={t} ({t/len(dir_d)*100:.0f}%)  "
                          f"stop={s} ({s/len(dir_d)*100:.0f}%)")
        else:
            print("  No hit_stop/hit_target data available.")

    # ── Q3: Return Analysis ──────────────────────────────────────────────
    print(f"\n\n3. RETURN ANALYSIS (scored directional only)")
    print("─" * 50)

    for horizon in ("1d", "5d", "30d"):
        key = f"return_{horizon}_pct"
        returns = [d[key] for d in directional if d.get(key) is not None]
        if not returns:
            print(f"\n  {horizon}: no data")
            continue

        winners = [r for r in returns if r > 0]
        losers = [r for r in returns if r <= 0]

        print(f"\n  {horizon} returns (n={len(returns)}):")
        print(f"    Mean:     {mean(returns):+.2f}%")
        print(f"    Median:   {median(returns):+.2f}%")
        print(f"    Winners:  {len(winners):3} ({len(winners)/len(returns)*100:.0f}%)")
        print(f"    Losers:   {len(losers):3} ({len(losers)/len(returns)*100:.0f}%)")
        print(f"    Best:     {max(returns):+.2f}%")
        print(f"    Worst:    {min(returns):+.2f}%")

        # Per symbol
        print(f"    By symbol:")
        for sym in sorted(set(d["symbol"] for d in directional)):
            sym_ret = [d[key] for d in directional
                       if d["symbol"] == sym and d.get(key) is not None]
            if sym_ret:
                print(f"      {sym:5} n={len(sym_ret):2}  "
                      f"mean={mean(sym_ret):+.2f}%  "
                      f"median={median(sym_ret):+.2f}%  "
                      f"win%={sum(1 for r in sym_ret if r > 0)/len(sym_ret)*100:.0f}%")

    # ── Q4: Confidence Calibration ───────────────────────────────────────
    print(f"\n\n4. CONFIDENCE CALIBRATION")
    print("─" * 50)

    confs = [d.get("confidence") for d in decisions if d.get("confidence") is not None]
    if confs:
        conf_dist = Counter(confs)
        print(f"  Confidence distribution (all {len(confs)} decisions):")
        for c in sorted(conf_dist.keys()):
            bar = "█" * conf_dist[c]
            print(f"    {c:2}: {bar} ({conf_dist[c]})")

    # High vs low confidence on scored directional
    if directional:
        high = [d for d in directional if (d.get("confidence") or 0) >= 8]
        low = [d for d in directional if (d.get("confidence") or 0) < 8]

        print(f"\n  High confidence (≥8) vs Low confidence (<8) on 1d returns:")
        for label, bucket in [("High (≥8)", high), ("Low (<8)", low)]:
            rets = [d["return_1d_pct"] for d in bucket if d.get("return_1d_pct") is not None]
            if rets:
                wins = sum(1 for r in rets if r > 0)
                print(f"    {label:12} n={len(rets):2}  "
                      f"mean={mean(rets):+.2f}%  "
                      f"win%={wins/len(rets)*100:.0f}%")
            else:
                print(f"    {label:12} n= 0  (no scored data)")

        # Hit rates by confidence
        has_hits = [d for d in directional if d.get("hit_stop") is not None]
        if has_hits:
            high_h = [d for d in has_hits if (d.get("confidence") or 0) >= 8]
            low_h = [d for d in has_hits if (d.get("confidence") or 0) < 8]
            print(f"\n  Hit rates by confidence:")
            for label, bucket in [("High (≥8)", high_h), ("Low (<8)", low_h)]:
                if bucket:
                    t = sum(1 for d in bucket if d.get("hit_target"))
                    s = sum(1 for d in bucket if d.get("hit_stop"))
                    print(f"    {label:12} n={len(bucket):2}  "
                          f"target={t/len(bucket)*100:.0f}%  "
                          f"stop={s/len(bucket)*100:.0f}%")

    # ── Q5: Time Patterns ────────────────────────────────────────────────
    print(f"\n\n5. TIME PATTERNS")
    print("─" * 50)

    from datetime import datetime
    day_rets = {}
    for d in directional:
        ts = d.get("timestamp", "")
        ret = d.get("return_1d_pct")
        if not ts or ret is None:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            dow = dt.strftime("%A")
            day_rets.setdefault(dow, []).append(ret)
        except Exception:
            pass

    if day_rets:
        print(f"  Returns by day of week:")
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            rets = day_rets.get(day, [])
            if rets:
                print(f"    {day:10} n={len(rets):2}  mean={mean(rets):+.2f}%  "
                      f"win%={sum(1 for r in rets if r > 0)/len(rets)*100:.0f}%")
            else:
                print(f"    {day:10} n= 0")
    else:
        print("  Insufficient data for day-of-week analysis.")

    # ── Q6: HOLD Opportunity Cost ────────────────────────────────────────
    print(f"\n\n6. HOLD ANALYSIS (n={len(holds)})")
    print("─" * 50)
    opp_costs = [d.get("opportunity_cost_pct") for d in holds
                 if d.get("opportunity_cost_pct") is not None]
    if opp_costs:
        missed = sum(1 for o in opp_costs if o > 0)
        avoided = sum(1 for o in opp_costs if o <= 0)
        print(f"  Opportunity cost (what lit_winner direction would have yielded):")
        print(f"    Mean:              {mean(opp_costs):+.2f}%")
        print(f"    Missed profits:    {missed} ({missed/len(opp_costs)*100:.0f}%)")
        print(f"    Avoided losses:    {avoided} ({avoided/len(opp_costs)*100:.0f}%)")
    else:
        print("  No opportunity cost data for HOLD decisions.")

    # ── Limitations ──────────────────────────────────────────────────────
    print(f"\n\n7. HONEST LIMITATIONS")
    print("─" * 50)
    print(f"""
  - Sample size: {len(decisions)} total decisions, {len(scored)} scored,
    {len(directional)} directional-scored. Almost nothing is statistically
    significant at this sample size.
  - Period: {min(dates) if dates else '?'} to {max(dates) if dates else '?'} — a single
    market regime. Generalization is unsafe.
  - The bot's strategy changed materially during this period (sentiment
    fix Apr 17, position sizing Apr 24, risk gatekeeper refactor Apr 24).
    Earlier decisions were made under different code than later ones.
  - HOLD decisions count as 0% return by design. They're not losses, but
    they dilute the portfolio's actual compound return vs. the reported
    per-decision average.
  - Bar fetch failures (Alpaca SIP subscription) mean some recent decisions
    are unscored. This biases the scored sample toward older decisions.
  - No transaction costs are included in return calculations.
  """)

    print("=" * 70)


if __name__ == "__main__":
    main()
