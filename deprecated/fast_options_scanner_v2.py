"""
Fast Options Scanner v2 - Ultra-optimized dual-model system.
Uses 7B model for fast agents, 30B model for final selection.
Target: sub-10 seconds total for options trading decisions.
"""

from typing import Dict, List, Optional, Literal
from datetime import datetime, timedelta
from dataclasses import dataclass
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from model_config import model_router
from pydantic import BaseModel, Field

# Import existing components
from fast_options_scanner import (
    CommoditySignalCache, 
    CommodityOptionsFlowAnalyzer,
    FastCommodityTechnicalAnalyzer
)


class FastCommodityAnalyst(BaseModel):
    """Fast commodity analysis using 7B model."""
    symbol: str = Field(description="Commodity ETF ticker")
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(description="Options bias")
    strategy: str = Field(description="Options strategy")
    confidence: int = Field(description="Confidence 1-10", ge=1, le=10)
    entry_iv: float = Field(description="Entry IV level")
    time_horizon: str = Field(description="Holding period")
    key_catalyst: str = Field(description="Primary catalyst")
    risk_factors: List[str] = Field(description="Top 3 risks")


def fast_analyze_commodity(symbol: str, cache: CommoditySignalCache) -> FastCommodityAnalyst:
    """Lightning-fast analysis using 7B model (target: <2 seconds)."""
    
    start_time = time.time()
    print(f"⚡ Fast analyzing {symbol}...")
    
    # Get category
    category = cache.get_commodity_category(symbol)
    
    # Fast technical + options flow (parallel)
    flow_analyzer = CommodityOptionsFlowAnalyzer()
    tech_analyzer = FastCommodityTechnicalAnalyzer()
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        flow_future = executor.submit(flow_analyzer.get_commodity_options_flow, symbol, category)
        tech_future = executor.submit(tech_analyzer.get_commodity_signals, symbol, category)
        
        options_flow = flow_future.result()
        technical = tech_future.result()
    
    if "error" in technical:
        return None
    
    # Ultra-fast synthesis using 7B model - minimal prompt
    fast_llm = model_router.get_structured_output("fast_commodity_analyzer", FastCommodityAnalyst)
    
    # Simplified prompt for maximum speed
    context = f"""Quick {symbol} options analysis:
- Price: ${technical['current_price']:.2f}
- Trend: {technical['momentum']} 
- Volume: {technical['vol_surge']:.1f}x
- IV: {options_flow.iv_rank}%

Strategy recommendation for {category} commodity."""
    
    try:
        analysis = fast_llm.invoke(context)
        analysis_time = time.time() - start_time
        print(f"✅ {symbol} fast analysis: {analysis_time:.1f}s")
        return analysis
        
    except Exception as e:
        print(f"❌ {symbol} fast analysis error: {str(e)[:50]}...")
        return None


def master_synthesis(opportunities: List[FastCommodityAnalyst]) -> Dict:
    """Ultra-fast selection using 30B thinking model - no structured output."""
    
    if not opportunities:
        return None
    
    print("🧠 Master synthesis using thinking model...")
    start_time = time.time()
    
    # Use thinking model with simple text response (no structured output)
    master_llm = model_router.get_model_for_agent("master_synthesis")
    
    # Prepare concise opportunity summary
    opp_summaries = []
    best_confidence = 0
    for opp in opportunities:
        opp_summaries.append(f"{opp.symbol}({opp.confidence}/10)")
        best_confidence = max(best_confidence, opp.confidence)
    
    # Ultra-simple prompt for speed
    context = f"""Pick best trade: {', '.join(opp_summaries)}
    
Respond format: "SYMBOL: Strategy"
Example: "USO: Bear Put Spread" """
    
    try:
        response = master_llm.invoke(context)
        synthesis_time = time.time() - start_time
        print(f"🧠 Master synthesis complete: {synthesis_time:.1f}s")
        
        # Parse the simple response
        response_text = response.content if hasattr(response, 'content') else str(response)
        
        # Extract symbol and strategy
        if ':' in response_text:
            parts = response_text.split(':')
            symbol = parts[0].strip()
            strategy = parts[1].strip()
        else:
            # Fallback - use highest confidence option
            best_opp = max(opportunities, key=lambda x: x.confidence)
            symbol = best_opp.symbol
            strategy = best_opp.strategy
        
        return {
            "symbol": symbol,
            "final_decision": strategy,
            "confidence": best_confidence,
            "synthesis_time": synthesis_time,
            "raw_response": response_text[:100]  # For debugging
        }
        
    except Exception as e:
        print(f"❌ Master synthesis error: {str(e)[:50]}...")
        # Fallback - return highest confidence option
        if opportunities:
            best_opp = max(opportunities, key=lambda x: x.confidence)
            return {
                "symbol": best_opp.symbol,
                "final_decision": best_opp.strategy,
                "confidence": best_opp.confidence,
                "synthesis_time": time.time() - start_time,
                "raw_response": "fallback_selection"
            }
        return None


