
## 2026-04-27 — Pre-trade cost adjuster removed (Option D)

Removed pre-trade cost adjuster; spread > 200 bps now rejects trade entirely. Cost tracking moves to post-hoc analysis of fill_records.jsonl.

## 2026-04-27 — Daily pipeline timer shifted 9:35 ET → 9:45 ET

Shifted daily pipeline from 9:35 ET to 9:45 ET to allow IEX quotes to settle post-open. Wed/Thu EIA-day 10:45 ET runs unchanged. intraday-swing.timer unchanged (existing 25-min ExecStartPre handles ordering).

Note: daemon-reload triggered a Persistent=true catch-up run at 19:51 PDT (after-hours, no execute). Run was stopped and DB rows cleaned. Future timer changes should `systemctl --user stop` the service before daemon-reload to prevent catch-up.

Catch-up cleanup completed: 32 lines filtered from agent_calls.jsonl (two GLD calc_run_ids). daily_GLD_20260427.log deleted (morning log was clobbered by catch-up's bash redirect; partial after-hours run was incomplete). daily_pipeline.log preserved as audit trail. Zero Postgres rows affected — orchestrator was killed before vote aggregation reached the DB write step.

## 2026-04-12 — Phase 2 step 1: Technical Analyst agent working

First end-to-end multi-agent pipeline node. LangGraph + Alpaca live data + deep-lane LLM with structured output via Pydantic.

### Architecture pattern locked in
- Pydantic BaseModel with Literal types for enum fields
- 3-node LangGraph: fetch (Alpaca) → compute (pure Python pre-judged booleans) → report (LLM with_structured_output)
- All directional judgments (price vs SMA, RSI zone, volume trend) computed in Python, not by the LLM
- LLM receives only booleans/categories and produces typed TechnicalReport object

### Bug caught and fixed
- Fast-lane (Qwen3-Coder) produced arithmetic-inverted rationales when fed raw numbers ("price below $123.33" when it was $124.82). Root cause: coder model unreliable at numeric comparisons.
- Fix: move to deep-lane (Qwen3-Thinking) + pre-judge all comparisons in Python.
- Lesson: analysts consume pre-judged signals, not raw data. Documenting as reusable pattern.

### Validated on 3 symbols
USO bullish/moderate, UNG bearish/weak, GLD bullish/moderate. All reports internally consistent, no contradictions between trend and rationale.

### Next session
- Fundamentals Analyst (same pattern, EIA crude inventory data)
- Parallel execution of Technical + Fundamentals in single LangGraph

## 2026-04-12 — Phase 2 steps 2-3: Dual analyst parallel execution
- Fundamentals Analyst: EIA crude inventory integration, 52-week range analysis
- Dual orchestrator: parallel Technical + Fundamentals with signal divergence detection
- Validated: bullish technical vs bearish fundamentals on USO (classic commodity tension)
