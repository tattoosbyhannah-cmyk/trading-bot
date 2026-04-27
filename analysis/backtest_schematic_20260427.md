# Backtest Schematic — Trading Bot
**Date:** April 27, 2026
**Status:** Design document. No backtest code exists yet.

---

## 1. Questions the Backtest Will Answer

### Q1: Does the post-fix strategy produce positive risk-adjusted returns on USO?
- **Metric:** Annualized Sharpe ratio on USO daily decisions, post-Apr-17 strategy (after sentiment fix + deterministic math offload), walk-forward over 6 months of daily bars.
- **Assumptions:** 5 bps slippage per trade (round-trip), $0 commission (Alpaca paper), 5% base position sizing.
- **"Yes":** Sharpe > 0.5 over the 6-month period with >100 decisions scored.
- **"No":** Sharpe < 0.0, or win rate below 45% with negative mean return.
- **Why:** USO is the highest-volume, highest-ATR symbol and showed -4.00% mean 1d return in live paper trading — but that was dominated by a single event (Apr 16 crash) under pre-fix code. We need to know if the fixed system has edge on USO specifically.

### Q2: Does the majority vote mechanism improve over single-run decisions?
- **Metric:** Compare 3-vote majority outcome accuracy vs. the accuracy of each individual run (Run 1, Run 2, Run 3) taken alone over the same period.
- **"Yes":** Majority vote win rate exceeds each single-run win rate by ≥5 percentage points.
- **"No":** Majority vote performs same as or worse than the best individual run.
- **Why:** The 3-vote system costs 3x compute. If it doesn't improve accuracy, we should drop to 1 run and save 10 minutes per symbol per day.

### Q3: Does the whipsaw detector reduce losses vs. unrestricted trading?
- **Metric:** Compare total return with whipsaw detector active (HOLD forced when 2+ flips in 3 days) vs. always trading the majority vote direction.
- **"Yes":** Whipsaw-active version has smaller max drawdown AND higher total return.
- **"No":** Whipsaw detector causes missed profits that exceed avoided losses. (The live data hints at this — 60% of HOLDs missed profitable trades.)
- **Why:** The whipsaw detector is the most aggressive position-management rule. If it's net negative, we should relax it.

### Q4: Is confidence score predictive of outcome quality?
- **Metric:** Spearman rank correlation between confidence (1-10) and 1-day directional return, across all scored decisions in the backtest.
- **"Yes":** Correlation > 0.15 with p-value < 0.05 (n≥200).
- **"No":** Correlation ≈ 0 or negative. (Live data suggests this — 90% of decisions are confidence 8-9, no discrimination.)
- **Why:** If confidence is uncalibrated, it's wasting a schema field and misleading the position sizing layer.

### Q5: Does multi-asset diversification (USO + UNG + GLD) reduce drawdowns vs. USO-only?
- **Metric:** Max drawdown of the 3-asset portfolio vs. USO-only, same period, same sizing.
- **"Yes":** 3-asset max drawdown < 0.7× USO-only max drawdown.
- **"No":** Correlation between assets during drawdown events makes diversification illusory.
- **Why:** The system trades 3 correlated-at-extremes commodity ETFs. If they all draw down simultaneously (as in a risk-off event), the diversification is fake.

---

## 2. Data Architecture

### 2.1 Alpaca Price Bars
- **Current source:** `technical_analyst.py:47` — `StockBarsRequest(start=now-45d)`, no `end` param.
- **PIT status:** Naturally PIT-clean. Alpaca stores immutable historical bars. Requesting bars for 2025-06-15 returns the bars that existed on that date.
- **Leakage risk:** LOW. Only risk is if `start` isn't parameterized — currently hardcoded to `datetime.now() - 45d`.
- **Backtest change:** Pass `as_of_date` through MasterState → `fetch_bars()`. Set `start=as_of_date - 45d`, `end=as_of_date`. One parameter change.

