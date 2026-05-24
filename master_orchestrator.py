"""
Master Trading Orchestrator — coordinates all agents into unified trading decisions.
Runs: Data Analysts → RAG Researchers → Literature Judge → Risk Gatekeeper → Final Decision
"""

import json
import uuid as _uuid
from typing import TypedDict, Optional, List
from datetime import datetime, timedelta
from pathlib import Path
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv
import os
load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)

OUTCOMES_LOG = Path(__file__).parent / "logs" / "decision_outcomes.jsonl"

# Lazy-load Alpaca client only when we need a price
def _get_current_price(symbol: str):
    """Fetch latest trade price for symbol. Returns None on failure."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        client = StockHistoricalDataClient(
            os.environ.get('ALPACA_API_KEY_ID'),
            os.environ.get('ALPACA_SECRET_KEY')
        )
        trade = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
        return float(trade[symbol].price)
    except Exception as e:
        print(f"⚠️  Price fetch failed for {symbol}: {e}")
        return None


# Import all your agents
from dual_analyst import dual_graph
from rag_bull_researcher import generate_rag_bull_case, RagBullArgument
from rag_bear_researcher import generate_rag_bear_case, RagBearArgument
from rag_debate_synthesis import synthesize_literature_debate, LiteratureDebateSynthesis
from risk_gatekeeper import evaluate_trade_risk, RiskAssessment
from enhanced_sentiment_analyst import analyze_enhanced_sentiment, EnhancedSentimentReport
from technical_analyst import TechnicalReport
from fundamentals_analyst import FundamentalsReport
from trading_dashboard import TradingPerformanceDB, save_master_decision_to_db
from agent_logger import log_agent_call


class _LLMDecision(BaseModel):
    """Raw LLM output — percentages only, no price arithmetic."""
    symbol: str = Field(description="Ticker symbol analyzed")
    final_decision: str = Field(description="LONG, SHORT, or HOLD with rationale")
    confidence: int = Field(description="Overall confidence 1-10 in the decision", ge=1, le=10)
    stop_loss_pct: float = Field(description="Stop loss distance as % from entry (e.g. 4.5 means 4.5% away). Informed by ATR volatility data.", ge=0, le=20)
    price_target_pct: float = Field(description="Price target distance as % from entry (e.g. 8.0 means 8.0% away)", ge=0, le=30)
    key_thesis: str = Field(description="Core investment thesis in 2-3 sentences")
    risk_factors: List[str] = Field(description="Top 3-5 risk factors to monitor")
    catalyst_timeline: List[str] = Field(description="Upcoming events that could affect the trade")


class MasterTradingDecision(BaseModel):
    """Final decision with Python-computed price levels."""
    symbol: str = Field(description="Ticker symbol analyzed")
    timestamp: str = Field(description="Decision timestamp")
    final_decision: str = Field(description="LONG, SHORT, or HOLD with rationale")
    confidence: int = Field(description="Overall confidence 1-10 in the decision", ge=1, le=10)
    position_size: float = Field(description="Recommended position size as % of portfolio", ge=0, le=10)
    entry_price: Optional[float] = Field(description="Current market price from Alpaca")
    stop_loss: Optional[float] = Field(description="Stop loss price (computed by Python from LLM pct)")
    price_target: Optional[float] = Field(description="Price target (computed by Python from LLM pct)")
    stop_loss_pct: Optional[float] = Field(None, description="Stop distance as % from entry")
    price_target_pct: Optional[float] = Field(None, description="Target distance as % from entry")
    key_thesis: str = Field(description="Core investment thesis in 2-3 sentences")
    risk_factors: List[str] = Field(description="Top 3-5 risk factors to monitor")
    catalyst_timeline: List[str] = Field(description="Upcoming events that could affect the trade")
    agent_consensus: dict = Field(description="Summary of what each agent concluded")


class MasterState(TypedDict):
    symbol: str
    calculation_run_id: Optional[str]     # UUID for audit trail
    # Backtest support
    as_of_date: Optional[str]             # ISO 'YYYY-MM-DD'; cutoff for data fetches
    portfolio_context: Optional[dict]     # pre-built risk-context dict (SimPortfolio output)
                                          # When set, risk_gatekeeper uses this instead of
                                          # querying live Alpaca. See backtest/portfolio_sim.py.
    # Variation parameters (set per vote run for diversity)
    rag_chunks_limit: Optional[int]       # max chunks for prompt (default 8)
    rag_query_n: Optional[int]            # n_results per collection query (default 2)
    debate_order: Optional[str]           # "bull_first" or "bear_first"
    temperature_override: Optional[float] # per-run temperature for deep agents
    # Data layer outputs
    technical_report: Optional[TechnicalReport]
    fundamentals_report: Optional[FundamentalsReport]
    sentiment_report: Optional[EnhancedSentimentReport]
    # Research layer outputs
    bull_argument: Optional[RagBullArgument]
    bear_argument: Optional[RagBearArgument]
    literature_synthesis: Optional[LiteratureDebateSynthesis]
    # Risk layer output
    risk_assessment: Optional[RiskAssessment]
    # Final decision
    master_decision: Optional[MasterTradingDecision]


def run_data_analysts(state: MasterState) -> MasterState:
    """Phase 1: Run Technical + Fundamentals analysts in parallel."""
    print("Phase 1: Running Technical and Fundamentals analysts...")

    # Re-set calc_id for sub-graph (LangGraph may use separate thread context)
    calc_id = state.get("calculation_run_id", "")
    if calc_id:
        from agent_logger import set_calculation_run_id
        set_calculation_run_id(calc_id)

    analyst_result = dual_graph.invoke({
        "symbol": state["symbol"],
        "calculation_run_id": calc_id,
        "as_of_date": state.get("as_of_date"),
    })
    
    return {
        "technical_report": analyst_result["technical_report"],
        "fundamentals_report": analyst_result["fundamentals_report"]
    }


def run_sentiment_analysis(state: MasterState) -> MasterState:
    """Phase 1b: Run Sentiment analysis."""
    print("Phase 1b: Analyzing market sentiment and news...")
    
    sentiment_result = analyze_enhanced_sentiment(state["symbol"])
    
    return {"sentiment_report": sentiment_result}


def run_research_debate(state: MasterState) -> MasterState:
    """Phase 2: Run RAG Bull + Bear researchers, then synthesize."""
    debate_order = state.get("debate_order", "bull_first")
    rag_chunks = state.get("rag_chunks_limit") or 8
    rag_n = state.get("rag_query_n") or 2
    print(f"Phase 2: Running literature-backed Bull vs Bear research "
          f"(order={debate_order}, chunks={rag_chunks}, n={rag_n})...")

    researcher_state = {
        "symbol": state["symbol"],
        "technical_report": state["technical_report"],
        "fundamentals_report": state["fundamentals_report"],
        "rag_chunks_limit": rag_chunks,
        "rag_query_n": rag_n,
    }

    if debate_order == "bear_first":
        # Bear argues first — sees the evidence fresh, bull responds
        bear_result = generate_rag_bear_case(researcher_state)
        bull_result = generate_rag_bull_case(researcher_state)
    else:
        # Default: bull argues first
        bull_result = generate_rag_bull_case(researcher_state)
        bear_result = generate_rag_bear_case(researcher_state)

    # Literature-backed synthesis
    synthesis_result = synthesize_literature_debate(
        bull_result["rag_bull_argument"],
        bear_result["rag_bear_argument"],
        state["symbol"]
    )

    return {
        "bull_argument": bull_result["rag_bull_argument"],
        "bear_argument": bear_result["rag_bear_argument"],
        "literature_synthesis": synthesis_result
    }


def run_risk_validation(state: MasterState) -> MasterState:
    """Phase 3: Run Risk Gatekeeper validation with ATR-based stop."""
    print("Phase 3: Risk management validation...")

    lit_synthesis = state["literature_synthesis"]
    tech = state["technical_report"]

    if lit_synthesis.winning_side.lower() == "bull":
        recommended_action = f"Long {state['symbol']} based on literature consensus"
    else:
        recommended_action = f"Short {state['symbol']} based on literature consensus"

    base_position_size = 5.0  # ~$5,000 per position on $100k account

    # Dynamic stop loss: 2x ATR as % of price (Clenow volatility-based method)
    atr_pct = getattr(tech, "atr_pct", None)
    if atr_pct and atr_pct > 0:
        base_stop_loss = round(atr_pct * 2, 1)  # 2x ATR
        # Clamp to reasonable range: 2% floor, 15% ceiling
        base_stop_loss = max(2.0, min(15.0, base_stop_loss))
    else:
        base_stop_loss = 6.0  # Fallback when ATR unavailable

    # In backtest mode, MasterState["portfolio_context"] is pre-built from
    # SimPortfolio.to_risk_context(...). In live mode it's None and
    # evaluate_trade_risk falls back to get_live_portfolio_context (Alpaca).
    risk_result = evaluate_trade_risk(
        state["symbol"],
        recommended_action,
        base_position_size,
        base_stop_loss,
        portfolio_context=state.get("portfolio_context"),
    )

    return {"risk_assessment": risk_result}


from config.llm_factory import create_llm
llm_master = create_llm("master_orchestrator", output_schema=_LLMDecision,
                         max_tokens_override=8000)


def _compute_price_levels(llm_out: _LLMDecision, entry_price: float,
                          risk_position_pct: float) -> MasterTradingDecision:
    """Convert LLM percentage outputs to actual price levels using Python math."""
    direction = llm_out.final_decision.strip().split()[0].upper()

    # abs() belt-and-suspenders: schema already enforces ge=0, but if a negative
    # ever slips through (LLM bypass, future schema drift), treat as magnitude so
    # the SHORT/LONG branch below still produces the geometrically correct level.
    sl_pct = abs(llm_out.stop_loss_pct)
    tp_pct = abs(llm_out.price_target_pct)

    # HOLD has no actual side to put a stop on. Fabricating a LONG-math stop
    # for a HOLD record is not just meaningless — it actively confuses
    # ratchet_stops.py which uses the most-recent decision's stop_loss as the
    # comparison basis (see docs/hold_behavior_audit.md). Return None instead;
    # the ratchet has a fallback that walks back to the prior LONG/SHORT.
    if direction == "HOLD":
        stop_loss = None
        price_target = None
    elif entry_price and entry_price > 0:
        if direction == "SHORT":
            stop_loss = round(entry_price * (1 + sl_pct / 100), 2)
            price_target = round(entry_price * (1 - tp_pct / 100), 2)
        else:  # LONG
            stop_loss = round(entry_price * (1 - sl_pct / 100), 2)
            price_target = round(entry_price * (1 + tp_pct / 100), 2)
    else:
        stop_loss = None
        price_target = None

    return MasterTradingDecision(
        symbol=llm_out.symbol,
        timestamp="",
        final_decision=llm_out.final_decision,
        confidence=llm_out.confidence,
        position_size=risk_position_pct,
        entry_price=entry_price,
        stop_loss=stop_loss,
        price_target=price_target,
        stop_loss_pct=sl_pct,
        price_target_pct=tp_pct,
        key_thesis=llm_out.key_thesis,
        risk_factors=llm_out.risk_factors,
        catalyst_timeline=llm_out.catalyst_timeline,
        agent_consensus={},
    )


def _load_recent_decisions(symbol: str, days: int = 3) -> list:
    """Load recent decisions — SQL first, JSONL fallback."""
    from db.queries import load_recent_decisions
    return load_recent_decisions(symbol, days)


def _get_yesterday_context(symbol: str, current_price: float) -> str:
    """Build a prompt section describing yesterday's decision and current status."""
    recent = _load_recent_decisions(symbol, days=2)
    if not recent:
        return ""
    # Get the most recent prior decision (not today's)
    today = datetime.now().strftime("%Y-%m-%d")
    prior = [r for r in recent if r["timestamp"][:10] != today]
    if not prior:
        return ""
    last = prior[-1]
    direction = last.get("decision", "?")
    entry = last.get("entry_price", 0) or 0
    if entry > 0 and current_price:
        change_pct = (current_price - entry) / entry * 100
        change_str = f"currently {'up' if change_pct > 0 else 'down'} {abs(change_pct):.1f}%"
    else:
        change_str = "current status unknown"

    status = ""
    if last.get("hit_stop"):
        status = "STOPPED OUT"
    elif last.get("hit_target"):
        status = "HIT TARGET"
    else:
        status = "still open / not yet scored"

    return (
        f"\nYESTERDAY'S DECISION: {symbol} was {direction} at ${entry:.2f}, "
        f"{change_str}. Status: {status}.\n"
    )


