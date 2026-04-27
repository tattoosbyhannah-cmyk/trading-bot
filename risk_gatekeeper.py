"""
Risk Gatekeeper — validates trade recommendations against literature-backed risk management rules.
Final filter before trade execution, applying documented risk control principles.

PATCHES (2026-04-14):
- Replaced hardcoded mock portfolio (which falsely claimed 15% commodity exposure and
  that GLD was already held) with live Alpaca paper account lookup.
- Falls back to empty-portfolio context if Alpaca fetch fails (fail-safe: rejects trade
  rather than approving on bad data is still the goal, but we no longer LIE about the portfolio).
- temperature 0.2 -> 0.0 (greedy sampling) for reproducible risk scores.
- seed=42 pinned for llama.cpp RNG determinism.
"""

import os
import json
from pathlib import Path
from typing import TypedDict, Optional, Literal, List

from dotenv import load_dotenv
from pydantic import BaseModel, Field
import chromadb
from agent_logger import log_agent_call

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)

# Initialize ChromaDB for risk management literature
client = chromadb.PersistentClient(path="./chromadb-data")
risk_mgmt_collection = client.get_collection("risk_mgmt")
methodology_collection = client.get_collection("methodology")


# --------------------------------------------------------------------------
# Alpaca live portfolio context
# --------------------------------------------------------------------------

# Lazy-import Alpaca so this file still imports cleanly if Alpaca SDK is missing.
try:
    from alpaca.trading.client import TradingClient
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False

_alpaca_client = None


def _get_alpaca_client():
    """Lazy singleton for the Alpaca TradingClient."""
    global _alpaca_client
    if _alpaca_client is None and _ALPACA_AVAILABLE:
        api_key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if api_key and secret_key:
            _alpaca_client = TradingClient(
                api_key=api_key,
                secret_key=secret_key,
                paper=True,
            )
    return _alpaca_client


# Commodity-family symbols for exposure bucketing. Extend as new asset-class
# analyzers come online. Kept in sync with fundamentals_analyst OIL_SYMBOLS
# and expanded to the full commodity-adjacent ETF universe.
COMMODITY_SYMBOLS = {
    # Oil
    "USO", "UCO", "SCO", "USL", "DBO", "BNO", "OIL",
    # Natural gas
    "UNG", "BOIL", "KOLD",
    # Precious metals
    "GLD", "SLV", "GDX", "GDXJ", "SIL", "PPLT", "PALL", "IAU",
    # Agriculture
    "DBA", "CORN", "WEAT", "SOYB", "CANE",
    # Base metals / broad commodity
    "DBB", "CPER", "DBC", "GSG", "PDBC",
}


def get_live_portfolio_context(symbol: str) -> dict:
    """Fetch real Alpaca paper portfolio state for the risk gatekeeper.

    Returns a dict matching the shape the LLM prompt expects. On any failure,
    returns an empty-portfolio fallback (honest about the failure, so the LLM
    does not make concentration decisions based on fabricated holdings).
    """
    client = _get_alpaca_client()
    if client is None:
        print("⚠️  Alpaca client unavailable (SDK missing or credentials not set); using empty-portfolio fallback")
        return _empty_portfolio_context(symbol, reason="alpaca_unavailable")

    try:
        account = client.get_account()
        positions = client.get_all_positions()

        total_value = float(account.portfolio_value)
        cash = float(account.cash)

        equity_exposure = 0.0
        commodity_exposure = 0.0
        open_symbols = []
        symbol_current_position = 0.0

        for pos in positions:
            market_value = abs(float(pos.market_value))
            pct = market_value / total_value if total_value > 0 else 0.0
            open_symbols.append(pos.symbol)

            if pos.symbol.upper() in COMMODITY_SYMBOLS:
                commodity_exposure += pct
            else:
                equity_exposure += pct

            if pos.symbol.upper() == symbol.upper():
                symbol_current_position = pct

        return {
            "total_portfolio_value": round(total_value, 2),
            "current_equity_exposure": round(equity_exposure, 4),
            "current_commodity_exposure": round(commodity_exposure, 4),
            "current_cash": round(cash / total_value, 4) if total_value > 0 else 1.0,
            "ytd_drawdown": 0.0,  # TODO: compute from account.portfolio_history
            "open_positions": open_symbols,
            f"{symbol.lower()}_current_position": round(symbol_current_position, 4),
            "data_source": "alpaca_paper_live",
        }
    except Exception as e:
        print(f"⚠️  Alpaca portfolio fetch failed ({type(e).__name__}: {str(e)[:80]}); using empty-portfolio fallback")
        return _empty_portfolio_context(symbol, reason=f"alpaca_error_{type(e).__name__}")


