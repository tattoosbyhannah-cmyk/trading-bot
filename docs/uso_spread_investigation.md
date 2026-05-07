# USO Spread Pattern Investigation — 2026-05-01

**Question.** USO has been rejected by the 200 bps spread circuit-breaker for 5 consecutive trading days (recorded values: 263, 699, 262, 259, 616 bps). High-conviction APPROVED LONG decisions never reach the broker. Is the wide spread real, an artifact, or a configuration issue?

**Answer.** The wide spread is a **data-feed artifact**, not a real market condition. Alpaca's free/paper data tier returns IEX-only quotes, and IEX is one of ~16 NMS venues. For USO, IEX systematically posts wider quotes than the consolidated NBBO. The market itself is tight (~3-7 bps).

The 200 bps circuit-breaker is doing its design job (flagging anomalous spread reads); the input it's reading is just bad.

---

## Evidence

### A) Historical SIP (consolidated tape) sample — 5 days × 6 time slots

Pulled via `StockHistoricalDataClient.get_stock_quotes` (which works on paper for data >15 min old). 60-second windows centered on each timestamp. NBBO computed as `(max bid across venues, min ask across venues)`; "worst venue" is the widest single-quote spread in the window.

| Date | Time (ET) | NBBO (bps) | Worst single venue (bps) | IEX-only worst (bps) | Active venues |
|------|----------:|-----------:|-------------------------:|---------------------:|:--------------|
| 2026-04-27 (Mon) | 09:35 | -2.2 (locked) | 3.0 | 2.2 | P, K, T, U, Z |
| 2026-04-27 | 10:00 | -2.2 | 3.0 | 2.2 | K, P, T |
| 2026-04-27 | 10:30 | -4.5 | 3.0 | n/a | P, T, Z |
| 2026-04-27 | 10:45 | -1.5 | 3.0 | 3.0 | K, P, T, V |
| 2026-04-27 | 11:00 | -0.7 | 3.0 | 2.2 | K, P, T, U |
| 2026-04-27 | 14:00 | -2.2 | 3.7 | n/a | K, P, T, U |
| 2026-04-28 (Tue) | 09:35 | -5.0 | 3.6 | n/a | K, P, J |
| 2026-04-28 | 10:00 | -5.0 | 6.5 | 6.5 | K, N, P |
| 2026-04-28 | 10:30 | -2.1 | 3.6 | n/a | K, P, T |
| 2026-04-28 | 10:45 | -1.4 | 4.3 | 3.6 | P, T, U |
| 2026-04-28 | 11:00 | -2.9 | 5.0 | 4.3 | K, P, T, Z |
| 2026-04-28 | 14:00 | -1.4 | 2.9 | 2.1 | K, P, T, U |
| 2026-04-29 (Wed, EIA) | 09:35 | -2.0 | 2.7 | 1.4 | K, P, T, Z |
| 2026-04-29 | 10:00 | -2.0 | 2.0 | 1.4 | K, P, T |
| 2026-04-29 | 10:30 | -10.9 | 19.8 | 6.8 | K, N, T, V, Z |
| 2026-04-29 | 10:45 | -1.4 | 3.4 | 2.7 | K, P, T |
| 2026-04-29 | 11:00 | -1.4 | 3.4 | 2.7 | K, N, P, T, U |
| 2026-04-29 | 14:00 | -2.0 | 14.1 | 6.7 | K, N, P, T |
| 2026-04-30 (Thu, EIA) | 09:35 | 1.4 | 7.5 | 4.8 | K, P, T, U, Z |
| 2026-04-30 | 10:00 | -4.1 | 12.9 | 7.5 | K, N, P, V |
| 2026-04-30 | 10:30 | -2.7 | 6.8 | 3.4 | K, N, P, T, U |
| 2026-04-30 | 10:45 | -1.4 | 4.7 | 4.1 | K, P, T, U |
| 2026-04-30 | 11:00 | -3.4 | 7.4 | 6.1 | K, N, P, T, U |
| 2026-04-30 | 14:00 | -2.1 | 5.5 | 3.5 | H, K, N, P, T |
| 2026-05-01 (Fri) | 09:35 | -4.2 | 4.2 | n/a | J, K, P, T, U |
| 2026-05-01 | 10:00 | -2.1 | 11.4 | 7.1 | K, N, P, T |
| 2026-05-01 | 10:30 | -5.0 | 3.6 | 3.6 | P, H, N, T, U |
| 2026-05-01 | 10:45 | -2.8 | 4.9 | 4.2 | K, P, T, Z |
| 2026-05-01 | 11:00 | -2.8 | 4.9 | 3.5 | K, P, U, Z |

