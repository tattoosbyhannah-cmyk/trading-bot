"""
Fast Commodity Options Scanner — optimized for commodity ETF options trading.
Leverages cached EIA/agricultural fundamentals for 1-2 second analysis speed.
"""

from typing import Dict, List, Optional, Literal
from datetime import datetime, timedelta
from dataclasses import dataclass
import sqlite3
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# Import core components
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv()


@dataclass
class CommoditySignals:
    """Commodity-specific cached signals."""
    symbol: str
    fundamental_bias: str  # bearish/neutral/bullish
    eia_status: str  # for energy commodities
    agricultural_cycle: str  # for ag commodities
    macro_sentiment: str  # safe haven/risk-on/risk-off
    seasonal_factor: str  # seasonal tailwinds/headwinds
    cached_at: datetime
    expires_at: datetime


@dataclass
class CommodityOptionsFlow:
    """Commodity options flow and positioning."""
    symbol: str
    unusual_volume: float  # vs 30-day avg
    put_call_ratio: float
    iv_rank: float  # 0-100 percentile
    max_pain: Optional[float]
    gamma_exposure: str  # positive/negative/neutral
    commodity_specific: Dict  # sector-specific flow data


class FastCommodityAnalyst(BaseModel):
    """Ultra-fast commodity options analysis."""
    symbol: str = Field(description="Commodity ETF ticker")
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(description="Options bias")
    strategy: str = Field(description="Specific options strategy optimized for commodities")
    confidence: int = Field(description="Confidence 1-10", ge=1, le=10)
    entry_iv: float = Field(description="Recommended entry IV level")
    time_horizon: str = Field(description="Expected holding period")
    key_catalyst: str = Field(description="Primary catalyst (EIA, weather, geopolitical)")
    risk_factors: List[str] = Field(description="Top 3 commodity-specific risks")
    fundamental_context: str = Field(description="Brief fundamental backdrop")