def _empty_portfolio_context(symbol: str, reason: str) -> dict:
    """Fallback portfolio context when Alpaca is unreachable.

    Assumes a clean slate (no holdings) rather than the old hardcoded mock.
    Marks data_source clearly so the LLM prompt and downstream logs know this
    is not live data.
    """
    return {
        "total_portfolio_value": 100000,
        "current_equity_exposure": 0.0,
        "current_commodity_exposure": 0.0,
        "current_cash": 1.0,
        "ytd_drawdown": 0.0,
        "open_positions": [],
        f"{symbol.lower()}_current_position": 0.0,
        "data_source": f"fallback_empty:{reason}",
    }


# --------------------------------------------------------------------------
# Risk assessment schema + LLM
# --------------------------------------------------------------------------

class RiskAssessment(BaseModel):
    symbol: str = Field(description="Ticker symbol being evaluated")
    approval_status: Literal["APPROVED", "REJECTED", "MODIFIED"] = Field(description="Risk gatekeeper decision")
    risk_score: int = Field(description="Overall risk score 1-10 (10=highest risk)", ge=1, le=10)
    position_size_pct: float = Field(description="Approved position size as % of portfolio", ge=0, le=10)
    stop_loss_pct: float = Field(description="Approved stop-loss percentage", ge=0, le=20)
    risk_factors: List[str] = Field(description="Identified risk factors and mitigations")
    literature_guidance: List[str] = Field(description="Risk management principles from trading literature")
    monitoring_alerts: List[str] = Field(description="Key metrics to monitor post-entry")
    rationale: str = Field(description="Detailed reasoning for approval/rejection/modification")


def retrieve_risk_guidance(query: str, n_results: int = 4) -> List[str]:
    """Retrieve risk management guidance from literature."""
    results = []

    # Search risk management collection
    risk_results = risk_mgmt_collection.query(
        query_texts=[query],
        n_results=n_results
    )

    # Search methodology for risk-related content
    method_results = methodology_collection.query(
        query_texts=[query + " risk position sizing drawdown"],
        n_results=2
    )

    # Combine with source attribution
    for doc, metadata in zip(risk_results['documents'][0], risk_results['metadatas'][0]):
        source = f"{metadata['author']} - {metadata['title']}"
        results.append(f"[{source}]: {doc[:400]}...")

    for doc, metadata in zip(method_results['documents'][0], method_results['metadatas'][0]):
        source = f"{metadata['author']} - {metadata['title']}"
        results.append(f"[{source}]: {doc[:400]}...")

    return results


from config.llm_factory import create_llm
llm_deep = create_llm("risk_gatekeeper", output_schema=RiskAssessment, max_tokens_override=6000)


