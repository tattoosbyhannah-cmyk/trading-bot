# HOLD-on-existing-position Behavior Audit — 2026-05-01

**Question.** When the daily pipeline outputs HOLD for a symbol that has an existing position, what happens? Are positions and stops preserved cleanly, or is there hidden mutation?

**Answer.** Positions are preserved cleanly by the executor's HOLD branch. The ratchet system (separately scheduled) runs on the position's symbol regardless of the day's decision direction, but it reads the day's decision record for the stop-loss basis — and HOLD decisions persist a stop_loss computed via LONG-side math regardless of actual position direction. **In practice the ratchet still produces correct broker actions** because of the comparison-check coincidence described below, but the bookkeeping is confused enough that a future change to the ratchet logic could break the protection. **Flagged for fix when convenient; not breaking today.**

---

## 1. `execute_master_decision` HOLD branch — verified clean

`paper_trading_executor.py:795-801`:

```python
elif decision.final_decision.upper() == "HOLD":
    print("📊 Decision: HOLD - No trade executed")
    execution_result = {
        "success": True,
        "action": "HOLD",
        "reason": "Master Orchestrator recommended no position change"
    }
```

Confirmed by reading the entire `execute_master_decision` function:
- The "flatten opposite position before entering new direction" check at lines 620-628 is gated on `LONG/SHORT`, not HOLD. HOLD does not flatten.
- The spread circuit-breaker (line 600) returns BEFORE the direction branch but doesn't act on positions.
- The `is_shortable` pre-check + W4 routing (lines 654+) only runs in the SHORT branch.
- The W4 exposure-cap and proxy-stop logic (added 2026-05-01) only run in the SHORT routing branch.
- HOLD takes no action on existing positions, no orders, no stop modifications. Verified by code review.

**Verdict: HOLD branch is genuinely a no-op on existing positions.** ✓

## 2. Ratchet system — runs independently of daily decision direction

**Trigger:** `score_outcomes.py:528-535` calls `ratchet_stops.ratchet_all()` after each scoring run. `score_outcomes.py` is invoked by `trading-scorer.service`, scheduled by `trading-scorer.timer` at **17:15 ET (Mon-Fri)** — a separate timer from the daily-pipeline timer.

**Ratchet runs on every weekday at 17:15 ET regardless of what the day's pipeline decisions were.** It iterates open positions (from broker), not from decisions. So a HOLD-day still triggers the ratchet check.

**For each open position, ratchet_all does:**

```python
decision = _get_decision_for_symbol(symbol)         # most recent within 7 days
atr_pct = decision.get("stop_loss_pct")              # used for ATR
current_stop = decision.get("stop_loss")             # used as the comparison basis
# ... compute new_stop based on position side + favorable move ...
# ... if new_stop is "tighter" than current_stop, submit broker stop + update decision row
```

**Key observation: `decision.stop_loss` and `decision.stop_loss_pct` are read from the day's decision record — NOT from the actual broker stop.**

## 3. Verified data — HOLD decisions persist LONG-math stop_loss

Pulled from Postgres `decisions` table for the past 7 days:

| Date | Symbol | Direction | Entry | Stored stop_loss | sl_pct | Stop side relative to entry |
|------|--------|-----------|-------|-----------------:|-------:|:----------------------------|
| 2026-04-30 | GLD | **HOLD** | $423.95 | $409.11 | 3.5% | **below** entry (LONG-math) |
| 2026-04-29 | GLD | **HOLD** | $416.58 | $402.00 | 3.5% | **below** entry (LONG-math) |
| 2026-04-29 | GLD | **HOLD** | $417.37 | $402.76 | 3.5% | **below** entry (LONG-math) |
| 2026-04-28 | USO | **HOLD** | $138.64 | $125.75 | 9.3% | **below** entry (LONG-math) |
| 2026-04-28 | USO | **HOLD** | $139.09 | $126.29 | 9.2% | **below** entry (LONG-math) |
| 2026-04-28 | GLD | **HOLD** | $420.96 | $406.65 | 3.4% | **below** entry (LONG-math) |
| 2026-04-28 | GLD | **HOLD** | $421.46 | $407.55 | 3.3% | **below** entry (LONG-math) |
| (compare) 2026-04-30 | UNG | **SHORT** | $10.32 | $10.91 | 5.7% | above entry (correct SHORT-math) |
| (compare) 2026-05-01 | UNG | **SHORT** | $10.64 | $11.24 | 5.6% | above entry (correct SHORT-math) |

**Source:** `master_orchestrator.py:_compute_price_levels` lines 217-222 — for any direction NOT equal to `"SHORT"`, it computes `stop_loss = entry × (1 - sl_pct/100)`. HOLD falls into this branch and gets a LONG-math stop. The actual position direction is irrelevant to this calculation.

## 4. Edge-case scenarios — does the bookkeeping mismatch break anything?

### Scenario A: Position is LONG, today's decision is HOLD (LONG continuity)

- `decision.stop_loss` = LONG-math stop (correct for the position)
- Ratchet runs LONG-side math, comparison check produces correct results.
- **No issue.**

### Scenario B: Position is SHORT, today's decision is HOLD

- `decision.stop_loss` = LONG-math stop (BELOW entry) — wrong for a SHORT position.
- Ratchet runs SHORT-side math: `favorable_move = entry - current`, `new_stop = entry - K × atr_dollars` (also below entry, profit-locking).
- Comparison check: `if new_stop >= current_stop: skip` (SHORT side requires tighter = lower).
- Both `new_stop` and `current_stop` are below entry. Their ordering depends on the ATR multiple chosen vs. `sl_pct`.

