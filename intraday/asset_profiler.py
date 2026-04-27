"""
Intraday Asset Profiler — RAG-informed daily assessment of whether and how
each symbol should be traded intraday.

Runs once per symbol during the daily pipeline, AFTER the majority vote.
Uses Qwen3-8B (fast lane) + RAG chunks about intraday trading characteristics.

The LLM provides judgment (should we trade? how cautious?).
Python applies the resulting thresholds deterministically.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

import chromadb

import os
from dotenv import load_dotenv

import sys
_BOTDIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BOTDIR))
load_dotenv(_BOTDIR / ".env")

from agent_logger import log_agent_call

# ChromaDB for RAG
_client = chromadb.PersistentClient(path=str(_BOTDIR / "chromadb-data"))
_methodology = _client.get_or_create_collection("methodology")
_risk_mgmt = _client.get_or_create_collection("risk_mgmt")
_commodities = _client.get_or_create_collection("commodities")

TRADE_LOG = _BOTDIR / "logs" / "intraday_trades.jsonl"


# ── Schema ────────────────────────────────────────────────────────────────────

class IntradayProfile(BaseModel):
    allow_intraday: bool = Field(description="Should this asset be traded intraday today?")
    reason: str = Field(description="Why or why not, citing specific data points")
    min_signal_strength: float = Field(description="Minimum composite signal strength 0.4-0.8")
    volume_spike_threshold: float = Field(description="Volume spike multiplier required 2.0-5.0x avg")
    max_trades: int = Field(description="Maximum intraday trades allowed today 0-5")
    stop_atr_multiple: float = Field(description="Stop loss as multiple of 1-min ATR 1.5-3.0")
    target_atr_multiple: float = Field(description="Take profit as multiple of 1-min ATR 2.0-5.0")
    preferred_entry_window: str = Field(description="Best trading window today, e.g. '10:00-14:00'")
    catalyst_times: List[str] = Field(description="Known events today that affect this asset")


# ── RAG retrieval ─────────────────────────────────────────────────────────────

def _retrieve_intraday_guidance(symbol: str, asset_class: str) -> str:
    """Pull RAG chunks about intraday trading for this asset class."""
    queries = [
        f"intraday trading {asset_class} volume liquidity 1-minute",
        f"{asset_class} scalping swing day trading characteristics",
        f"position sizing intraday risk {asset_class} stop loss ATR",
    ]
    chunks = []
    for q in queries:
        for coll in [_methodology, _risk_mgmt, _commodities]:
            try:
                results = coll.query(query_texts=[q], n_results=2)
                for doc, meta in zip(results['documents'][0], results['metadatas'][0]):
                    src = f"{meta.get('author', '?')} - {meta.get('title', '?')}"
                    chunks.append(f"[{src}]: {doc[:300]}")
            except Exception:
                pass
    # Dedup and limit
    seen = set()
    unique = []
    for c in chunks:
        key = c[:80]
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return "\n\n".join(unique[:8])


# ── Yesterday's intraday performance ─────────────────────────────────────────

def _get_yesterday_intraday(symbol: str) -> str:
    """Load yesterday's intraday trades for this symbol."""
    if not TRADE_LOG.exists():
        return "No intraday trade history available."

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    trades = []
    try:
        for line in TRADE_LOG.read_text().strip().split("\n"):
            if not line:
                continue
            t = json.loads(line)
            if t.get("symbol") == symbol and t.get("timestamp", "")[:10] == yesterday:
                trades.append(t)
    except Exception:
        pass

    if not trades:
        return f"No intraday trades for {symbol} yesterday."

    wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
    total_pnl = sum(t.get("pnl_pct", 0) for t in trades)
    return (
        f"Yesterday's intraday for {symbol}: {len(trades)} trades, "
        f"{wins} wins, total P&L {total_pnl:+.3f}%. "
        f"Exit reasons: {', '.join(t.get('exit_reason','?') for t in trades)}"
    )


# ── LLM ──────────────────────────────────────────────────────────────────────

from config.llm_factory import create_llm
llm_deep = create_llm("intraday_profiler", output_schema=IntradayProfile,
                       max_tokens_override=3000,
                       extra_body={"chat_template_kwargs": {"enable_thinking": False}})


