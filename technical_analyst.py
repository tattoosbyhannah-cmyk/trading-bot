"""
Technical Analyst — first agent in the multi-agent trading system.
Uses structured output via Pydantic + LangChain's with_structured_output.
"""

import os
from datetime import datetime, timedelta
from typing import TypedDict, Optional, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


from langgraph.graph import StateGraph, END
from agent_logger import log_agent_call

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)


class TechnicalReport(BaseModel):
    symbol: str = Field(description="Ticker symbol analyzed")
    trend: Literal["bullish", "bearish", "neutral"] = Field(description="Overall directional bias")
    strength: Literal["strong", "moderate", "weak"] = Field(description="Conviction level")
    key_observation: str = Field(description="Single most important finding in one sentence")
    rationale: str = Field(description="2-4 sentences citing specific signal names to justify trend and strength")
    latest_close: float | None = Field(None, description="Most recent closing price")
    sma_20: float | None = Field(None, description="20-day simple moving average")
    sma_5: float | None = Field(None, description="5-day simple moving average")
    pct_vs_sma20: float | None = Field(None, description="Percent of latest_close vs sma_20")
    rsi_14: float | None = Field(None, description="14-day RSI")
    atr_14: float | None = Field(None, description="14-day Average True Range (dollars)")
    atr_pct: float | None = Field(None, description="ATR as % of latest close")


class TradingState(TypedDict):
    symbol: str
    calculation_run_id: Optional[str]
    raw_bars: Optional[list]
    indicators: Optional[dict]
    technical_report: Optional[TechnicalReport]


def fetch_bars(state: TradingState) -> TradingState:
    client = StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )
    req = StockBarsRequest(
        symbol_or_symbols=[state["symbol"]],
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=45),
    )
    bars = client.get_stock_bars(req)
    bar_list = [
        {"date": str(b.timestamp.date()), "o": b.open, "h": b.high,
         "l": b.low, "c": b.close, "v": b.volume}
        for b in bars.data.get(state["symbol"], [])
    ]
    return {"raw_bars": bar_list}


def compute_indicators(state: TradingState) -> TradingState:
    bars = state["raw_bars"]
    if len(bars) < 20:
        return {"indicators": {"error": "insufficient data"}}

    closes = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]

    sma_5 = sum(closes[-5:]) / 5
    sma_20 = sum(closes[-20:]) / 20
    latest = closes[-1]
    pct_vs_sma20 = ((latest - sma_20) / sma_20) * 100

    gains = []
    losses = []
    for i in range(-14, 0):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

    vol_recent = sum(volumes[-5:]) / 5
    vol_prior = sum(volumes[-20:-5]) / 15
    vol_change_pct = ((vol_recent - vol_prior) / vol_prior) * 100

    rsi_zone = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    vol_trend = "rising" if vol_change_pct > 10 else "falling" if vol_change_pct < -10 else "flat"

    # ATR (14-day Average True Range)
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    true_ranges = []
    for i in range(-14, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    atr_14 = sum(true_ranges) / len(true_ranges) if true_ranges else 0
    atr_pct = (atr_14 / latest) * 100 if latest > 0 else 0

    return {
        "indicators": {
            "latest_close": round(latest, 2),
            "sma_5": round(sma_5, 2),
            "sma_20": round(sma_20, 2),
            "price_above_sma20": latest > sma_20,
            "price_above_sma5": latest > sma_5,
            "sma5_above_sma20": sma_5 > sma_20,
            "pct_vs_sma20": round(pct_vs_sma20, 2),
            "rsi_14": round(rsi, 1),
            "rsi_zone": rsi_zone,
            "volume_change_pct": round(vol_change_pct, 1),
            "volume_trend": vol_trend,
            "atr_14": round(atr_14, 2),
            "atr_pct": round(atr_pct, 2),
        }
    }


from config.llm_factory import create_llm
llm_deep = create_llm("technical_analyst", output_schema=TechnicalReport,
                       temperature_override=0.3, max_tokens_override=4000)


@log_agent_call(agent_name="technical_analyst", model_lane="fast")
def generate_report(state: TradingState) -> TradingState:
    ind = state["indicators"]
    prompt = f"""You are a technical analyst for commodity ETFs. Analyze {state['symbol']} using these pre-computed signals.

Price position:
- Latest close: ${ind['latest_close']}
- 20-day SMA: ${ind['sma_20']}
- 5-day SMA: ${ind['sma_5']}
- price_above_sma20: {ind['price_above_sma20']} (price is {ind['pct_vs_sma20']:+.2f}% vs 20-SMA)
- price_above_sma5: {ind['price_above_sma5']}
- sma5_above_sma20: {ind['sma5_above_sma20']} (True = short-term momentum stronger than medium-term)

Momentum:
- RSI(14): {ind['rsi_14']} — rsi_zone: {ind['rsi_zone']}

Volume:
- 5-day avg vs prior 15-day avg: {ind['volume_change_pct']:+.1f}% — volume_trend: {ind['volume_trend']}

Rules:
- Use ONLY the booleans and categories above.
- If price_above_sma20 is True, price is ABOVE the SMA20 (bullish); if False, BELOW (bearish).
- Trend must be consistent with the dominant signals.
- Reference signal names (e.g., "price_above_sma20 is True", "rsi_zone oversold") in your rationale.
"""
    report: TechnicalReport = llm_deep.invoke(prompt)
    if not report.symbol:
        report.symbol = state["symbol"]
    # Populate computed price fields so downstream agents have ground truth
    report.latest_close = ind.get("latest_close")
    report.sma_20 = ind.get("sma_20")
    report.sma_5 = ind.get("sma_5")
    report.pct_vs_sma20 = ind.get("pct_vs_sma20")
    report.atr_14 = ind.get("atr_14")
    report.atr_pct = ind.get("atr_pct")
    report.rsi_14 = ind.get("rsi_14")
    return {"technical_report": report}


workflow = StateGraph(TradingState)
workflow.add_node("fetch_bars", fetch_bars)
workflow.add_node("compute_indicators", compute_indicators)
workflow.add_node("generate_report", generate_report)
workflow.set_entry_point("fetch_bars")
workflow.add_edge("fetch_bars", "compute_indicators")
workflow.add_edge("compute_indicators", "generate_report")
workflow.add_edge("generate_report", END)
graph = workflow.compile()


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"
    print(f"\n=== Running Technical Analyst for {symbol} ===\n")
    result = graph.invoke({"symbol": symbol})

    print("Indicators:")
    for k, v in result["indicators"].items():
        print(f"  {k}: {v}")

    report = result["technical_report"]
    print(f"\n--- Structured Report (typed) ---")
    print(f"symbol:          {report.symbol}")
    print(f"trend:           {report.trend}")
    print(f"strength:        {report.strength}")
    print(f"key_observation: {report.key_observation}")
    print(f"rationale:       {report.rationale}")
    print(f"\n--- As dict (for state logging) ---")
    print(report.model_dump())