def ultra_fast_commodity_scan(symbols: List[str]) -> Dict:
    """Complete ultra-fast scan + selection pipeline."""
    
    total_start = time.time()
    cache = CommoditySignalCache()
    
    print(f"🚀 ULTRA-FAST DUAL-MODEL PIPELINE: {len(symbols)} symbols")
    print("="*70)
    
    # Step 1: Lightning-fast parallel analysis (7B model)
    print("Phase 1: Fast agents (7B model)...")
    fast_opportunities = []
    
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_symbol = {
            executor.submit(fast_analyze_commodity, symbol, cache): symbol 
            for symbol in symbols
        }
        
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                analysis = future.result(timeout=10)  # 10 second timeout
                if analysis:
                    fast_opportunities.append(analysis)
            except Exception as e:
                print(f"❌ {symbol}: {str(e)[:30]}...")
    
    fast_time = time.time() - total_start
    
    # Step 2: Ultra-fast master selection (30B thinking model)
    print("Phase 2: Master selection (30B model)...")
    master_decision = master_synthesis(fast_opportunities)
    
    total_time = time.time() - total_start
    
    # Build comprehensive result
    result = {
        "success": master_decision is not None,
        "total_time": total_time,
        "fast_scan_time": fast_time,
        "synthesis_time": total_time - fast_time,
        "opportunities_found": len(fast_opportunities),
        "master_decision": master_decision,
        "all_opportunities": fast_opportunities,
        "performance": {
            "avg_fast_time": fast_time / len(symbols) if symbols else 0,
            "target_met": total_time < 10,
            "speed_rating": "EXCELLENT" if total_time < 8 else "GOOD" if total_time < 12 else "NEEDS_WORK"
        }
    }
    
    # Display comprehensive results
    print(f"\n🎯 ULTRA-FAST DUAL-MODEL RESULTS")
    print("="*50)
    print(f"Total time: {total_time:.1f}s ({'✅ EXCELLENT' if total_time < 8 else '✅ GOOD' if total_time < 12 else '⚠️ SLOW'})")
    print(f"Fast scan: {fast_time:.1f}s ({result['performance']['avg_fast_time']:.1f}s per symbol)")
    print(f"Synthesis: {result['synthesis_time']:.1f}s")
    print(f"Speed rating: {result['performance']['speed_rating']}")
    
    # Show selected trade
    if master_decision:
        print(f"\n🏆 SELECTED TRADE:")
        print(f"  Symbol: {master_decision['symbol']}")
        print(f"  Strategy: {master_decision['final_decision']}")
        print(f"  Confidence: {master_decision['confidence']}/10")
        
    # Show all opportunities for context
    if fast_opportunities:
        print(f"\n📊 ALL OPPORTUNITIES ({len(fast_opportunities)} found):")
        sorted_opps = sorted(fast_opportunities, key=lambda x: x.confidence, reverse=True)
        for i, opp in enumerate(sorted_opps, 1):
            direction_emoji = "🟢" if opp.direction == "BULLISH" else "🔴" if opp.direction == "BEARISH" else "⚪"
            print(f"  {i}. {direction_emoji} {opp.symbol}: {opp.strategy} ({opp.confidence}/10)")
    
    # Speed assessment for options trading
    print(f"\n🎯 OPTIONS TRADING READINESS:")
    if total_time < 8:
        print("✅ EXCELLENT - Ready for high-frequency options day trading")
    elif total_time < 12:
        print("✅ GOOD - Ready for options swing trading and event-driven strategies")
    elif total_time < 20:
        print("⚠️ ACCEPTABLE - Suitable for daily options screening")
    else:
        print("❌ TOO SLOW - Need further optimization for options trading")
    
    return result


def quick_single_symbol_test(symbol: str = "USO"):
    """Quick test on single symbol for speed validation."""
    print(f"⚡ QUICK TEST: {symbol}")
    cache = CommoditySignalCache()
    
    start_time = time.time()
    analysis = fast_analyze_commodity(symbol, cache)
    total_time = time.time() - start_time
    
    if analysis:
        print(f"✅ Single symbol test: {total_time:.1f}s")
        print(f"Strategy: {analysis.strategy} ({analysis.confidence}/10)")
        return total_time < 3  # Target: sub-3 seconds for single symbol
    else:
        print("❌ Single symbol test failed")
        return False


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "quick":
        # Quick single symbol test
        quick_single_symbol_test("USO")
    elif len(sys.argv) > 1 and sys.argv[1] == "single":
        # Single symbol analysis
        symbol = sys.argv[2] if len(sys.argv) > 2 else "USO"
        quick_single_symbol_test(symbol)
    else:
        # Full multi-symbol test
        test_symbols = ["USO", "GLD", "UNG"]
        print("⚡ TESTING ULTRA-FAST DUAL-MODEL SYSTEM")
        
        result = ultra_fast_commodity_scan(test_symbols)
        
        if result["success"]:
            print(f"\n🚀 FINAL PERFORMANCE SUMMARY:")
            print(f"  Per symbol: {result['performance']['avg_fast_time']:.1f}s")
            print(f"  Total pipeline: {result['total_time']:.1f}s")
            print(f"  Rating: {result['performance']['speed_rating']}")
            
            if result["performance"]["target_met"]:
                print("🎉 TARGET ACHIEVED: Ready for live options trading!")
            else:
                print("⚡ Close to target - minor optimization needed")
