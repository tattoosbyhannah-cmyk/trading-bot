# Cost Adjuster Review — April 27, 2026

## What the function does today

`adjust_for_costs(base_position_pct, spread_bps)` in `paper_trading_executor.py:110` reduces position size when estimated round-trip trading costs exceed a ceiling.

**Logic:**
1. If `spread_bps > 30` (MAX_PLAUSIBLE_SPREAD), clamp to 30 bps.
2. Compute `avg_historical_slippage` from `fill_records.jsonl` (currently 11.9 bps avg across 11 fills).
3. `estimated_round_trip = 2 × spread + 2 × avg_slippage`.
4. If round-trip > 50 bps (COST_CEILING_BPS), reduce position by `ceiling / round_trip`.
5. Return the reduced percentage.

**Callers:** `paper_trading_executor.py:343` (daily pipeline execution), `intraday/swing_executor.py:336` (intraday entry).

## Where stale quotes come from

`estimate_spread()` calls `broker.get_latest_quote(symbol)` which uses Alpaca's `StockLatestQuoteRequest` with no `feed` parameter specified. This defaults to the account's default feed — **IEX for free/paper accounts**.

IEX quotes are:
- Derived from a single exchange (Investors Exchange), not the consolidated tape
- Stale outside regular trading hours (9:30 AM – 4:00 PM ET)
- Can show wide bid-ask during pre-market because IEX has no pre-market session
- The "latest" quote may be hours old (last IEX quote from previous close)

The pipeline timer fires at **9:35 AM ET** (Mon/Tue/Fri) or **10:45 AM ET** (Wed/Thu). At 9:35 AM, IEX quotes may be only 5 minutes into the session — some ETFs haven't printed a tight IEX quote yet. At 10:45 AM, this is less of an issue.

## Reductions that actually fired (last 30 days)

| Date | Symbol | Raw Spread | Clamped | Before | After | Reduction | Root Cause |
|------|--------|-----------|---------|--------|-------|-----------|------------|
| 2026-04-17 | GLD | 6.5 bps | N/A | 0.20% | 0.16% | 20% | Historical slippage avg inflated (early data) |
| 2026-04-20 | UNG | 9.3 bps | N/A | 0.20% | 0.18% | 10% | Same — avg slippage pushing round-trip over 50 |
| 2026-04-21 | USO | 283.6 bps | N/A* | 0.20% | 0.02% | 91% | Pre-market stale IEX quote |

*The 30 bps clamp was added Apr 24. The Apr 21 event had no clamp.

Only 3 reductions ever fired. The Apr 21 USO event was catastrophic (91% reduction → 1 share). The other two were mild and arguably correct (the avg slippage was legitimately high early on).

**After the Apr 24 clamp was added:** No production trade has executed (--execute disabled). The dry-run on Apr 27 showed the clamp working (597 → 30 bps) but still producing a 40% reduction due to the `2 × 30 = 60 > 50` ceiling math.

## Historical slippage data quality

The `_avg_historical_slippage()` function averages all fills in `fill_records.jsonl`. The dataset (n=11) includes:
- 74.8 bps slippage on a UNG buy (Apr 17 — large market order, thin book)
- 18.0-18.5 bps on two UNG trades
- 0.0-6.5 bps on everything else

The 74.8 bps UNG outlier inflates the average from ~5 bps (median) to 11.9 bps. This is an outlier problem, not a systematic cost. The avg is not robust.

## Failure modes

1. **Stale IEX quotes before/during early market hours** — spread appears 100-600+ bps, position crushed to near-zero. This is the primary failure.

2. **Historical slippage outliers** — a single large fill inflates the slippage average, causing unnecessary reductions even when the spread is tight.

3. **Circular feedback** — small positions fill with higher slippage (market impact is higher per-share on small orders), which increases avg slippage, which further reduces positions. Self-reinforcing death spiral toward 1-share orders.

4. **Cross-symbol contamination** — `_avg_historical_slippage()` pools all symbols. A bad UNG fill penalizes USO sizing.

5. **The 30 bps clamp is arbitrary** — USO typically trades at 2-5 bps RTH spread. Clamping at 30 bps means even after the clamp, the function assumes 6x the true cost, guaranteeing a reduction.

---

## Addendum — Option D dry-run results (2026-04-27, evening)

**Decision implemented:** Option D — cost adjuster removed entirely.

**Changes:**
- `adjust_for_costs()` and `_avg_historical_slippage()` deleted from `paper_trading_executor.py`.
- Both call sites updated: `paper_trading_executor.py:execute_master_decision` and `intraday/swing_executor.py` now flow `position_size_pct` directly from the risk gatekeeper to `calculate_share_quantity` with no cost reduction step.
- `estimate_spread()` retained. Spread > 50 bps logs WARNING (informational, post-hoc analysis only).
- New circuit-breaker: spread > 200 bps REJECTS the trade entirely (returns 0 shares, logs CRITICAL).

**Dry-run results** (mocked broker, $100k portfolio, USO @ $75, 5% gatekeeper position):

| Scenario | Spread (bps) | Old behavior | New behavior | Shares |
|----------|-------------:|--------------|--------------|-------:|
| Apr 21 raw IEX | 283.6 | 0.02% (1 share, 91% reduction) | **REJECTED** (circuit-breaker) | 0 |
| Apr 27 raw IEX | 597.0 | 30→clamp→40% reduction (~40 shares pre-clamp logic) | **REJECTED** (circuit-breaker) | 0 |
| Counterfactual RTH | 5.0 | full size | full size | **66** |
| Boundary | 199.0 | reduced under old | full size | **66** |

**Discrepancy with original Option D framing:** the spec said "both [Apr 21 / Apr 27] should produce full-size positions (since spreads are below 200 bps even when stale)." The recorded raw IEX spreads on those dates were 283.6 and 597 bps — both above the 200 bps threshold. Under Option D as implemented, both still reject.

This is arguably *correct* behavior: a 283.6 bps or 597 bps quoted spread on USO is indistinguishable from a flash-crash or trading halt from the executor's point of view. The circuit-breaker exists for exactly that case. But it means Option D does not, by itself, restore a full-size USO position when the IEX feed is stale at 9:35 AM ET — it just changes the failure mode from "tiny position" to "no position + CRITICAL alert".

**Recommended follow-ups (not in this change):**
1. Switch the Alpaca quote feed from IEX (default) to SIP to fix the root cause of stale pre-market quotes — this is the one change that would actually let Option D produce full-size positions on Apr 21 / Apr 27 type mornings.
2. Move the 9:35 AM ET timer to 9:45 or 10:00 AM ET so IEX has time to print fresh quotes.
3. Until either of the above lands, expect early-week (Mon/Tue/Fri) executions to occasionally trigger the 200 bps circuit-breaker on stale feeds. The CRITICAL alert is the signal to investigate.

**Files modified:**
- `paper_trading_executor.py` — functions removed, circuit-breaker added (backed up to `.pre-cost-removal`)
- `intraday/swing_executor.py` — import + call site updated
- `tests/test_position_sizing.py` — `TestSpreadCircuitBreaker` class added (6 tests, all passing)
- `CHANGELOG.md` — one-line note added

Full test suite: 40 passed, 1 skipped, 1 xfailed.
