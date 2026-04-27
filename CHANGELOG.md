
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