### 2.2 EIA Crude Inventory
- **Current source:** `data_sources/eia_crude.py:54` — `https://api.eia.gov/v2/petroleum/stoc/wstk/data/`, params `length=52, sort=desc`.
- **PIT status:** NOT PIT-CLEAN. Returns the current 52 most recent weeks. The EIA API does not support "as of date X, what were the latest 52 weeks?" It returns whatever is published now, including revisions to historical values.
- **Leakage risk:** HIGH. Historical EIA values are revised weeks after initial publication. Using today's revised values for a backtest date in the past overstates signal quality.
- **Backtest options:**
  1. **Build forward-going snapshot archive** — start storing daily EIA responses in Postgres with `snapshot_date`. Captures revision history. Cost: ~1 KB/day, trivial. Limitation: only covers dates from when archiving starts.
  2. **Use EIA API date filters** — the API supports `start` and `end` params on the `period` field. Query `period <= backtest_date`. This returns the historical values *as revised today*, not as originally published. Partial fix: eliminates future data, but doesn't eliminate revision bias.
  3. **Accept revision bias** — document it as a known limitation and proceed. Revision bias in crude inventory is typically <1% of the reported value.
  - **Recommendation:** Option 2 (date-filtered query) for the first backtest, with option 1 running in parallel to build the archive for future backtests. The revision bias is small relative to the signal magnitude.

### 2.3 EIA Natural Gas Storage
- **Current source:** `data_sources/eia_natgas.py:54` — identical architecture to EIA crude.
- **PIT status / leakage / options:** Same as EIA crude. Same recommendation.

### 2.4 FRED Gold/Silver Macro
- **Current source:** `data_sources/fred_gold.py:58` — `https://api.stlouisfed.org/fred/series/observations`, fetches latest 60 observations of DTWEXBGS (DXY), DFII10 (real yields), DGS10 (nominal), T10YIE (breakeven).
- **PIT status:** NOT PIT-CLEAN. FRED returns currently-published values. Historical values can be revised (especially CPI-derived series like T10YIE).
- **Leakage risk:** MEDIUM. DXY and Treasury yields are rarely revised. Breakeven inflation (derived from TIPS vs nominal) has minor revision risk.
- **Backtest options:**
  1. **Switch to ALFRED endpoint** — `https://alfred.stlouisfed.org/fred/series/observations` supports `realtime_start` and `realtime_end` parameters, returning values exactly as published on a given date. This is the gold standard for PIT-clean FRED data.
  2. **Date-filter the standard endpoint** — add `observation_start` and `observation_end` params to limit returned observations to before the backtest date. Same revision caveat as EIA.
  3. **Build daily snapshot archive** — same as EIA option 1.
  - **Recommendation:** Option 1 (ALFRED) is correct and requires only an endpoint URL change + two extra params. The FRED API key works on ALFRED. This should be the first data source upgraded.