# ── Main profiler function ────────────────────────────────────────────────────

@log_agent_call(agent_name="intraday_profiler", model_lane="fast")
def profile_asset(symbol: str, asset_class: str, technical_data: dict,
                  fundamentals_bias: str, sentiment_data: dict,
                  daily_direction: str) -> IntradayProfile:
    """Generate an IntradayProfile for a symbol using RAG + market data."""

    rag_guidance = _retrieve_intraday_guidance(symbol, asset_class)
    yesterday_perf = _get_yesterday_intraday(symbol)

    # Registry config for this instrument
    try:
        from config.instrument_registry import registry
        intraday_eligible = registry.is_intraday_eligible(symbol)
        min_atr = registry.get_min_atr(symbol)
        registry_note = (
            f"Registry config: intraday_eligible={intraday_eligible}, "
            f"min_daily_atr={min_atr}%. "
            f"If intraday_eligible is false, you MUST set allow_intraday=false."
        )
    except Exception:
        registry_note = ""
        intraday_eligible = True
        min_atr = 1.5

    # Build data context from what the daily pipeline already computed
    atr_pct = technical_data.get("atr_pct", 0)
    rsi = technical_data.get("rsi_14", 50)
    vol_trend = technical_data.get("volume_trend", "unknown")
    latest_close = technical_data.get("latest_close", 0)
    news_vol = sentiment_data.get("news_volume", 0)
    sentiment_bias = sentiment_data.get("sentiment", "neutral")
    sentiment_conf = sentiment_data.get("confidence", 3)

    prompt = f"""You are an intraday trading profiler. Decide whether and how {symbol} should be traded intraday today.

ASSET: {symbol} ({asset_class})
{registry_note}
DAILY DIRECTION: {daily_direction} (from the daily majority vote system)

TODAY'S MARKET DATA (computed by code — these numbers are exact):
- Daily ATR: {atr_pct:.2f}% of price (${latest_close:.2f})
- RSI(14): {rsi}
- Volume trend: {vol_trend}
- Fundamentals bias: {fundamentals_bias}
- Sentiment: {sentiment_bias} (confidence {sentiment_conf}/10, {news_vol} articles)

YESTERDAY'S INTRADAY PERFORMANCE:
{yesterday_perf}

TRADING LITERATURE ON INTRADAY {asset_class.upper()} TRADING:
{rag_guidance}

DECISION FACTORS:
1. **Liquidity**: Does this asset have enough 1-minute bar volume for clean fills?
   Oil ETFs (USO): Excellent liquidity, tight spreads, clean 1-min bars.
   Nat gas ETFs (UNG): Moderate liquidity, wider spreads, noisy 1-min bars.
   Gold ETFs (GLD): Good daily volume but moves slowly intraday — few viable 1-min signals.

2. **Volatility regime**: Is today's ATR high enough for meaningful intraday moves?
   Rule of thumb: 1-min ATR needs to be >0.05% for swing entries to clear spread + slippage.

3. **Catalyst timing**: Are there known events today (EIA 10:30 Wed, OPEC, Fed)?
   Avoid trading 15 min before/after major releases. Specify exact windows.

4. **Yesterday's performance**: If yesterday's intraday was unprofitable, tighten thresholds.
   If profitable, maintain current levels.

5. **Daily direction alignment**: Intraday trades only follow the daily direction.
   If daily says HOLD, set allow_intraday=false.

6. **Asset-class catalysts** (ONLY list catalysts relevant to THIS asset class):
   OIL catalysts: EIA Crude Oil Inventories (Wed 10:30 AM ET), OPEC/OPEC+ announcements,
     Baker Hughes rig count (Fri 1:00 PM ET), Iran/Saudi/geopolitical supply disruptions,
     API crude stockpiles (Tue 4:30 PM ET).
   NATGAS catalysts: EIA Natural Gas Storage Report (Thu 10:30 AM ET), weather forecasts
     (heating/cooling degree days), Freeport LNG/export terminal status, hurricane season impacts.
   GOLD catalysts: Fed rate decisions (FOMC, ~8x/year), CPI/PPI inflation data (monthly),
     jobs reports (NFP first Friday), Treasury auctions, geopolitical escalation (safe haven flows),
     USD strength (DXY moves), central bank gold purchases.
   Do NOT assign oil catalysts to gold or vice versa. EIA crude data does NOT affect gold.

OUTPUT RULES:
- allow_intraday=false if daily direction is HOLD, or if the asset is unsuitable for 1-min trading
- min_signal_strength: higher = more selective (0.4=loose, 0.6=moderate, 0.8=very selective)
- volume_spike_threshold: higher = requires bigger volume confirmation (2x=normal, 4x=strict)
- max_trades: fewer trades for noisier/less liquid assets
- stop_atr_multiple and target_atr_multiple: wider for volatile assets, tighter for stable ones
- preferred_entry_window: avoid first 15 min (open chaos) and last 30 min (close unwind)
- catalyst_times: list any known events with exact times if possible"""

    profile = llm_deep.invoke(prompt)

    # Post-LLM enforcement of registry rules
    if not intraday_eligible:
        profile.allow_intraday = False
        profile.max_trades = 0
    if atr_pct > 0 and atr_pct < min_atr:
        profile.allow_intraday = False
        profile.max_trades = 0

    # Post-LLM clamping — enforce sensible ranges deterministically
    profile.min_signal_strength = max(0.4, min(0.8, profile.min_signal_strength))
    profile.volume_spike_threshold = max(2.0, min(5.0, profile.volume_spike_threshold))
    profile.max_trades = max(0, min(5, profile.max_trades))
    profile.stop_atr_multiple = max(1.5, min(3.0, profile.stop_atr_multiple))
    profile.target_atr_multiple = max(2.0, min(5.0, profile.target_atr_multiple))
    # target must be > stop
    if profile.target_atr_multiple <= profile.stop_atr_multiple:
        profile.target_atr_multiple = profile.stop_atr_multiple + 0.5

    return profile


