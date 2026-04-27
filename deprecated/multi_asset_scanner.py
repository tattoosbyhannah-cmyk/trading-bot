"""
Multi-Asset Scanner — runs complete trading pipeline across multiple symbols.
Ranks opportunities and identifies the best risk-adjusted trades across asset classes.
"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from paper_trading_executor import run_live_paper_trading, PaperTradingManager
from master_orchestrator import run_complete_trading_analysis, MasterTradingDecision
from trading_dashboard import TradingPerformanceDB


@dataclass
class AssetOpportunity:
    """Ranked trading opportunity across multiple assets."""
    symbol: str
    decision: str  # LONG, SHORT, HOLD
    confidence: int
    position_size: float
    risk_score: float  # Lower = better (from Risk Gatekeeper)
    expected_return: float  # Estimated based on price targets
    risk_adjusted_score: float  # Confidence / Risk Score
    key_thesis: str
    catalyst_timeline: List[str]
    timestamp: str


class MultiAssetScanner:
    """Scans multiple assets and ranks trading opportunities."""
    
    def __init__(self):
        self.db = TradingPerformanceDB()
        self.paper_manager = PaperTradingManager()
        
        # Asset universe - organized by category
        self.asset_universe = {
            "commodities": ["USO", "GLD", "UNG", "SLV", "DBA"],  # Oil, Gold, NatGas, Silver, Agriculture
            "equity_etfs": ["SPY", "QQQ", "IWM", "XLF", "XLE"],  # S&P500, Nasdaq, Russell2K, Finance, Energy
            "volatility": ["VIX", "UVXY"],  # Volatility plays
            "bonds": ["TLT", "HYG"],  # Treasury, High Yield
        }
        
        # Priority symbols for focused scanning
        self.priority_symbols = ["USO", "GLD", "UNG", "SPY", "QQQ"]
    
    def analyze_single_asset(self, symbol: str, timeout: int = 300) -> Optional[MasterTradingDecision]:
        """Run complete trading analysis on a single asset with timeout."""
        try:
            print(f"🔍 Analyzing {symbol}...")
            start_time = time.time()
            
            decision = run_complete_trading_analysis(symbol)
            
            analysis_time = time.time() - start_time
            print(f"✅ {symbol} analysis complete ({analysis_time:.1f}s)")
            
            return decision
            
        except Exception as e:
            print(f"❌ Error analyzing {symbol}: {str(e)[:100]}...")
            return None
    
    def calculate_expected_return(self, decision: MasterTradingDecision) -> float:
        """Estimate expected return based on price targets and decision."""
        if not decision.price_target or not decision.entry_price:
            # Use confidence-based estimate if no price targets
            if decision.final_decision.upper() == "LONG":
                return decision.confidence * 0.5  # 0.5% per confidence point
            elif decision.final_decision.upper() == "SHORT":
                return decision.confidence * 0.5
            else:
                return 0.0
        
        # Calculate based on price target
        expected_move = (decision.price_target - decision.entry_price) / decision.entry_price
        return expected_move * 100  # Convert to percentage
    
    def create_opportunity(self, decision: MasterTradingDecision) -> AssetOpportunity:
        """Convert MasterTradingDecision to ranked AssetOpportunity."""
        
        # Extract risk score from agent consensus or default
        risk_score = 5.0  # Default medium risk
        
        # Try to extract risk score from decision context
        for risk_factor in decision.risk_factors:
            if "Risk Score:" in risk_factor:
                try:
                    risk_score = float(risk_factor.split("Risk Score:")[1].split("/")[0].strip())
                    break
                except:
                    pass
        
        expected_return = self.calculate_expected_return(decision)
        
        # Risk-adjusted score: higher confidence, lower risk = better opportunity
        risk_adjusted_score = decision.confidence / max(risk_score, 1.0)
        
        return AssetOpportunity(
            symbol=decision.symbol,
            decision=decision.final_decision,
            confidence=decision.confidence,
            position_size=decision.position_size,
            risk_score=risk_score,
            expected_return=expected_return,
            risk_adjusted_score=risk_adjusted_score,
            key_thesis=decision.key_thesis,
            catalyst_timeline=decision.catalyst_timeline,
            timestamp=decision.timestamp
        )
    
    def scan_asset_class(self, asset_class: str, max_workers: int = 4) -> List[AssetOpportunity]:
        """Scan all assets in a specific class with parallel processing."""
        symbols = self.asset_universe.get(asset_class, [])
        opportunities = []
        
        print(f"\n📊 SCANNING {asset_class.upper()} ({len(symbols)} assets)")
        print("-" * 50)
        
        # Use ThreadPoolExecutor for parallel analysis
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all analysis jobs
            future_to_symbol = {
                executor.submit(self.analyze_single_asset, symbol): symbol 
                for symbol in symbols
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    decision = future.result(timeout=300)  # 5 minute timeout per asset
                    if decision:
                        opportunity = self.create_opportunity(decision)
                        opportunities.append(opportunity)
                except Exception as e:
                    print(f"❌ {symbol} failed: {str(e)[:50]}...")
        
        return opportunities
    
    def scan_priority_assets(self) -> List[AssetOpportunity]:
        """Quick scan of priority assets for immediate opportunities."""
        print(f"\n🎯 PRIORITY ASSET SCAN")
        print(f"Symbols: {', '.join(self.priority_symbols)}")
        print("=" * 60)
        
        opportunities = []
        
        for symbol in self.priority_symbols:
            decision = self.analyze_single_asset(symbol)
            if decision:
                opportunity = self.create_opportunity(decision)
                opportunities.append(opportunity)
        
        return opportunities
    
    def rank_opportunities(self, opportunities: List[AssetOpportunity]) -> List[AssetOpportunity]:
        """Rank opportunities by risk-adjusted score and filter executable trades."""
        
        # Filter to executable trades only (approved by Risk Gatekeeper)
        executable = [opp for opp in opportunities if opp.position_size > 0]
        
        # Sort by risk-adjusted score (higher = better)
        ranked = sorted(executable, key=lambda x: x.risk_adjusted_score, reverse=True)
        
        return ranked
    
    def display_scan_results(self, opportunities: List[AssetOpportunity]):
        """Display formatted scan results."""
        
        if not opportunities:
            print("\n📋 SCAN RESULTS: No executable opportunities found")
            print("All assets either resulted in HOLD decisions or were rejected by Risk Gatekeeper")
            return
        
        ranked_opps = self.rank_opportunities(opportunities)
        
        print(f"\n🏆 TOP TRADING OPPORTUNITIES")
        print("=" * 80)
        
        for i, opp in enumerate(ranked_opps[:5], 1):  # Top 5
            direction_emoji = "🟢" if opp.decision.startswith("LONG") else "🔴" if opp.decision.startswith("SHORT") else "⚪"
            
            print(f"{i}. {direction_emoji} {opp.symbol} - {opp.decision}")
            print(f"   Confidence: {opp.confidence}/10 | Risk Score: {opp.risk_score:.1f} | RA Score: {opp.risk_adjusted_score:.2f}")
            print(f"   Position Size: {opp.position_size:.1f}% | Expected Return: {opp.expected_return:.1f}%")
            print(f"   Thesis: {opp.key_thesis[:80]}...")
            if opp.catalyst_timeline:
                print(f"   Next Catalyst: {opp.catalyst_timeline[0]}")
            print()
        
        # Summary statistics
        total_scanned = len(opportunities)
        executable = len(ranked_opps)
        avg_confidence = sum(opp.confidence for opp in opportunities) / total_scanned if opportunities else 0
        
        print(f"📊 SCAN SUMMARY")
        print(f"   Assets Scanned: {total_scanned}")
        print(f"   Executable Trades: {executable}")
        print(f"   Hit Rate: {executable/total_scanned:.1%}")
        print(f"   Avg Confidence: {avg_confidence:.1f}/10")
        
        return ranked_opps


def run_full_market_scan():
    """Run comprehensive multi-asset scan across all categories."""
    scanner = MultiAssetScanner()
    
    print(f"\n{'🌍 FULL MARKET SCAN':=^70}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    all_opportunities = []
    
    # Scan each asset class
    for asset_class in scanner.asset_universe.keys():
        try:
            class_opps = scanner.scan_asset_class(asset_class, max_workers=4)
            all_opportunities.extend(class_opps)
            
            print(f"✅ {asset_class}: {len(class_opps)} opportunities found")
            time.sleep(2)  # Brief pause between asset classes
            
        except Exception as e:
            print(f"❌ {asset_class} scan failed: {e}")
    
    # Display ranked results
    scanner.display_scan_results(all_opportunities)
    
    return all_opportunities


def run_priority_scan():
    """Quick scan of priority assets only."""
    scanner = MultiAssetScanner()
    
    opportunities = scanner.scan_priority_assets()
    scanner.display_scan_results(opportunities)
    
    return opportunities


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "full":
        # Full market scan (20+ assets)
        opportunities = run_full_market_scan()
    elif len(sys.argv) > 1 and sys.argv[1] == "class":
        # Scan specific asset class
        asset_class = sys.argv[2] if len(sys.argv) > 2 else "commodities"
        scanner = MultiAssetScanner()
        opportunities = scanner.scan_asset_class(asset_class)
        scanner.display_scan_results(opportunities)
    else:
        # Default: priority assets only
        opportunities = run_priority_scan()
    
    if opportunities:
        ranked = sorted(opportunities, key=lambda x: x.risk_adjusted_score, reverse=True)
        if ranked:
            best_opportunity = ranked[0]
            print(f"\n🎯 BEST OPPORTUNITY: {best_opportunity.symbol} - {best_opportunity.decision}")
            print(f"Risk-Adjusted Score: {best_opportunity.risk_adjusted_score:.2f}")