class CommoditySignalCache:
    """High-performance commodity signal caching."""
    
    def __init__(self, cache_db: str = "commodity_signals.db"):
        self.cache_db = cache_db
        self.init_cache_db()
        
        # Commodity-specific cache lifetimes
        self.cache_lifetimes = {
            "eia_fundamentals": timedelta(hours=24),    # EIA updates weekly
            "agricultural_cycle": timedelta(days=7),    # Seasonal data changes slowly
            "macro_sentiment": timedelta(hours=4),      # Risk-on/off changes frequently
            "seasonal_factors": timedelta(days=30),     # Monthly seasonal updates
            "options_flow": timedelta(minutes=15),      # Options flow frequent updates
        }
        
        # Commodity categories for specialized analysis
        self.commodity_categories = {
            "energy": ["USO", "UNG", "XLE", "XOP"],
            "precious_metals": ["GLD", "SLV", "PPLT", "PALL"], 
            "industrial_metals": ["PDBC", "JJC", "JJN"],
            "agriculture": ["DBA", "CORN", "WEAT", "SOYB"],
            "livestock": ["COW"],
        }
    
    def init_cache_db(self):
        """Initialize SQLite cache database."""
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS commodity_signals (
                symbol TEXT,
                signal_type TEXT,
                data TEXT,
                cached_at TEXT,
                expires_at TEXT,
                PRIMARY KEY (symbol, signal_type)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def get_cached_signal(self, symbol: str, signal_type: str) -> Optional[dict]:
        """Retrieve cached signal if still valid."""
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT data, expires_at FROM commodity_signals 
            WHERE symbol = ? AND signal_type = ?
        """, (symbol, signal_type))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            data_json, expires_at = result
            if datetime.fromisoformat(expires_at) > datetime.now():
                return json.loads(data_json)
        
        return None
    
    def cache_signal(self, symbol: str, signal_type: str, data: dict):
        """Cache signal with appropriate expiration."""
        expires_at = datetime.now() + self.cache_lifetimes.get(signal_type, timedelta(hours=1))
        
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO commodity_signals 
            (symbol, signal_type, data, cached_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (symbol, signal_type, json.dumps(data), datetime.now().isoformat(), expires_at.isoformat()))
        
        conn.commit()
        conn.close()
    
    def get_commodity_category(self, symbol: str) -> str:
        """Identify commodity category for specialized analysis."""
        for category, symbols in self.commodity_categories.items():
            if symbol in symbols:
                return category
        return "general_commodity"


class CommodityOptionsFlowAnalyzer:
    """Commodity-specific options flow analysis."""
    
    def __init__(self):
        self.data_client = StockHistoricalDataClient(
            api_key=os.getenv("ALPACA_API_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
        )
    
    def get_commodity_options_flow(self, symbol: str, category: str) -> CommodityOptionsFlow:
        """Get commodity-specific options flow with sector context."""
        try:
            # Get current price
            price_req = StockLatestTradeRequest(symbol_or_symbols=[symbol])
            latest_trade = self.data_client.get_stock_latest_trade(price_req)
            current_price = float(latest_trade[symbol].price)
            
            # Commodity-specific flow patterns (mock data - production would use CBOE feeds)
            if category == "energy":
                flow_data = {
                    "unusual_volume": 2.1,  # Energy often sees high vol during geopolitical events
                    "put_call_ratio": 0.9,  # Balanced hedging in energy
                    "iv_rank": 70,          # Higher vol in energy complex
                    "gamma_exposure": "positive",
                    "sector_data": {"crude_contango": "normal", "refining_cracks": "elevated"}
                }
            elif category == "precious_metals":
                flow_data = {
                    "unusual_volume": 1.4,
                    "put_call_ratio": 0.7,  # More calls (safe haven buying)
                    "iv_rank": 45,          # Lower vol in metals
                    "gamma_exposure": "neutral",
                    "sector_data": {"real_rates": "negative", "dollar_weakness": "moderate"}
                }
            elif category == "agriculture":
                flow_data = {
                    "unusual_volume": 1.6,
                    "put_call_ratio": 1.1,  # Slightly bearish (harvest season)
                    "iv_rank": 60,
                    "gamma_exposure": "negative",
                    "sector_data": {"weather_risk": "normal", "usda_positioning": "neutral"}
                }
            else:
                # Default commodity flow
                flow_data = {
                    "unusual_volume": 1.3,
                    "put_call_ratio": 1.0,
                    "iv_rank": 55,
                    "gamma_exposure": "neutral",
                    "sector_data": {}
                }
            
            return CommodityOptionsFlow(
                symbol=symbol,
                unusual_volume=flow_data["unusual_volume"],
                put_call_ratio=flow_data["put_call_ratio"],
                iv_rank=flow_data["iv_rank"],
                max_pain=current_price * 0.99,
                gamma_exposure=flow_data["gamma_exposure"],
                commodity_specific=flow_data["sector_data"]
            )
            
        except Exception as e:
            print(f"Error getting commodity options flow for {symbol}: {e}")
            return CommodityOptionsFlow(
                symbol=symbol,
                unusual_volume=1.0,
                put_call_ratio=1.0,
                iv_rank=50,
                max_pain=None,
                gamma_exposure="neutral",
                commodity_specific={}
            )


class FastCommodityTechnicalAnalyzer:
    """Fast technical analysis optimized for commodity volatility."""
    
    def __init__(self):
        self.data_client = StockHistoricalDataClient(
            api_key=os.getenv("ALPACA_API_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
        )
    
    def get_commodity_signals(self, symbol: str, category: str) -> Dict:
        """Get commodity-optimized technical signals."""
        try:
            # Use 5-minute bars for commodity volatility
            req = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Minute,
                start=datetime.now() - timedelta(hours=3),
                limit=100
            )
            
            bars = self.data_client.get_stock_bars(req)
            bar_data = bars.data.get(symbol, [])
            
            if len(bar_data) < 20:
                return {"error": "insufficient data"}
            
            # Commodity-specific technical calculations
            closes = [float(bar.close) for bar in bar_data[-20:]]
            volumes = [float(bar.volume) for bar in bar_data[-20:]]
            highs = [float(bar.high) for bar in bar_data[-20:]]
            lows = [float(bar.low) for bar in bar_data[-20:]]
            
            latest_price = closes[-1]
            sma_10 = sum(closes[-10:]) / 10
            sma_20 = sum(closes[-20:]) / 20
            
            # Volatility measures (important for options)
            price_ranges = [(h - l) / l for h, l in zip(highs[-10:], lows[-10:])]
            avg_volatility = sum(price_ranges) / len(price_ranges)
            recent_volatility = sum(price_ranges[-3:]) / 3
            vol_expansion = recent_volatility / avg_volatility if avg_volatility > 0 else 1.0
            
            # Volume analysis
            recent_vol = sum(volumes[-5:]) / 5
            avg_vol = sum(volumes[-20:]) / 20
            vol_surge = recent_vol / avg_vol if avg_vol > 0 else 1.0
            
            # Momentum with commodity volatility consideration
            price_momentum = (latest_price - sma_10) / sma_10 * 100
            trend_strength = "strong" if abs(price_momentum) > 2 else "moderate" if abs(price_momentum) > 0.5 else "weak"
            
            # Breakout detection with commodity-specific thresholds
            resistance = max(highs[-10:])
            support = min(lows[-10:])
            breakout_up = latest_price > resistance * 1.005  # 0.5% breakout threshold
            breakdown = latest_price < support * 0.995
            
            momentum = "bullish" if latest_price > sma_10 > sma_20 else "bearish" if latest_price < sma_10 < sma_20 else "neutral"
            
            return {
                "momentum": momentum,
                "trend_strength": trend_strength,
                "breakout_up": breakout_up,
                "breakdown": breakdown,
                "vol_surge": vol_surge,
                "vol_expansion": vol_expansion,
                "price_momentum": price_momentum,
                "support": support,
                "resistance": resistance,
                "current_price": latest_price
            }
            
        except Exception as e:
            print(f"Error in commodity technical for {symbol}: {e}")
            return {"error": str(e)}


# Fast LLM for commodity analysis
commodity_llm = ChatOpenAI(
    base_url="http://127.0.0.1:8081/v1",
    api_key="not-needed",
    model="qwen3-coder",
    temperature=0.4,
    max_tokens=2000,
).with_structured_output(FastCommodityAnalyst)


def analyze_commodity_options_fast(symbol: str, cache: CommoditySignalCache) -> FastCommodityAnalyst:
    """Ultra-fast commodity options analysis pipeline."""
    
    start_time = time.time()
    print(f"⚡ Fast analyzing {symbol}...")
    
    # Step 1: Determine commodity category
    category = cache.get_commodity_category(symbol)
    
    # Step 2: Get cached fundamentals (instant if cached)
    eia_data = cache.get_cached_signal(symbol, "eia_fundamentals")
    macro_data = cache.get_cached_signal(symbol, "macro_sentiment")
    seasonal_data = cache.get_cached_signal(symbol, "seasonal_factors")
    
    # Step 3: Real-time options flow analysis
    flow_analyzer = CommodityOptionsFlowAnalyzer()
    options_flow = flow_analyzer.get_commodity_options_flow(symbol, category)
    
    # Step 4: Fast technical analysis
    tech_analyzer = FastCommodityTechnicalAnalyzer()
    technical = tech_analyzer.get_commodity_signals(symbol, category)
    
    if "error" in technical:
        print(f"❌ Technical analysis failed for {symbol}")
        return None
    
    # Step 5: Commodity-specific synthesis
    context = f"""FAST COMMODITY OPTIONS ANALYSIS for {symbol} ({category.upper()}):

TECHNICAL ANALYSIS:
- Momentum: {technical['momentum']} ({technical['trend_strength']})
- Price momentum: {technical['price_momentum']:+.1f}%
- Breakouts: Up={technical['breakout_up']}, Down={technical['breakdown']}
- Volume surge: {technical['vol_surge']:.1f}x
- Volatility expansion: {technical['vol_expansion']:.1f}x
- Current price: ${technical['current_price']:.2f}
- Support/Resistance: ${technical['support']:.2f} / ${technical['resistance']:.2f}

OPTIONS FLOW ({category.upper()}):
- Unusual volume: {options_flow.unusual_volume:.1f}x
- Put/Call ratio: {options_flow.put_call_ratio:.2f}
- IV rank: {options_flow.iv_rank}/100 percentile
- Gamma exposure: {options_flow.gamma_exposure}
- Sector specifics: {options_flow.commodity_specific}

CACHED FUNDAMENTALS:
- EIA/Supply data: {eia_data or f"No cached data for {category}"}
- Macro sentiment: {macro_data or "Risk-neutral"}
- Seasonal factors: {seasonal_data or "No seasonal bias"}

COMMODITY CONTEXT:
{category.upper()} options typically respond to:
- Energy: EIA inventory reports, geopolitical events, refining capacity
- Metals: Fed policy, real rates, dollar strength, safe haven flows
- Agriculture: Weather, USDA reports, global demand, seasonal cycles

Recommend OPTIONS STRATEGY considering:
1. {category.upper()}-specific volatility patterns
2. Upcoming catalysts (EIA Wednesday, USDA reports)
3. IV rank for premium timing
4. Time decay management for commodity volatility
5. Directional bias from momentum + flow + fundamentals
"""
    
    try:
        analysis = commodity_llm.invoke(context)
        analysis_time = time.time() - start_time
        print(f"✅ {symbol} analyzed in {analysis_time:.1f}s")
        return analysis
        
    except Exception as e:
        analysis_time = time.time() - start_time
        print(f"❌ {symbol} LLM error in {analysis_time:.1f}s: {str(e)[:50]}...")
        return None


def scan_commodity_options(symbols: List[str]) -> List[FastCommodityAnalyst]:
    """Scan commodity symbols for options opportunities."""
    
    cache = CommoditySignalCache()
    results = []
    
    print(f"⚡ FAST COMMODITY OPTIONS SCAN: {len(symbols)} symbols")
    print("="*60)
    
    start_time = time.time()
    
    # Parallel processing
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_symbol = {
            executor.submit(analyze_commodity_options_fast, symbol, cache): symbol 
            for symbol in symbols
        }
        
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                analysis = future.result(timeout=60)
                if analysis:
                    results.append(analysis)
                    direction_emoji = "🟢" if analysis.direction == "BULLISH" else "🔴" if analysis.direction == "BEARISH" else "⚪"
                    print(f"{direction_emoji} {symbol}: {analysis.strategy} ({analysis.confidence}/10)")
            except Exception as e:
                print(f"❌ {symbol}: {str(e)[:30]}...")
    
    total_time = time.time() - start_time
    avg_time = total_time / len(symbols) if symbols else 0
    
    print(f"\n⚡ COMMODITY SCAN COMPLETE")
    print(f"Total time: {total_time:.1f}s")
    print(f"Avg per symbol: {avg_time:.1f}s")
    print(f"Opportunities found: {len(results)}")
    
    return results


if __name__ == "__main__":
    # Commodity-focused symbol lists
    all_commodities = ["USO", "GLD", "UNG", "SLV", "DBA", "CORN", "WEAT"]
    energy_complex = ["USO", "UNG", "XLE"]
    metals_complex = ["GLD", "SLV", "PDBC"]
    agriculture_complex = ["DBA", "CORN", "WEAT", "SOYB"]
    
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "energy":
            symbols = energy_complex
            print("🛢️  ENERGY COMMODITY OPTIONS SCAN")
        elif sys.argv[1] == "metals":
            symbols = metals_complex
            print("🥇 METALS COMMODITY OPTIONS SCAN")
        elif sys.argv[1] == "agriculture":
            symbols = agriculture_complex
            print("🌾 AGRICULTURE COMMODITY OPTIONS SCAN")
        else:
            # Custom symbols
            symbols = sys.argv[1].split(',')
            print(f"🔄 CUSTOM COMMODITY SCAN: {', '.join(symbols)}")
    else:
        # Default: all commodities
        symbols = all_commodities
        print("📊 FULL COMMODITY OPTIONS SCAN")
    
    opportunities = scan_commodity_options(symbols)
    
    if opportunities:
        print(f"\n🎯 TOP COMMODITY OPTIONS OPPORTUNITIES")
        print("="*60)
        
        sorted_opps = sorted(opportunities, key=lambda x: x.confidence, reverse=True)
        for opp in sorted_opps:
            category_emoji = "🛢️" if opp.symbol in ["USO", "UNG", "XLE"] else "🥇" if opp.symbol in ["GLD", "SLV"] else "🌾"
            print(f"{category_emoji} {opp.symbol}: {opp.strategy}")
            print(f"  Direction: {opp.direction} | Confidence: {opp.confidence}/10")
            print(f"  Entry IV: {opp.entry_iv} | Time: {opp.time_horizon}")
            print(f"  Catalyst: {opp.key_catalyst}")
            print(f"  Context: {opp.fundamental_context}")
            print()