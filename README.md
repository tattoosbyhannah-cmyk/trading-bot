# trading-bot

**An open-source, fully self-hosted multi-agent LLM trading system for commodity ETFs.**
Eight LangGraph agents backed by a locally served Qwen3 model debate, vote, and size trades on crude oil, natural gas and gold — with every risk limit enforced in plain Python that the model cannot override.

Built and run on Linux. No cloud LLM APIs. The only outbound calls are to the broker (Alpaca paper trading), two public data agencies (EIA, FRED) and SMTP for alerts.

> **Status:** Phase 3 — paper trading on a live Alpaca paper account, scheduled daily via systemd. This is a research system, not investment advice, and it has not produced a positive track record yet (see [Results so far](#results-so-far)).

---

## Why this exists

Most "LLM trading bot" repos hand the model raw prices and ask it to decide. This project started the same way and broke immediately: the first model used (Qwen3-Coder-30B) produced rationales like *"price is below $123.33"* when the price was $124.82. Coder-class models are unreliable at numeric comparison.

The fix became the central design rule of the whole system:

**The LLM never does arithmetic.** Python computes every indicator, every comparison and every price level. The model receives booleans and categories, returns a typed Pydantic object with percentages only, and Python converts those back into prices, share counts and stop orders. Risk limits are hard-coded and sit *outside* the model loop.

Everything else — the debate structure, the RAG provenance system, the majority vote, the backtest isolation — follows from that.

## Architecture

```
                 ┌──────────────────────────────────────────────────┐
                 │  daily_pipeline.sh   (systemd timer, 09:45 ET)   │
                 │  for SYM in USO UNG GLD:                         │
                 │     majority_vote_orchestrator.py SYM  (×3 runs) │
                 └──────────────────────┬───────────────────────────┘
                                        │ each run = one LangGraph StateGraph
                                        ▼
   ┌─────────────────┐   ┌─────────────────┐
   │ Technical        │   │ Fundamentals     │   ← run in parallel (dual_analyst.py)
   │ Alpaca bars →    │   │ EIA / FRED →     │
   │ SMA/RSI/ATR in   │   │ 52-wk range,     │
   │ Python → booleans│   │ inventory deltas │
   └────────┬─────────┘   └────────┬─────────┘
            └──────────┬───────────┘
                       ▼
            ┌─────────────────────┐
            │ Sentiment            │   Alpaca News, 48h, deduped, top 15 headlines
            │ confidence floored   │   zero articles ⇒ "uninformative", not "neutral"
            │ by article count     │
            └──────────┬──────────┘
                       ▼
   ┌─────────────────┐   ┌─────────────────┐
   │ Bull researcher  │   │ Bear researcher  │   RAG over 5,060 chunks / 40 sources
   │ cites chunk IDs  │   │ cites chunk IDs  │   invented citations stripped in Python
   └────────┬─────────┘   └────────┬─────────┘
            └──────────┬───────────┘
                       ▼
            ┌─────────────────────┐
            │ Debate judge         │   citation metrics computed in Python;
            │ picks winning side   │   RAG-grounded arguments weighted higher
            └──────────┬──────────┘
                       ▼
            ┌─────────────────────┐
            │ Risk gatekeeper      │   APPROVED / MODIFIED / REJECTED + risk score
            │ live Alpaca portfolio│   Python sets final size from the score
            └──────────┬──────────┘
                       ▼
            ┌─────────────────────┐
            │ Final decision       │   LONG / SHORT / HOLD, % levels only
            └──────────┬──────────┘
                       ▼
            ┌─────────────────────┐
            │ Majority vote (3)    │   plurality on direction; HOLD if no quorum
            └──────────┬──────────┘
                       ▼
            ┌─────────────────────┐
            │ Paper trading        │   spread breaker, exposure cap, proxy routing,
            │ executor             │   STOP_LIMIT GTC, flatten-before-reverse
            └─────────────────────┘
```

### Agents

| Agent | Input (prepared in Python) | Output (Pydantic) |
|---|---|---|
| Technical analyst | 45d Alpaca IEX bars → SMA5/20, RSI14, ATR14, volume trend as booleans/categories | `TechnicalReport` (trend, strength) |
| Fundamentals analyst | EIA crude stocks / Lower-48 gas storage, FRED real yields, USD, breakevens | `FundamentalsReport` |
| Sentiment analyst | 48h Alpaca News for the asset group, deduped, max 15 | `EnhancedSentimentReport` |
| Bull / Bear researchers | Up to 8 RAG chunks (400 chars each) with per-call IDs, authority-tier sorted | Argument + `chunk_ids` |
| Debate judge | Both arguments + Python-computed citation counts | `LiteratureDebateSynthesis` |
| Risk gatekeeper | Proposed trade + live portfolio snapshot | verdict + `risk_score` 1–10 |
| Master orchestrator | All reports | `_LLMDecision` (direction, confidence, % stop/target) |
| Intraday profiler | Daily playbook + RAG | `IntradayProfile` (gating params for the intraday engine) |

Every agent is a `ChatOpenAI.with_structured_output(...)` call with `Literal` enum fields, so malformed output fails at parse time rather than leaking into execution.

### Majority vote

Each symbol is analysed three times with different RAG retrieval settings (chunk count and candidates-per-collection), and the direction is decided by plurality. The highest-confidence run in the winning direction becomes the representative decision. When the result is HOLD, the gate that blocked the trade is recorded (`no_valid_runs`, `whipsaw`, `risk_gatekeeper`, `agent_disagreement`, `majority_vote`) so HOLD behaviour can be audited.

## Risk controls (enforced in Python)

| Control | Value | Where |
|---|---|---|
| Base position size | 5% of equity | `master_orchestrator.py` |
| Size scaling by risk score | REJECTED → 0 · score ≥8 → ×0.25 · ≥6 → ×0.5 · ≥4 → ×0.75 | `risk_gatekeeper.py` |
| Schema hard caps | position ≤10%, stop ≤20%, target ≤30% | Pydantic `le=` |
| Stop distance | 2×ATR%, clamped 2–15% (6% fallback) — the LLM's stop is discarded | `master_orchestrator.py` |
| Exposure cap | 7.5% per asset class per direction, leverage-weighted | `config/risk_limits.yaml`, executor |
| Spread circuit breaker | warn >50 bps, **reject >200 bps** (consolidated SIP NBBO) | `paper_trading_executor.py` |
| Stop orders | STOP_LIMIT, GTC, 0.5% limit offset | executor |
| Short routing | Unshortable ETFs route to an inverse proxy at half size (USO→SCO, UNG→KOLD) | `config/bearish_proxies.yaml` |
| Ratchet stops | +1 ATR → breakeven · +2 ATR → lock 1 ATR · +3 ATR → lock 2 ATR; never moves backward | `ratchet_stops.py` |
| Kill switch | `kill_switch.py engage [--liquidate]` — file flag checked before every run, order and stop | `kill_switch.py` |
| Intraday limits | 0.5% daily loss per symbol; 0–3 trades/day gated by daily confidence | `intraday/swing_executor.py` |

Known gap: there is no portfolio-level daily loss or max-drawdown limit on the daily pipeline yet, and the whipsaw rule (≥2 direction flips in ~4 days ⇒ HOLD) is currently a prompt instruction rather than a Python gate.

## Stack

| Layer | Choice |
|---|---|
| OS | Ubuntu 24.04 LTS |
| Inference | **llama.cpp** serving **Qwen3-30B-A3B-Thinking** (MoE, 3B active) as an OpenAI-compatible endpoint on `127.0.0.1:8081`, GGUF quantised, `seed=42`, `temperature=0.0` |
| Hardware | Single AMD Ryzen AI Max+ 395 mini-PC (Radeon 8060S iGPU, 128 GB unified memory) — ROCm / Vulkan backend |
| Orchestration | LangGraph `StateGraph` + LangChain `ChatOpenAI` + Pydantic structured output |
| Persistence | PostgreSQL 16 + **pgvector** (HNSW cosine index) + `tsvector` GIN index; ChromaDB retained for some researcher paths |
| Retrieval | Hybrid: 0.7 × cosine + 0.3 × BM25 (`ts_rank_cd`), 3× over-fetch, point-in-time filter on `published_date_int` |
| Embeddings | Local sentence-transformers (`nomic-embed-text` configured — see *Known issues*) |
| Broker / data | Alpaca (paper), EIA v2, FRED, Alpaca News |
| Scheduling | systemd user timers (`systemd/`) |
| Alerts | Gmail SMTP daily summary + health check |
| ML signals (non-LLM) | Intraday signal engine — VWAP deviation, EMA momentum, volume spike, mean reversion; <1 ms/bar, never trades against the daily playbook |

## RAG corpus

40 sources, 5,060 chunks, four collections (`methodology`, `papers`, `risk_mgmt`, `commodities`), each tagged with a source class and an **authority tier**:

1. Issuer / SEC / EIA / FRED
2. Peer-reviewed papers (TradingAgents, QuantAgent, TradingGroup-Qwen3, FinRL, López de Prado, Till, Erb & Harvey, Baur & Lucey …)
3. Practitioner books
4. News and commentary

Chunking is paragraph-packed to ≤1,000 chars, with oversized chunks re-split to 800/100 overlap and validated by a "back-half retrieval" test. Each chunk injected into a prompt gets a per-call ID (`METH-001`, `RISK-003`…); the model must cite IDs or `["NONE"]`, and any ID it invents is logged and removed before the judge sees it. This was added after the researchers started hallucinating page numbers and editions. See `rag_chunk_provenance_spec.md`.

## Backtesting

The backtest harness (`backtest/`) is designed around one constraint: a full LLM run over ~200 trading days × 3 symbols costs roughly 25 hours of local compute, so LLM output must be cached and the run must be re-entrant.

- **L1** — Alpaca daily bars cached to JSON
- **L3** — every LLM response cached in SQLite, keyed by `sha256(model | temperature | schema | prompt)`
- `SimPortfolio` replaces the live Alpaca account for the risk gatekeeper
- `run_context.py` routes all writes to `logs/backtest_runs/{run_id}/` — no live tables or playbooks are touched
- Point-in-time: EIA/FRED revision bias is documented (ALFRED recommended), news sentiment is excluded from v1 as a leakage risk, RAG is filtered by publication date

The design doc (`analysis/backtest_schematic_20260427.md`) also states the honest limit: the model's training data extends into 2025, which is a look-ahead problem that cannot be engineered away.

## Results so far

This is the part most trading repos leave out.

**Live paper trading, 16–27 April 2026 (32 decisions, 15 directional):**

| Metric | Value |
|---|---|
| 1-day mean return | −1.03% (median −0.45%) |
| Win rate | 40% (95% CI ≈ 16–68%) |
| Target hit / stop hit | 13% / 33% |
| By symbol | USO −4.00% (17% wins) · UNG +1.50% (60%) · GLD +0.25% (50%) |
| HOLD opportunity cost | 60% of HOLDs missed a profitable trade, +1.65% avg |
| Confidence calibration | 90% of decisions scored 8 or 9 — confidence is **not predictive** |

**Intraday signal engine backtest, 11–22 May 2026, 1-min bars:**

| Symbol | LONG win rate / avg | SHORT win rate / avg |
|---|---|---|
| USO | 51.5% / +0.03% | 39.0% / −0.09% |
| UNG | 47.3% / +0.06% | 41.3% / −0.07% |
| GLD | 0% (n=7) / −0.17% | 63.6% / −0.02% |

Short signals lose money on every symbol. These numbers are why the system is still in paper trading.

### Things that were wrong and got fixed

- **Fake spreads.** USO trades were being rejected at 259–699 bps. The IEX feed was the problem, not the market: on the consolidated tape the same quotes were 4 bps. Fix: switch spread checks to `DELAYED_SIP`. (`docs/uso_spread_investigation.md`)
- **Adaptive cost adjuster feedback loop.** A single 74.8 bps outlier fill inflated the average and cut position sizes 91%. Fix: delete the adjuster, replace with a hard 200 bps reject. Tests enforce that no cost-adjuster function exists.
- **"1 share" positions.** Not a bug — the old 0.2% base size. Fixed by moving to 5% and asserting percent units everywhere (`tests/test_position_sizing.py`).
- **Mock portfolio.** The risk gatekeeper was being fed a fake portfolio. Replaced with a live Alpaca lookup.
- **HOLD carrying a fabricated stop.** HOLD rows stored a LONG-math stop that blocked ratcheting on SHORT positions. Documented, partially fixed. (`docs/hold_behavior_audit.md`)
- **systemd `Persistent=true` + `daemon-reload`** triggered an after-hours catch-up run. Stop the service before reloading.

## Running it

### Prerequisites

- Linux, Python 3.11+, PostgreSQL 16 with `pgvector`
- A running llama.cpp server exposing an OpenAI-compatible API on `127.0.0.1:8081` with a Qwen3-30B-A3B-Thinking GGUF loaded
- Accounts: Alpaca (paper), EIA API key, FRED API key

### Setup

```bash
git clone https://github.com/tattoosbyhannah-cmyk/trading-bot.git
cd trading-bot
python -m venv venv && source venv/bin/activate
pip install alpaca-py langchain-openai langgraph pydantic chromadb pgvector psycopg \
            sentence-transformers pandas requests pyyaml python-dotenv

bash db/install_postgres.sh          # creates DB, extension, schema
cp .env.example .env                 # then fill in the keys below
```

`.env`:

```
ALPACA_API_KEY_ID=
ALPACA_SECRET_KEY=
EIA_API_KEY=
FRED_API_KEY=
DATABASE_URL=postgresql://trading:<password>@localhost:5432/trading
ALERT_EMAIL=
ALERT_EMAIL_APP_PASSWORD=
```

### Run

```bash
python health_check.py                            # traffic-light status: LLM, DB, timers, disk
python majority_vote_orchestrator.py USO          # one symbol, dry run
python majority_vote_orchestrator.py USO --execute
bash daily_pipeline.sh                            # full daily run for all active symbols
python score_outcomes.py                          # score 1/5/30-day outcomes + ratchet stops
python kill_switch.py status | engage | disengage
pytest tests/                                     # contract tests
```

Schedule with systemd user timers (`systemd/*.timer`): pipeline at 09:45 ET (10:45 on EIA release days), intraday 10:00–15:45 ET, scorer 17:15 ET.

## Tests

`tests/` enforces architectural contracts rather than just behaviour:

- **No LLM arithmetic** — fails (xfail) if the model is caught computing
- **No raw prices in schemas** — all Pydantic outputs are percentages
- **DB boundary** — fails if `cur.execute` / `fetchall` appear outside `db/`
- **DB contract** — rows expose `timestamp` not `created_at`, floats not `Decimal`
- **Position sizing** — share math, percent-unit invariants, 6 spread-breaker cases, and "no cost adjuster exists"
- **RAG authority** — gold queries must prefer academic sources

## Repo layout

```
master_orchestrator.py        LangGraph pipeline for one analysis run
majority_vote_orchestrator.py  3-run vote wrapper, HOLD-reason logging, playbook writer
*_analyst.py / rag_*.py        individual agents
risk_gatekeeper.py             risk verdict + Python size scaling
paper_trading_executor.py      Alpaca execution, spread breaker, proxy routing, stops
ratchet_stops.py / kill_switch.py / score_outcomes.py
brokers/   data_sources/       adapter pattern — swap broker or data provider
config/                        models.yaml, instruments.yaml, risk_limits.yaml, rag_sources.yaml
db/                            schema.sql, pgvector store, hybrid query, ChromaDB migration
rag/                           corpus manager, re-chunker, retrieval validator
backtest/                      caching tiers, SimPortfolio, isolated run context
intraday/                      1-min streaming, signal engine, swing executor, profiler
systemd/                       timers and services
tests/                         contract tests
analysis/ docs/                audits and investigations (kept on purpose)
```

## Known issues / roadmap

- [ ] Add a portfolio-level daily loss and drawdown limit in Python
- [ ] Move the whipsaw rule from prompt to code
- [ ] Verify the embedding model and dimension: code configures `nomic-embed-text` (768-d) while the schema column is `vector(384)` and the original ChromaDB ingest used the default MiniLM embedder — likely a mismatch
- [ ] Finish the ChromaDB → pgvector migration (researchers and gatekeeper still read ChromaDB directly)
- [ ] `temperature_override` / `debate_order` vote variations are stored but not applied
- [ ] Run the full LLM backtest over the designed Jul 2025 – Apr 2026 window
- [ ] Add `requirements.txt`, remove `deprecated/`
- [ ] Majority-of-3 was introduced because ROCm/Vulkan GEMM nondeterminism makes `seed=42` insufficient for bitwise-reproducible runs — document the measured variance

## References

- Xiao et al., *TradingAgents: Multi-Agents LLM Financial Trading Framework* (2025) — the base eight-agent hierarchy. This project diverges by trading commodity proxies and illiquid names instead of large caps, running entirely on local models, hard-coding risk limits in Python, and fusing non-LLM signals with the LLM output.
- López de Prado, *Advances in Financial Machine Learning*; Clenow, *Following the Trend*; Till (2008, 2010) on commodity futures returns.
- TauricResearch/TradingAgents, AI4Finance-Foundation/FinGPT (open-source references).

## License

Open source. *(Add a `LICENSE` file — MIT or Apache-2.0 are the usual choices; without one the repo is technically all-rights-reserved regardless of what the README says.)*

---

Built by **Hannah Cowan** — [tattoosbyhannah.com](https://tattoosbyhannah.com) · [LinkedIn](https://linkedin.com/in/hannah-m-cowan)