### 2.5 Alpaca News
- **Current source:** `enhanced_sentiment_analyst.py:60` — `NewsRequest(symbols=..., start=now-48h, end=now)`.
- **PIT status:** UNCLEAR. Alpaca's news API accepts `start` and `end` date params. If I request news from 2025-06-15 00:00 to 2025-06-16 00:00, does it return the articles that existed on June 16, or does it return currently-available articles with timestamps in that range?
- **Leakage risk:** HIGH if articles are backfilled or if Benzinga (Alpaca's source) revises/removes articles retroactively. Unknown behavior.
- **Backtest options:**
  1. **Build forward-going news archive** — store every news response in Postgres with `query_date`. From now on, the backtest can replay exactly what the bot saw. Limitation: only covers future dates.
  2. **Test the API empirically** — query a known historical date range and compare against known events. If articles appear that weren't published until later, the API is leaking.
  3. **Exclude sentiment from the backtest** — run the backtest with sentiment fixed at `neutral/3` (the "no news" default). Compare backtest-with-sentiment vs. backtest-without-sentiment to measure the sentiment agent's marginal contribution.
  4. **Use a different historical news source** — NewsAPI, Polygon.io, or EODHD offer historical news with known retention policies.
  - **Recommendation:** Option 3 (exclude sentiment) for the first backtest. Simultaneously start option 1 (archive) for future backtests. Sentiment is the highest-leakage, hardest-to-validate source; removing it from v1 backtest is honest.

### 2.6 RAG Knowledge Base (ChromaDB / pgvector)
- **Current source:** `rag_bull_researcher.py:39` — queries with optional `as_of_date` filter on `published_date_int`.
- **PIT status:** FULLY PIT-CLEAN. The `published_date_int` metadata was backfilled on all 4,973 chunks. The filter `WHERE published_date_int <= YYYYMMDD` correctly restricts to sources published before the backtest date.
- **Leakage risk:** LOW. Only risk: a source's `published_date` is wrong in `rag_sources.yaml`. Spot-checkable.
- **Backtest change:** Pass `as_of_date` from orchestrator → researchers. The filter mechanism already exists. One line missing: `run_research_debate()` doesn't forward `as_of_date` to `researcher_state`. Trivial fix.

### 2.7 LLM Inference
- **Current config:** `config/models.yaml` — Qwen3-30B-A3B, temperature 0.0, seed 42.
- **PIT status:** N/A — the LLM doesn't change over time (same GGUF file on disk).
- **Determinism:** Temperature 0.0 + seed 42 = greedy decoding. llama.cpp with `--seed 42` produces deterministic output for identical inputs *on the same hardware*. Cross-hardware or cross-driver non-determinism is possible due to GPU floating-point reordering in GEMM operations (known issue with ROCm/CUDA).
- **Leakage risk:** NONE from the LLM itself. The model's training data has a knowledge cutoff (May 2025 for Qwen3) which is before all backtest dates.

### Summary Table

| Source | PIT Status | Leakage Risk | Backtest Fix |
|--------|-----------|-------------|-------------|
| Alpaca bars | Clean (parameterizable) | Low | Add date params |
| EIA crude | Revision bias | Medium | Date-filter API query |
| EIA natgas | Revision bias | Medium | Date-filter API query |
| FRED gold/silver | Revision bias | Medium | Switch to ALFRED |
| Alpaca news | Unknown | HIGH | Exclude from v1 backtest |
| RAG knowledge base | Clean (has filter) | Low | Forward as_of_date param |
| LLM inference | N/A | None | Already deterministic |

---

## 3. Backtest Harness Design

### 3.1 Date Parameterization

The backtest replays one trading day at a time. For each date:

1. `run_complete_trading_analysis(symbol, variation, as_of_date="2025-08-15")` passes the date into `MasterState`.
2. `fetch_bars()` queries Alpaca with `end=as_of_date`.
3. `fetch_broader_news()` is skipped (sentiment fixed at neutral/3) or queries with `end=as_of_date`.
4. EIA/FRED sources query with date filters (or use cached snapshots).
5. RAG researchers filter by `published_date_int <= as_of_date`.
6. The LLM sees only data that existed on that date.

**Files to parameterize:**
- `master_orchestrator.py:run_complete_trading_analysis()` — add `as_of_date` to state
- `master_orchestrator.py:run_data_analysts()` — forward to dual_graph
- `technical_analyst.py:fetch_bars()` — use `as_of_date` for bar range
- `enhanced_sentiment_analyst.py:fetch_broader_news()` — use `as_of_date` or return neutral
- `data_sources/eia_crude.py`, `eia_natgas.py` — add date filter to API params
- `data_sources/fred_gold.py`, `fred_silver.py` — switch to ALFRED or add date filter

### 3.2 LLM Output Caching

**This is the critical path.** One pipeline run takes ~2.5 minutes (8 agents × ~20 seconds each). A 90-day backtest with 3 symbols × 3 votes = ~810 pipeline runs = ~34 hours of compute. Without caching, iteration is impractical.

**Cache key shape:**
```
cache_key = hash(
    agent_name,
    symbol,
    as_of_date,
    prompt_text_hash,  # captures all data inputs
    model_name,
    temperature,
    seed,
    rag_chunks_limit,  # variation params
    rag_query_n,
    debate_order,
)
```

**Storage:** Postgres table `llm_cache`:
```sql
CREATE TABLE llm_cache (
    cache_key TEXT PRIMARY KEY,
    agent TEXT,
    symbol TEXT,
    as_of_date DATE,
    response_json JSONB,
    prompt_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Invalidation policy:** Cache entries are keyed by prompt hash, so any change to input data, prompt template, or RAG chunks automatically invalidates. Explicit invalidation only needed if the LLM model itself changes (new GGUF file).

**Expected speedup:** After the first full run, a re-run with identical parameters should complete in <1 minute (cache reads only). A re-run with a code change to one agent (e.g., new prompt template for risk_gatekeeper) only re-computes that agent's cache entries.

### 3.3 Determinism

**Greedy decoding (temperature 0.0)** makes the LLM output deterministic for identical inputs on the same hardware. The remaining non-determinism sources:

1. **GPU GEMM reordering** — floating-point addition is non-associative. Different thread scheduling can produce different results at the least-significant-bit level. On llama.cpp with `--seed 42`, this is typically deterministic in practice, but not guaranteed by the hardware.
2. **llama.cpp version changes** — upgrading llama.cpp can change the output even with identical weights and seed, due to kernel changes.

**Recommendation:** Accept approximate determinism. Validate by running the same day twice and comparing outputs. If outputs differ, increase the vote count to 5 and rely on majority stability rather than bit-exact reproducibility. Do not chase hardware-level determinism — it's not achievable on consumer GPUs without enormous performance cost.

### 3.4 Vote Architecture

**For the first backtest:** Run 1 vote per day per symbol (not 3). This reduces compute by 3× and produces a clean signal. The first backtest's goal is to detect whether there's any directional edge at all — vote ensembling is an optimization question for later.

**For Q2 specifically (vote mechanism value):** Run 3 votes for a subset of dates (e.g., every 5th day) and compare the 3-vote majority against each single vote. This answers Q2 without running the full 3× compute.

### 3.5 Scope

- **Symbols:** USO, UNG, GLD (the current active instruments)
- **Date range:** 2025-07-01 to 2026-04-15 (9.5 months, ~200 trading days). Starts after the Qwen3 model's training cutoff to avoid training data leakage. Ends before live trading started (Apr 16) to avoid overlap with scored paper-trading data.
- **Cadence:** Every trading day. The daily pipeline runs once per day; the backtest should match that cadence.
- **Total compute (1 vote):** 200 days × 3 symbols × 1 vote × 2.5 min = ~25 hours for the first uncached run. Subsequent runs with caching: minutes.

---

## 4. Scoring Layer

### 4.1 Per-Decision Metrics
- **1-day directional return:** `(close[t+1] - close[t]) / close[t]`, signed by direction (positive = correct).
- **5-day directional return:** Same, with close[t+5].
- **Hit rate:** Did price touch the target before the stop (using intraday high/low)?
- **Time-in-trade:** Days from entry until stop, target, or 30-day horizon (whichever first).

### 4.2 Aggregate Metrics
- **Total return:** Compound return of all decisions, assuming base position size and risk-score scaling.
- **Annualized Sharpe ratio:** `(mean daily return - risk-free) / std(daily returns) × sqrt(252)`. Use 3-month T-bill rate for risk-free.
- **Max drawdown:** Largest peak-to-trough decline in cumulative return curve.
- **Win rate:** % of decisions with positive directional return.
- **Profit factor:** Gross profit / gross loss.

### 4.3 Benchmarks
1. **Buy-and-hold each ETF** — total return of holding USO, UNG, GLD over the backtest period. The bot must beat this to justify its existence.
2. **Equal-weight basket** — 1/3 USO + 1/3 UNG + 1/3 GLD, rebalanced monthly. Measures whether the bot's asset allocation adds value.
3. **Signal-only strategy (no LLM)** — use only the Python-computed signals (ATR, SMA crossover, RSI, EIA/FRED categorical signals) with fixed rules (e.g., "go LONG when SMA5 > SMA20 and fundamentals ≠ bearish"). This measures whether the LLM adds value beyond the mechanical signals.

### 4.4 Statistical Significance
- **Minimum sample:** 200 decisions (200 trading days × 1 symbol, or 67 days × 3 symbols). At n=200, a 55% win rate is distinguishable from 50% at p<0.05 (binomial test).
- **Sharpe test:** Use the Lo (2002) adjusted Sharpe ratio test for autocorrelated returns. With 200 daily observations, Sharpe > 0.5 is detectable at p<0.05.
- **Multiple comparison correction:** We're testing 3 symbols + 1 aggregate. Apply Bonferroni correction (p < 0.0125 per test).

---

## 5. Iteration Plan

### Phase A: Correctness Validation (1 week)
- **Scope:** 1 symbol (USO), 1 month (March 2026), 1 vote per day, no caching.
- **Goal:** Verify the harness produces decisions that look reasonable. Check that bar data, fundamentals, and RAG chunks are correctly dated.
- **Validation:** Run the backtest over April 16–27 (the live paper-trading window) and compare against the 14 actual USO decisions in Postgres. The backtest should produce the same direction for ≥80% of dates. Divergences must be explainable (different news, different RAG chunks, non-determinism).

### Phase B: Full Backtest (2 weeks)
- **Scope:** 3 symbols, 200 trading days (Jul 2025 – Apr 2026), 1 vote per day with caching.
- **Goal:** Produce the dataset for answering Q1–Q5.
- **Compute:** ~25 hours first run, minutes for re-runs.

### Phase C: Sensitivity Analysis (ongoing)
- **Vary one parameter at a time:** position size (1% vs 5% vs 10%), stop distance (1.5× ATR vs 2× vs 3×), whipsaw threshold (2 flips vs 3 flips).
- **Goal:** Identify which parameters the strategy is sensitive to. If Sharpe changes dramatically with small parameter changes, the strategy is fragile.

### Invalidation Conditions
Any of these changes invalidate prior backtest results and require a re-run:
- Model change (different GGUF file)
- Prompt template change for any agent
- RAG corpus change (new sources ingested)
- Signal computation change (ATR formula, RSI period, etc.)
- Position sizing or stop logic change

Track via `calculation_run_id` and `git commit hash` stored in the backtest results table.

---

## 6. Honest Limitations

Even a perfect backtest cannot tell us:

1. **News leakage residual.** If sentiment is excluded from v1 backtest, we're testing a different system than the one that trades live. If sentiment is included, we can't verify Alpaca's news API doesn't backfill articles. Either way, the sentiment dimension is unvalidated.

2. **Regime change.** A model calibrated on Jul 2025 – Apr 2026 (tariff war, Iran tensions, Fed pivot uncertainty) may fail completely in a different regime (e.g., a deflationary recession, a commodity supercycle). The backtest covers one regime. It cannot predict the next one.

3. **Live execution differences.** The backtest assumes fills at close prices with fixed slippage. Live execution involves market orders that fill at whatever the ask is, partial fills, order queue position, and Alpaca's paper trading simulator behavior (which is more generous than real markets). The slippage assumption (5 bps) may understate reality for USO during volatile sessions.

4. **LLM knowledge contamination.** Qwen3's training data includes financial news and analysis through May 2025. For backtest dates between May 2025 and April 2026, the LLM "knows" what happened next — not from the prompt, but from its training data. This is an unfixable form of look-ahead bias inherent to using a pre-trained LLM for historical simulation. The severity depends on how much the LLM relies on parametric knowledge vs. the RAG context in its responses. Unknown.

5. **Revision bias in fundamentals.** EIA and FRED data are revised after initial publication. The backtest uses currently-published values, not as-originally-published values (except FRED via ALFRED). This overstates the quality of fundamental signals by a small, unmeasured amount.

6. **Sample size for rare events.** 200 trading days contains approximately zero black swan events. The bot's behavior during a flash crash, circuit breaker halt, or 10-sigma event is untested and untestable via backtest.

7. **Overfitting risk.** With 5 questions and multiple parameter variations, there is a substantial risk of finding a configuration that "works" in-sample by chance. The Bonferroni correction helps but doesn't eliminate this. The only real protection is out-of-sample validation on live data — which is what the paper trading is for.