def _detect_whipsaw(symbol: str) -> str:
    """Check if the system has flipped direction 2+ times in 3 trading days."""
    recent = _load_recent_decisions(symbol, days=4)
    if len(recent) < 2:
        return ""
    # Extract unique daily directions (one per day, latest per day)
    by_date = {}
    for r in recent:
        d = r["timestamp"][:10]
        direction = r.get("decision", "HOLD")
        if direction in ("LONG", "SHORT"):
            by_date[d] = direction
    directions = list(by_date.values())
    if len(directions) < 2:
        return ""
    # Count direction flips
    flips = sum(1 for i in range(1, len(directions)) if directions[i] != directions[i - 1])
    if flips >= 2:
        return (
            f"\n⚠️ WHIPSAW DETECTED: {symbol} has flipped direction {flips} times "
            f"in the last {len(directions)} trading days ({' → '.join(directions)}). "
            f"Direction instability detected — HOLD until clearer signal emerges.\n"
        )
    return ""


@log_agent_call(agent_name="master_orchestrator", model_lane="deep")
def make_final_decision(state: MasterState) -> MasterState:
    """Phase 4: Master decision synthesis considering all agent inputs."""
    print("Phase 4: Master orchestrator making final trading decision...")
    
    tech = state["technical_report"]
    fund = state["fundamentals_report"]
    sentiment = state["sentiment_report"]
    bull = state["bull_argument"]
    bear = state["bear_argument"]
    lit_judge = state["literature_synthesis"]
    risk = state["risk_assessment"]
    symbol = state["symbol"]
    
    # Fetch current market price for grounding stop_loss/price_target
    current_price = _get_current_price(symbol)
    price_context = (
        f"CURRENT MARKET PRICE: ${current_price:.2f}\n"
        f"OUTPUT RULES: You output PERCENTAGES for stop_loss_pct and price_target_pct — "
        f"NOT dollar prices. Code will compute actual price levels from your percentages. "
        f"Do NOT attempt price arithmetic.\n"
        f"The risk gatekeeper's actual approval_status is in the risk_assessment "
        f"input — read that directly, do not infer it from researcher outputs.\n"
        if current_price else
        "CURRENT MARKET PRICE: UNAVAILABLE (price fetch failed). "
        "If outputting entry_price/stop_loss/price_target, use null instead of guessing.\n"
    )

    # Format citations from bull and bear researchers
    bull_cites = "\n  - ".join(
        f'[{", ".join(c.chunk_ids)}] {c.author}: {c.claim}' for c in bull.literature_citations
    ) if bull.literature_citations else "(none)"
    bear_cites = "\n  - ".join(
        f'[{", ".join(c.chunk_ids)}] {c.author}: {c.claim}' for c in bear.literature_citations
    ) if bear.literature_citations else "(none)"

    # ATR context
    atr_val = getattr(tech, "atr_14", None)
    atr_pct = getattr(tech, "atr_pct", None)
    atr_context = ""
    if atr_val and atr_pct:
        atr_context = (
            f"\nVOLATILITY (ATR): ${atr_val:.2f} ({atr_pct:.2f}% of price) — "
            f"2x ATR stop = {atr_pct * 2:.1f}% from entry\n"
        )

    # Yesterday's decision + whipsaw detection
    yesterday_context = _get_yesterday_context(symbol, current_price) if current_price else ""
    whipsaw_warning = _detect_whipsaw(symbol)

    # Prepare comprehensive context
    agent_summary = f"""AGENT CONSENSUS SUMMARY:

{price_context}{atr_context}{yesterday_context}{whipsaw_warning}
TECHNICAL ANALYSIS: {tech.trend}/{tech.strength}
- {tech.rationale}

FUNDAMENTALS: {fund.bias}/{fund.conviction}
- {fund.rationale}

SENTIMENT: {sentiment.sentiment}/{sentiment.confidence}/10
- {sentiment.headline_summary}
- News Volume: {sentiment.news_volume} articles
- Key Catalysts: {', '.join(sentiment.catalyst_alerts[:3])}

LITERATURE DEBATE WINNER: {lit_judge.winning_side} (Confidence: {lit_judge.confidence}/10)
- Thesis: {lit_judge.key_thesis}
- Trade Rec: {lit_judge.trade_recommendation}

RISK ASSESSMENT: {risk.approval_status}
- Market Conditions Risk Score: {risk.risk_score}/10
- Approved Stop: {risk.stop_loss_pct}%
- Rationale: {risk.rationale[:200]}...

BULL CASE: {bull.strength}/{bull.conviction}/10
- {bull.thesis}
- Literature citations:
  - {bull_cites}

BEAR CASE: {bear.strength}/{bear.conviction}/10
- {bear.thesis}
- Literature citations:
  - {bear_cites}
"""

    prompt = f"""You are the Master Trading Orchestrator making the final trading decision for {symbol}.

{agent_summary}

Your job is to synthesize ALL agent inputs into a single, actionable trading decision.

DECISION FRAMEWORK:
1. **Agent Alignment**: How well do the agents agree? Conflicting signals reduce confidence.
2. **Risk Override**: Risk Gatekeeper evaluates MARKET CONDITIONS. If REJECTED, do not trade (extreme conditions). High risk_score means the market is volatile/dangerous, not that the thesis is wrong.
3. **Catalyst Timing**: Consider upcoming events from sentiment analysis.
4. **Literature Quality**: Weight the debate winner's argument strength.
5. **Multi-Timeframe**: Balance technical (short-term) vs fundamental (medium-term) signals.

STOP LOSS GUIDANCE:
Output stop_loss_pct as a PERCENTAGE distance from entry (e.g. 4.5 means 4.5% away).
Do NOT compute dollar prices — code will handle that.
Use the ATR volatility data above: Clenow recommends 2x ATR for trend-following stops.
Tighter stops get clipped by normal volatility; wider stops risk excessive loss per trade.
Your stop_loss_pct should reflect THIS asset's actual price behavior.

PRICE TARGET GUIDANCE:
Output price_target_pct as a positive PERCENTAGE distance from entry (e.g. 8.0 means
the target is 8% away from entry). ALWAYS POSITIVE — do NOT use negative values for SHORT
trades. The code derives direction from your LONG/SHORT decision: for LONG the target is
8% above entry, for SHORT the target is 8% below. You provide magnitude only.

POSITION CONTINUITY:
Consider yesterday's decision if provided above. If yesterday's thesis is still valid and
the position wasn't stopped out, maintaining the same direction is often correct.
Flipping direction daily without a clear catalyst change is a sign of low conviction —
prefer HOLD when signals are genuinely mixed rather than alternating LONG/SHORT.
If a WHIPSAW WARNING is shown above, you MUST output HOLD.

DECISION OPTIONS:
- LONG: Enter long position (sizing handled by code based on market risk)
- SHORT: Enter short position (sizing handled by code)
- HOLD: No position change (if existing) or no entry (if new)

Consider that this is a real trading decision with portfolio impact. Be decisive but conservative.
Include current market price context and specific entry/exit levels in your recommendation."""

    llm_out = llm_master.invoke(prompt)

    if not llm_out.symbol:
        llm_out.symbol = symbol

    # Position sizing already handled by risk gatekeeper (risk_score → scaling)
    # Just pass through the gatekeeper's final number
    if risk.approval_status == "REJECTED":
        final_position_size = 0.0
    else:
        final_position_size = risk.position_size_pct

    # Bug 6 fix: Override LLM's stop_loss_pct with deterministic ATR-based stop
    # risk.stop_loss_pct was computed as round(atr_pct * 2, 1) clamped to 2-15%
    # The LLM should not control stop distance — only Python does math
    llm_out.stop_loss_pct = risk.stop_loss_pct

    # Python computes price levels from percentages
    # entry_price ALWAYS from Alpaca, never from LLM
    decision = _compute_price_levels(llm_out, current_price, final_position_size)
    decision.timestamp = datetime.now().isoformat()

    # Build agent consensus summary
    calc_id = state.get("calculation_run_id", "")
    decision.agent_consensus = {
        "calculation_run_id": calc_id,
        "technical": f"{tech.trend}/{tech.strength}",
        "fundamentals": f"{fund.bias}/{fund.conviction}",
        "sentiment": f"{sentiment.sentiment}/{sentiment.confidence}",
        "literature_winner": f"{lit_judge.winning_side}/{lit_judge.confidence}",
        "risk_status": risk.approval_status,
        "bull_citations": [c.model_dump() for c in bull.literature_citations] if bull.literature_citations else [],
        "bear_citations": [c.model_dump() for c in bear.literature_citations] if bear.literature_citations else [],
        "current_market_price": current_price,
        "atr_pct": getattr(tech, "atr_pct", None),
    }

    return {"master_decision": decision}


