# DEPRECATED — not used in production. Daily pipeline uses
# majority_vote_orchestrator.py → master_orchestrator.py.
# Kept for reference only. Contains stale patterns (hardcoded LLM, no ATR stops).
"""
Parallel Master Orchestrator — runs agents simultaneously for maximum speed.
Parallelizes both across assets AND within each asset analysis.

DEPRECATED: See majority_vote_orchestrator.py for the production pipeline.
"""

from typing import TypedDict, Optional, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

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


class MasterTradingDecision(BaseModel):
    symbol: str = Field(description="Ticker symbol analyzed")
    timestamp: str = Field(description="Decision timestamp")
    final_decision: str = Field(description="LONG, SHORT, or HOLD with rationale")
    confidence: int = Field(description="Overall confidence 1-10 in the decision", ge=1, le=10)
    position_size: float = Field(description="Recommended position size as % of portfolio", ge=0, le=10)
    entry_price: Optional[float] = Field(description="Recommended entry price if applicable")
    stop_loss: Optional[float] = Field(description="Stop loss price if applicable")
    price_target: Optional[float] = Field(description="Price target if applicable")
    key_thesis: str = Field(description="Core investment thesis in 2-3 sentences")
    risk_factors: List[str] = Field(description="Top 3-5 risk factors to monitor")
    catalyst_timeline: List[str] = Field(description="Upcoming events that could affect the trade")
    agent_consensus: dict = Field(description="Summary of what each agent concluded")


def run_data_analysts_parallel(symbol: str):
    """Phase 1: Run Technical + Fundamentals + Sentiment in parallel."""
    print(f"Phase 1: Running 3 data analysts in parallel for {symbol}...")
    
    results = {}
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all data-gathering jobs simultaneously
        tech_fund_future = executor.submit(dual_graph.invoke, {"symbol": symbol})
        sentiment_future = executor.submit(analyze_enhanced_sentiment, symbol)
        
        # Wait for results
        analyst_result = tech_fund_future.result(timeout=180)  # 3 min timeout
        sentiment_result = sentiment_future.result(timeout=180)
        
        results['technical_report'] = analyst_result["technical_report"]
        results['fundamentals_report'] = analyst_result["fundamentals_report"] 
        results['sentiment_report'] = sentiment_result
    
    return results


def run_research_debate_parallel(symbol: str, data_results: dict):
    """Phase 2: Run Bull + Bear researchers in parallel."""
    print(f"Phase 2: Running Bull vs Bear research in parallel for {symbol}...")
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit bull and bear research simultaneously
        bull_future = executor.submit(generate_rag_bull_case, {
            "symbol": symbol,
            "technical_report": data_results['technical_report'],
            "fundamentals_report": data_results['fundamentals_report']
        })
        
        bear_future = executor.submit(generate_rag_bear_case, {
            "symbol": symbol,
            "technical_report": data_results['technical_report'],
            "fundamentals_report": data_results['fundamentals_report']
        })
        
        # Get results
        bull_result = bull_future.result(timeout=240)  # 4 min timeout
        bear_result = bear_future.result(timeout=240)
        
        # Synthesize the debate
        synthesis_result = synthesize_literature_debate(
            bull_result["rag_bull_argument"],
            bear_result["rag_bear_argument"],
            symbol
        )
    
    return {
        'bull_argument': bull_result["rag_bull_argument"],
        'bear_argument': bear_result["rag_bear_argument"],
        'literature_synthesis': synthesis_result
    }


llm_master = ChatOpenAI(
    base_url="http://127.0.0.1:8081/v1", 
    api_key="not-needed",
    model="qwen3-thinking",
    temperature=0.3,
    max_tokens=8000,
).with_structured_output(MasterTradingDecision)