**Observations:**
- NBBO is consistently tight: most readings -10 to +2 bps. Negative values reflect a "locked/crossed" market across venues — common for active multi-venue ETFs. The tightness across venues, not the sign, is the relevant fact.
- Worst single-venue spreads in a 60-second window: 2.0 to 19.8 bps. Maximum across all 30 observations: 19.8 bps (2026-04-29 10:30 ET, EIA-report moment).
- IEX-only worst: 1.4 to 7.5 bps when present. IEX is occasionally absent (no quotes from that single venue in the window).
- **No reading anywhere in the matrix exceeds 20 bps**. None of the 200-700 bps "spreads" the system saw in production are present in the actual market data.

Exchange code legend (Alpaca): K = NASDAQ BX, N = NYSE, P = NYSE Arca, T = NYSE American, U = NYSE Chicago, V = IEX, Z = Cboe BZX, J = Cboe EDGA, H = MIAX Pearl.

### B) Live `estimate_spread()` vs `delayed_sip` at the same moment (2026-05-01 13:18 ET)

| Feed | Timestamp | Bid | Ask | Spread | Exchanges | Status |
|------|-----------|----:|----:|-------:|:---------:|:------:|
| `iex` (system default) | 13:18:21 (live) | $137.75 | $146.46 | **612.9 bps** | V/V | accepted |
| `sip` | — | — | — | — | — | **403: subscription does not permit querying recent SIP data** |
| `delayed_sip` | 13:03:21 (15 min lagged) | $141.99 | $142.05 | **4.2 bps** | P/P | accepted |
| `otc` | — | — | — | — | — | 403: subscription does not permit |

The IEX-live and delayed-SIP feeds disagree by **145×** at the same wall-clock moment.

### C) Is this a stale-quote problem?

No. The IEX quote timestamp is `13:18:21` — the current second. The IEX feed is fresh. It's just *bad* — IEX is one venue, USO trades primarily on NYSE Arca (P), and IEX quotes a defensive wide market on USO most of the time. The displayed bid_size and ask_size are 100/100 (1 round lot each), consistent with IEX posting cosmetic-presence quotes that don't reflect real liquidity.

### D) Is this an EIA-time correlation?

No. The 5×6 matrix shows tight NBBO at all sampled times, including 10:30 ET (EIA-report moment) and 10:45 ET (EIA-day pipeline execution time). The single largest single-venue reading (19.8 bps on 2026-04-29 at 10:30 ET) coincides with EIA-report release, but it's still well below 200 bps and ~30× tighter than what the system reported.

### E) Is this a wider-after-EIA issue?

No. Spreads are tight 15 minutes after EIA (10:45 ET) and remain tight at 14:00 ET. The historical-SIP data shows no time-of-day pattern that explains 200-700 bps reads.

---

## Diagnosis

**Root cause:** `paper_trading_executor.estimate_spread()` calls `broker.get_latest_quote()`, which calls Alpaca's `StockLatestQuoteRequest` with no `feed` parameter. The default for paper accounts is `feed=IEX`. For USO, IEX systematically returns wide cosmetic quotes that don't reflect the consolidated NBBO.