# ── Test mode ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(_BOTDIR))

    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["USO", "UNG", "GLD"]

    for symbol in symbols:
        from fundamentals_analyst import asset_class as get_ac
        ac = get_ac(symbol)

        # Mock the data that the daily pipeline would have
        mock_tech = {
            "USO": {"atr_pct": 6.64, "rsi_14": 43.7, "volume_trend": "falling", "latest_close": 121.72},
            "UNG": {"atr_pct": 2.44, "rsi_14": 38.2, "volume_trend": "flat", "latest_close": 10.83},
            "GLD": {"atr_pct": 1.85, "rsi_14": 55.1, "volume_trend": "rising", "latest_close": 440.0},
        }.get(symbol, {"atr_pct": 2.0, "rsi_14": 50, "volume_trend": "flat", "latest_close": 100})

        mock_sentiment = {
            "USO": {"sentiment": "bearish", "confidence": 9, "news_volume": 31},
            "UNG": {"sentiment": "neutral", "confidence": 3, "news_volume": 5},
            "GLD": {"sentiment": "neutral", "confidence": 4, "news_volume": 8},
        }.get(symbol, {"sentiment": "neutral", "confidence": 3, "news_volume": 0})

        mock_direction = {"USO": "HOLD", "UNG": "SHORT", "GLD": "LONG"}.get(symbol, "HOLD")

        print(f"\n{'='*60}")
        print(f"PROFILING: {symbol} ({ac})")
        print(f"{'='*60}")

        profile = profile_asset(
            symbol=symbol,
            asset_class=ac,
            technical_data=mock_tech,
            fundamentals_bias="bearish",
            sentiment_data=mock_sentiment,
            daily_direction=mock_direction,
        )

        print(f"  allow_intraday:        {profile.allow_intraday}")
        print(f"  reason:                {profile.reason}")
        print(f"  min_signal_strength:   {profile.min_signal_strength}")
        print(f"  volume_spike_threshold: {profile.volume_spike_threshold}")
        print(f"  max_trades:            {profile.max_trades}")
        print(f"  stop_atr_multiple:     {profile.stop_atr_multiple}")
        print(f"  target_atr_multiple:   {profile.target_atr_multiple}")
        print(f"  preferred_entry_window: {profile.preferred_entry_window}")
        print(f"  catalyst_times:        {profile.catalyst_times}")