# Build the master workflow
workflow = StateGraph(MasterState)

# Phase 1: Data gathering (parallel)
workflow.add_node("data_analysts", run_data_analysts)
workflow.add_node("sentiment_analysis", run_sentiment_analysis)

# Phase 2: Research and debate
workflow.add_node("research_debate", run_research_debate)

# Phase 3: Risk validation
workflow.add_node("risk_validation", run_risk_validation)

# Phase 4: Final decision
workflow.add_node("final_decision", make_final_decision)

# Workflow edges
workflow.set_entry_point("data_analysts")
workflow.add_edge("data_analysts", "sentiment_analysis")
workflow.add_edge("sentiment_analysis", "research_debate")
workflow.add_edge("research_debate", "risk_validation")
workflow.add_edge("risk_validation", "final_decision")
workflow.add_edge("final_decision", END)

master_graph = workflow.compile()


def run_complete_trading_analysis(symbol: str, variation: dict = None):
    """Execute complete trading analysis pipeline.

    Args:
        symbol: Ticker to analyze
        variation: Optional dict with keys: rag_chunks_limit, rag_query_n,
                   debate_order, temperature_override
    """
    print(f"\n{'='*70}")
    print(f"MASTER TRADING ORCHESTRATOR - {symbol}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    calc_id = str(_uuid.uuid4())
    state = {"symbol": symbol, "calculation_run_id": calc_id}
    if variation:
        state.update(variation)

    # Set thread-local so all agents in this pipeline run share the calc_id
    from agent_logger import set_calculation_run_id
    set_calculation_run_id(calc_id)

    result = master_graph.invoke(state)
    
    decision = result["master_decision"]
    
    print(f"\n🎯 FINAL TRADING DECISION: {decision.final_decision}")
    print(f"Confidence: {decision.confidence}/10")
    print(f"Position Size: {decision.position_size}% of portfolio")
    if decision.stop_loss:
        print(f"Stop Loss: ${decision.stop_loss:.2f}")
    if decision.price_target:
        print(f"Price Target: ${decision.price_target:.2f}")
    
    print(f"\n📊 INVESTMENT THESIS:")
    print(f"{decision.key_thesis}")
    
    print(f"\n⚠️  KEY RISKS TO MONITOR:")
    for i, risk in enumerate(decision.risk_factors, 1):
        print(f"  {i}. {risk}")
    
    print(f"\n📅 CATALYST TIMELINE:")
    for i, catalyst in enumerate(decision.catalyst_timeline, 1):
        print(f"  {i}. {catalyst}")
    
    print(f"\n🤖 AGENT CONSENSUS:")
    consensus = decision.agent_consensus
    for agent, conclusion in consensus.items():
        print(f"  {agent}: {conclusion}")
    
    # Save decision to performance database
    print(f"\n💾 Saving decision to performance database...")
    db = TradingPerformanceDB()
    save_master_decision_to_db(decision, db)
    
    return decision


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"
    
    final_decision = run_complete_trading_analysis(symbol)