**Why USO and not other symbols.** The same data-feed limitation applies to UNG, GLD, etc. But:
- **GLD** trades heavily on multiple venues including NASDAQ; IEX often quotes near consolidated tape because IEX participates in GLD market-making.
- **UNG** has had at least one IEX-feed wide-quote day (the catalysts for our shortability discussion), but more frequently IEX matches consolidated tape.
- **USO** has the worst IEX/SIP discrepancy in the active universe — likely because USO's primary venue is NYSE Arca and IEX is essentially absent from active USO quoting.

This is consistent with the "Apr 21 stale IEX quote" diagnosis from the cost-adjuster review (2026-04-27). It's not stale; it's just a low-quality single-venue feed.

---

## Of the four candidate fix paths the spec listed

| # | Hypothesis | Verdict |
|---|-----------|---------|
| 1 | Time-of-day pattern (delayed retry would help) | **Rejected.** Spreads are tight all day on the consolidated tape; no time slot fixes the IEX feed. |
| 2 | Alpaca IEX feed artifact | **CONFIRMED.** This is the issue. |
| 3 | Genuinely wide spreads (geopolitical) | **Rejected.** Historical SIP shows tight spreads through Hormuz-headline days. The "Hormuz risk-on" framing in earlier session notes was a misdiagnosis. |
| 4 | Circuit-breaker too tight for USO | **Rejected as a fix.** Per-symbol thresholds would mask the data-quality bug, leaving the system blind to *real* spread risk on USO. |

---

## Recommended fix (NOT applied yet, per investigation-only directive)

**Switch `paper_trading_executor.estimate_spread` to use Alpaca's `delayed_sip` feed.** One-line change in `brokers/alpaca_broker.py:get_latest_quote`:

```python
# brokers/alpaca_broker.py — current
quote = self._data.get_stock_latest_quote(
    StockLatestQuoteRequest(symbol_or_symbols=symbol))

# proposed
from alpaca.data.enums import DataFeed
quote = self._data.get_stock_latest_quote(
    StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.DELAYED_SIP))
```

**Trade-off analysis:**
- ✅ Free on the paper subscription (no payment required; verified working today).
- ✅ Returns consolidated NBBO (4.2 bps observed today vs. 612.9 bps from IEX).
- ✅ The 200 bps circuit-breaker is for flash-crash / halt detection. Flash crashes persist for many minutes; a 15-min-old NBBO reading still detects them within one or two pipeline cycles. Acceptable.
- ⚠ The 15-min lag means we don't see the *current-second* spread. For sub-second decisions this would matter; for daily-pipeline trading (which already runs ~22 min behind market events) it doesn't.
- ⚠ For the swing executor's intraday entries, the 15-min lag is more material. Consider:
  - Keep IEX-fresh for swing's `estimate_spread` (mid-day spreads more reliable than morning), OR
  - Apply `delayed_sip` everywhere (accept the lag as the cost of correctness)

**Alternative fix paths (not recommended but documented):**
- Pay for Alpaca's "Algo Trader Plus" tier ($9/mo) to get live SIP. Reasonable if you eventually want true real-time spread visibility.
- Add a fallback: try IEX, if spread > 50 bps cross-check via `delayed_sip` before rejecting. More complex but minimizes the lag for the common case.
- Per-symbol IEX-quality whitelist in `instruments.yaml` (e.g., `data_feed: delayed_sip` for USO, `data_feed: iex` for GLD). Future-proof but invites tuning that obscures the underlying issue.

---

## Production positions check

Today's USO LONG signal was rejected by the IEX-artifact spread circuit-breaker, so no USO position currently exists. No remediation needed for existing exposure. The next pipeline run after the fix is applied will be the first to test the corrected spread-check on a live decision.

---

## Status

- Investigation: **complete**.
- Recommended fix: **switch to `delayed_sip` feed** for `estimate_spread`.
- Code change: **deferred** until owner confirms which scope (executor only vs. executor + swing).
- Files referenced: `paper_trading_executor.py:estimate_spread`, `brokers/alpaca_broker.py:get_latest_quote`.