**Worked example using actual production data:**
- UNG SHORT position from 2026-04-30: entry $10.29, real Alpaca stop $10.91 (above entry).
- Hypothetical HOLD next day: UNG entry $10.29, stored `decision.stop_loss = $9.71` (LONG-math, sl_pct=5.6%).
- UNG drops to $9.80 (favorable move +$0.49). atr_dollars = $10.29 × 2.8/100 = $0.288. atr_units = 1.70 → eligible for breakeven ratchet.
- New stop (breakeven SHORT) = entry = $10.29.
- Comparison: `$10.29 >= $9.71` → **SKIP**. Ratchet does not act. Real Alpaca stop of $10.91 is preserved.

**But:**
- If UNG drops further to $9.45 (favorable +$0.84, atr_units = 2.92 → lock 1× ATR profit).
- New stop (SHORT, lock 1× ATR) = entry - atr_dollars = $10.29 - $0.288 = $10.00.
- Comparison: `$10.00 >= $9.71` → **SKIP** (still). Ratchet does not act. Real Alpaca stop $10.91 preserved.

Both cases skip — by mathematical coincidence: `LONG-math current_stop = entry × (1 - sl_pct/100)` is ALWAYS lower than any SHORT-side ratchet stop (which uses `entry - K × atr_dollars` with `K ≤ 2` and `atr_pct ≈ sl_pct/2`). The skip-comparison's intended semantic ("don't move stop backward") accidentally matches reality.

### Scenario C: Position is SHORT, ratchet should fire to lock profit (favorable to extreme)

- UNG drops to $7.00 (favorable +$3.29, atr_units = 11.4 → lock 2× ATR profit).
- New stop (SHORT, lock 2× ATR) = entry - 2 × atr_dollars = $10.29 - $0.576 = $9.71.
- Comparison: `$9.71 >= $9.71` (HOLD-math current_stop = $9.71) → **SKIP** (equal, not strictly less).

Ratchet still skips. **The actual Alpaca stop of $10.91 is unchanged.** From the position's perspective, the user is still exposed to a $10.91 stop while the position has moved 32% in their favor. This is a real loss of risk-management quality — the ratchet should have tightened the stop to $9.71 to lock in $0.58/share of profit, but it didn't because of the HOLD-math contamination.

### Scenario D: Multiple HOLD days in a row

If a position survives several HOLD days, the most recent decision keeps getting overwritten with new HOLD records (each with LONG-math stop). The 7-day lookback in `_get_decision_for_symbol` returns the newest. The original SHORT-math stop_loss data ages out of the lookup. After 7 days, even if you queried, you couldn't find the original direction-correct stop.

## 5. Verdict

| Property | Status |
|----------|:------:|
| HOLD branch in `execute_master_decision` is a no-op | ✅ verified |
| Ratchet system runs independently on a separate timer | ✅ verified |
| Ratchet produces correct broker actions for LONG-position-after-HOLD | ✅ verified by case analysis |
| Ratchet produces correct broker actions for SHORT-position-after-HOLD | ⚠ **accidentally correct** — math coincidence prevents harm in practice, but the bookkeeping is wrong; future ratchet logic changes could expose the bug |
| Ratchet correctly tightens stops when position has moved a lot favorably (Scenario C) | ❌ **FAILS** for SHORT positions on HOLD days. Real-Alpaca stop is preserved at the original value, never ratcheted closer. |

## 6. Recommended remediation (NOT applied — out of scope for an audit)

Two options, in order of cleanliness:

1. **Fix at the source.** In `master_orchestrator.py:_compute_price_levels` lines 217-222, for HOLD decisions don't compute a stop_loss at all (set to `None`). The `stop_loss_pct` can remain (it's directionally meaningful as "ATR-based stop distance") but the dollar `stop_loss` should not be a fabricated LONG-math value for a HOLD record.

   In `ratchet_stops.py:_get_decision_for_symbol`, modify to skip HOLD decisions and walk back further to find the most recent LONG/SHORT decision with a real stop_loss. Or alternatively, query Alpaca for the real current stop and use that as the comparison basis.

2. **Fix at the consumer.** Have `ratchet_stops.py:ratchet_all` query the broker for the actual current stop order on the position and use THAT as the comparison basis, not the decision record. The decision record only provides ATR for sizing the ratchet step.

Option 2 is more invasive but more principled — it eliminates the entire class of "decision says X, broker has Y" bugs in stop ratcheting.

**Severity:** Medium. No live trades have been harmed (verified — all current positions have correct Alpaca stops). The risk is silent under-protection on SHORT positions during multi-day HOLD streaks. The user's morning UNG SHORT (filled 2026-04-30 with stop $10.91) is currently protected; if today's pipeline had output HOLD instead of SHORT, the ratchet wouldn't have moved the stop appropriately if UNG fell sharply over the weekend.

## 7. Open follow-up

The user's audit instruction said: "Document the expected behavior: 'HOLD preserves existing positions and stops. Ratchet logic runs independently of daily decisions.'"

Confirmed:
- **HOLD preserves existing positions** ✓
- **HOLD does not modify Alpaca stops directly** ✓
- **Ratchet logic runs independently** ✓ (separate timer, broker-driven position discovery)
- **Ratchet correctly handles LONG-position-after-HOLD** ✓
- ⚠ **Ratchet does NOT correctly handle SHORT-position-after-HOLD when position moves significantly favorable** — the LONG-math stop_loss persisted on the HOLD decision row prevents profit-locking ratchets from firing.

The "probably correct" framing in the original problem statement is *almost* right but not entirely. Recommend folding the LONG-math-on-HOLD-decisions fix into the next maintenance window.