@log_agent_call(agent_name="risk_gatekeeper", model_lane="fast")
def evaluate_trade_risk(
    symbol: str,
    recommended_action: str,
    position_size_pct: float,
    stop_loss_pct: float,
    portfolio_context: Optional[dict] = None,
) -> RiskAssessment:
    """Evaluate trade recommendation against risk management literature."""

    # Retrieve relevant risk management guidance
    risk_queries = [
        f"volatility regime {symbol} commodity market conditions liquidity",
        "market microstructure bid-ask spread execution risk slippage",
        "correlation risk commodity exposure portfolio drawdown",
        "catalyst timing event risk trading halt circuit breaker",
    ]

    risk_guidance = []
    for query in risk_queries:
        risk_guidance.extend(retrieve_risk_guidance(query, 2))

    risk_context = "\n\n".join(risk_guidance[:8])  # Top 8 risk management insights

    # Live portfolio lookup (replaces old hardcoded mock)
    if portfolio_context is None:
        portfolio_context = get_live_portfolio_context(symbol)

    prompt = f"""You are a market conditions gatekeeper. You evaluate EXECUTION RISK ONLY.

TRADE UNDER EVALUATION:
Symbol: {symbol}
Action: {recommended_action}
Stop Loss: {stop_loss_pct}% from entry (ATR-based)

PORTFOLIO CONTEXT:
{json.dumps(portfolio_context, indent=2)}

RISK MANAGEMENT LITERATURE:
{risk_context}

YOUR JOB: Is this a good time to enter {symbol} based on market conditions?
You do NOT evaluate position sizing — that is handled by code after your assessment.
You do NOT evaluate directional thesis — that was decided by upstream agents.

EVALUATE THESE MARKET CONDITION FACTORS:
1. **Volatility Regime**: Is current volatility normal, elevated, or extreme for this asset?
2. **Liquidity**: Is the bid-ask spread normal? Any signs of thin books or wide spreads?
3. **Catalyst Proximity**: Are there imminent news events (EIA, FOMC, CPI) that could cause gaps?
4. **Correlation Risk**: Does the portfolio already have correlated exposure to this sector?
5. **Market Microstructure**: Any halt risk, circuit breaker proximity, or flash crash conditions?

CALIBRATION ANCHORS for risk_score (1-10) — market conditions only:
- 1-3: Calm/favorable — normal volatility, tight spreads, no imminent catalysts, low correlation
- 4-6: Normal conditions — typical volatility, standard spreads, routine market environment
- 7-8: Elevated risk — high volatility, wide spreads, imminent catalyst (e.g., EIA in 30 min),
  significant existing correlation in portfolio
- 9-10: Extreme conditions — reserve for: halt risk, circuit breaker proximity, flash crash
  conditions, exchange-level issues, market-wide panic. NOT for "the position seems big."

DECISION CRITERIA:
- APPROVE: Market conditions are acceptable for entry
- MODIFY: Conditions are marginal — code will reduce size based on your risk_score
- REJECT: Conditions are dangerous — halt risk, extreme volatility, or structural market issue

Do NOT score 7+ just because a position seems large. You don't know the position size and it's
not your job. Score based on what the MARKET is doing, not what the PORTFOLIO is doing.
Cite specific market condition observations from the data and literature provided."""

    assessment = llm_deep.invoke(prompt)
    if not assessment.symbol:
        assessment.symbol = symbol

    # Fix 2: Python overrides LLM's position_size_pct based on risk_score
    # LLM decides APPROVE/MODIFY/REJECT + risk_score (judgment)
    # Python computes final sizing (deterministic)
    if assessment.approval_status == "REJECTED":
        assessment.position_size_pct = 0.0
    elif assessment.risk_score >= 8:
        assessment.position_size_pct = position_size_pct * 0.25
    elif assessment.risk_score >= 6:
        assessment.position_size_pct = position_size_pct * 0.5
    elif assessment.risk_score >= 4:
        assessment.position_size_pct = position_size_pct * 0.75
    else:
        assessment.position_size_pct = position_size_pct

    # Pin stop_loss_pct to the input value — LLM doesn't change this
    assessment.stop_loss_pct = stop_loss_pct

    return assessment


class RiskGatekeeperState(TypedDict):
    symbol: str
    trade_recommendation: str
    position_size_pct: float
    stop_loss_pct: float
    risk_assessment: Optional[RiskAssessment]


def run_risk_gatekeeper(state: RiskGatekeeperState) -> RiskGatekeeperState:
    """Run risk assessment on trade recommendation."""
    assessment = evaluate_trade_risk(
        state["symbol"],
        state["trade_recommendation"],
        state["position_size_pct"],
        state["stop_loss_pct"]
    )
    return {"risk_assessment": assessment}


if __name__ == "__main__":
    # Test with the USO recommendation from the literature debate
    test_recommendation = {
        "symbol": "USO",
        "trade_recommendation": "Long USO at current price with momentum continuation strategy",
        "position_size_pct": 2.0,  # Max 2% portfolio risk from debate output
        "stop_loss_pct": 8.0       # 8% stop-loss from debate output
    }

    print("=== RISK GATEKEEPER EVALUATION ===\n")
    print(f"Evaluating: {test_recommendation['trade_recommendation']}")
    print(f"Position Size: {test_recommendation['position_size_pct']}% of portfolio")
    print(f"Stop Loss: {test_recommendation['stop_loss_pct']}%")

    # Preview the portfolio context the gatekeeper will see
    print("\nPortfolio context that will be passed to gatekeeper:")
    ctx = get_live_portfolio_context(test_recommendation["symbol"])
    print(json.dumps(ctx, indent=2))

    result = run_risk_gatekeeper(test_recommendation)
    assessment = result["risk_assessment"]

    print(f"\n{'='*50}")
    print(f"RISK ASSESSMENT RESULT: {assessment.approval_status}")
    print(f"Risk Score: {assessment.risk_score}/10")
    print(f"Approved Position Size: {assessment.position_size_pct}%")
    print(f"Approved Stop Loss: {assessment.stop_loss_pct}%")

    print(f"\nRisk Factors:")
    for i, factor in enumerate(assessment.risk_factors, 1):
        print(f"  {i}. {factor}")

    print(f"\nLiterature Guidance:")
    for i, guidance in enumerate(assessment.literature_guidance, 1):
        print(f"  {i}. {guidance}")

    print(f"\nMonitoring Alerts:")
    for i, alert in enumerate(assessment.monitoring_alerts, 1):
        print(f"  {i}. {alert}")

    print(f"\nRationale: {assessment.rationale}")