def run_parallel_trading_analysis(symbol: str) -> MasterTradingDecision:
    """Execute trading analysis with maximum parallelization."""
    print(f"\n🚀 PARALLEL ANALYSIS: {symbol}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    start_time = datetime.now()
    
    # Phase 1: Data analysts in parallel (Technical + Fundamentals + Sentiment)
    data_results = run_data_analysts_parallel(symbol)
    
    # Phase 2: Research debate in parallel (Bull + Bear simultaneously)  
    research_results = run_research_debate_parallel(symbol, data_results)
    
    # Phase 3: Risk validation (sequential - needs research results)
    print(f"Phase 3: Risk management validation for {symbol}...")
    lit_synthesis = research_results['literature_synthesis']
    
    if lit_synthesis.winning_side.lower() == "bull":
        recommended_action = f"Long {symbol} based on literature consensus"
    else:
        recommended_action = f"Short {symbol} based on literature consensus"
    base_position_size = 5.0  # Symmetric — risk gatekeeper Python scaling adjusts
    base_stop_loss = 6.0      # Fallback — ATR-based stop preferred when available
    
    risk_result = evaluate_trade_risk(symbol, recommended_action, base_position_size, base_stop_loss)
    
    # Phase 4: Final decision synthesis
    print(f"Phase 4: Master decision synthesis for {symbol}...")
    
    tech = data_results["technical_report"]
    fund = data_results["fundamentals_report"]
    sentiment = data_results["sentiment_report"]
    bull = research_results["bull_argument"]
    bear = research_results["bear_argument"]
    lit_judge = research_results["literature_synthesis"]
    risk = risk_result
    
    agent_summary = f"""AGENT CONSENSUS SUMMARY FOR {symbol}:

TECHNICAL: {tech.trend}/{tech.strength} - {tech.rationale[:100]}...
FUNDAMENTALS: {fund.bias}/{fund.conviction} - {fund.rationale[:100]}...
SENTIMENT: {sentiment.sentiment}/{sentiment.confidence} - {sentiment.headline_summary[:100]}...
LITERATURE WINNER: {lit_judge.winning_side} ({lit_judge.confidence}/10)
RISK ASSESSMENT: {risk.approval_status} (Risk Score: {risk.risk_score}/10)
"""
    
    prompt = f"""Synthesize trading decision for {symbol} based on parallel agent analysis.

{agent_summary}

Make decisive LONG/SHORT/HOLD recommendation considering:
1. Risk Gatekeeper veto power (REJECTED = position_size must be 0)
2. Agent consensus strength and conflicts
3. Upcoming catalysts from sentiment analysis
4. Literature quality from debate winner"""

    decision = llm_master.invoke(prompt)
    
    if not decision.symbol:
        decision.symbol = symbol
    
    decision.timestamp = datetime.now().isoformat()
    decision.agent_consensus = {
        "technical": f"{tech.trend}/{tech.strength}",
        "fundamentals": f"{fund.bias}/{fund.conviction}",
        "sentiment": f"{sentiment.sentiment}/{sentiment.confidence}",
        "literature_winner": f"{lit_judge.winning_side}/{lit_judge.confidence}",
        "risk_status": risk.approval_status
    }
    
    # Calculate total time
    total_time = (datetime.now() - start_time).total_seconds()
    print(f"✅ {symbol} parallel analysis complete ({total_time:.1f}s)")
    
    # Save to database
    db = TradingPerformanceDB()
    save_master_decision_to_db(decision, db)
    
    return decision


def run_multi_asset_parallel_scan(symbols: List[str], max_workers: int = 8):
    """Scan multiple assets with maximum parallelism."""
    print(f"\n{'🌍 PARALLEL MULTI-ASSET SCAN':=^70}")
    print(f"Assets: {', '.join(symbols)}")
    print(f"Parallel Workers: {max_workers}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    decisions = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all asset analyses simultaneously
        future_to_symbol = {
            executor.submit(run_parallel_trading_analysis, symbol): symbol 
            for symbol in symbols
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                decision = future.result(timeout=600)  # 10 min timeout per asset
                decisions.append(decision)
                
                # Show quick result
                print(f"📊 {symbol}: {decision.final_decision} (Confidence: {decision.confidence}/10)")
                
            except Exception as e:
                print(f"❌ {symbol} failed: {str(e)[:50]}...")
    
    # Display ranked results
    executable = [d for d in decisions if d.position_size > 0]
    if executable:
        ranked = sorted(executable, key=lambda x: x.confidence, reverse=True)
        
        print(f"\n🏆 TOP OPPORTUNITIES")
        print("="*50)
        
        for i, decision in enumerate(ranked[:3], 1):
            print(f"{i}. {decision.symbol} - {decision.final_decision}")
            print(f"   Confidence: {decision.confidence}/10 | Position: {decision.position_size:.1f}%")
            print(f"   Thesis: {decision.key_thesis[:60]}...")
            print()
    else:
        print(f"\n📋 All {len(decisions)} assets resulted in HOLD/REJECTED decisions")
    
    return decisions


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Custom symbol list
        symbols = sys.argv[1].split(',')
    else:
        # Default: commodity ETFs
        symbols = ["USO", "GLD", "UNG"]
    
    decisions = run_multi_asset_parallel_scan(symbols, max_workers=8)