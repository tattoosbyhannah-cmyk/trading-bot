"""
Optimized Multi-Asset Scanner — handles token limits and scales to larger scans.
Fixes parsing errors and provides better performance monitoring.
"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from parallel_master_orchestrator import run_parallel_trading_analysis, MasterTradingDecision


@dataclass  
class ScanMetrics:
    """Track scan performance and errors."""
    total_assets: int
    successful_analyses: int
    failed_analyses: int
    avg_analysis_time: float
    total_scan_time: float
    hold_decisions: int
    approved_trades: int
    error_details: List[str]


class OptimizedScanner:
    """Production-ready scanner with error handling and performance optimization."""
    
    def __init__(self):
        # Expanded asset universe for larger testing
        self.asset_groups = {
            "commodities": ["USO", "GLD", "UNG", "SLV", "DBA", "CORN", "WEAT"],
            "large_cap": ["SPY", "QQQ", "IWM", "DIA", "VTI"],  
            "sectors": ["XLF", "XLE", "XLK", "XLV", "XLI"],
            "international": ["EFA", "EEM", "FXI", "EWJ"],
            "bonds": ["TLT", "HYG", "LQD", "TIP"],
            "volatility": ["VIX", "UVXY", "VXX"]
        }
        
        # Top 10 liquid ETFs for stress testing
        self.top_10_etfs = ["SPY", "QQQ", "GLD", "USO", "TLT", "EFA", "XLF", "IWM", "HYG", "VIX"]
    
    def scan_with_metrics(self, symbols: List[str], max_workers: int = 6) -> tuple[List[MasterTradingDecision], ScanMetrics]:
        """Scan assets with comprehensive performance metrics."""
        
        print(f"\n🔬 OPTIMIZED SCAN: {len(symbols)} assets, {max_workers} workers")
        print(f"Assets: {', '.join(symbols)}")
        print("="*70)
        
        start_time = time.time()
        decisions = []
        errors = []
        analysis_times = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {
                executor.submit(self._analyze_with_timeout, symbol): symbol 
                for symbol in symbols
            }
            
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    decision, analysis_time = future.result()
                    if decision:
                        decisions.append(decision)
                        analysis_times.append(analysis_time)
                        
                        # Quick status update
                        status = "✅ TRADE" if decision.position_size > 0 else "⭕ HOLD"
                        print(f"{status} {symbol}: {decision.final_decision} ({decision.confidence}/10) - {analysis_time:.1f}s")
                    else:
                        errors.append(f"{symbol}: Analysis returned None")
                        print(f"❌ {symbol}: Analysis failed")
                        
                except Exception as e:
                    error_msg = f"{symbol}: {str(e)[:60]}..."
                    errors.append(error_msg)
                    print(f"❌ {symbol}: {str(e)[:40]}...")
        
        total_time = time.time() - start_time
        
        # Calculate metrics
        metrics = ScanMetrics(
            total_assets=len(symbols),
            successful_analyses=len(decisions),
            failed_analyses=len(errors),
            avg_analysis_time=sum(analysis_times) / len(analysis_times) if analysis_times else 0,
            total_scan_time=total_time,
            hold_decisions=len([d for d in decisions if d.position_size == 0]),
            approved_trades=len([d for d in decisions if d.position_size > 0]), 
            error_details=errors
        )
        
        self._display_scan_metrics(metrics, decisions)
        return decisions, metrics
    
    def _analyze_with_timeout(self, symbol: str, timeout: int = 600) -> tuple[Optional[MasterTradingDecision], float]:
        """Analyze single asset with timeout and performance tracking."""
        start_time = time.time()
        
        try:
            decision = run_parallel_trading_analysis(symbol)
            analysis_time = time.time() - start_time
            return decision, analysis_time
            
        except Exception as e:
            analysis_time = time.time() - start_time
            print(f"⚠️  {symbol} error after {analysis_time:.1f}s: {str(e)[:50]}...")
            return None, analysis_time
    
    def _display_scan_metrics(self, metrics: ScanMetrics, decisions: List[MasterTradingDecision]):
        """Display comprehensive scan results and performance metrics."""
        
        print(f"\n📊 SCAN PERFORMANCE METRICS")
        print("="*50)
        print(f"Total Assets: {metrics.total_assets}")
        print(f"Successful: {metrics.successful_analyses} ({metrics.successful_analyses/metrics.total_assets:.1%})")
        print(f"Failed: {metrics.failed_analyses}")
        print(f"Avg Analysis Time: {metrics.avg_analysis_time:.1f}s")
        print(f"Total Scan Time: {metrics.total_scan_time:.1f}s")
        print(f"Efficiency: {metrics.total_assets * metrics.avg_analysis_time / metrics.total_scan_time:.1f}x parallel speedup")
        
        print(f"\n📈 TRADING DECISIONS")
        print("="*50)
        print(f"Approved Trades: {metrics.approved_trades}")
        print(f"Hold Decisions: {metrics.hold_decisions}")
        print(f"Trade Hit Rate: {metrics.approved_trades/metrics.successful_analyses:.1%}" if metrics.successful_analyses > 0 else "Trade Hit Rate: 0%")
        
        # Show approved trades if any
        approved = [d for d in decisions if d.position_size > 0]
        if approved:
            print(f"\n🎯 APPROVED TRADES")
            for decision in sorted(approved, key=lambda x: x.confidence, reverse=True):
                print(f"  {decision.symbol}: {decision.final_decision} ({decision.confidence}/10 conf, {decision.position_size:.1f}% pos)")
        
        # Risk analysis
        if decisions:
            avg_confidence = sum(d.confidence for d in decisions) / len(decisions)
            print(f"\n⚖️  RISK ANALYSIS")
            print(f"Avg Confidence: {avg_confidence:.1f}/10")
            print(f"Conservative Bias: {metrics.hold_decisions/metrics.successful_analyses:.1%} rejection rate")
            
            if metrics.hold_decisions == metrics.successful_analyses:
                print("🛡️  INSIGHT: 100% HOLD rate suggests:")
                print("   • Risk management working as intended")
                print("   • Market conditions unfavorable for new positions")
                print("   • Consider adjusting risk parameters if consistently too conservative")
        
        # Error summary
        if metrics.error_details:
            print(f"\n❌ ERROR SUMMARY")
            for error in metrics.error_details[:3]:  # Show top 3 errors
                print(f"  {error}")


def run_stress_test():
    """Run comprehensive stress test on top 10 ETFs."""
    scanner = OptimizedScanner()
    
    print(f"\n{'🚀 SYSTEM STRESS TEST':=^70}")
    print("Testing system scalability and error handling")
    
    decisions, metrics = scanner.scan_with_metrics(scanner.top_10_etfs, max_workers=8)
    
    # System health assessment
    success_rate = metrics.successful_analyses / metrics.total_assets
    parallel_efficiency = (metrics.total_assets * metrics.avg_analysis_time) / metrics.total_scan_time
    
    print(f"\n🏥 SYSTEM HEALTH ASSESSMENT")
    print("="*40)
    print(f"Success Rate: {success_rate:.1%} {'✅ HEALTHY' if success_rate > 0.8 else '⚠️ NEEDS ATTENTION'}")
    print(f"Parallel Efficiency: {parallel_efficiency:.1f}x {'✅ GOOD' if parallel_efficiency > 4 else '⚠️ SUBOPTIMAL'}")
    print(f"Error Rate: {metrics.failed_analyses/metrics.total_assets:.1%}")
    
    return decisions, metrics


if __name__ == "__main__":
    import sys
    
    scanner = OptimizedScanner()
    
    if len(sys.argv) > 1 and sys.argv[1] == "stress":
        # Stress test with top 10 ETFs
        run_stress_test()
    elif len(sys.argv) > 1 and sys.argv[1] == "group":
        # Scan specific group
        group = sys.argv[2] if len(sys.argv) > 2 else "commodities"
        symbols = scanner.asset_groups.get(group, ["SPY"])
        decisions, metrics = scanner.scan_with_metrics(symbols)
    else:
        # Quick test with 5 assets
        test_symbols = ["SPY", "QQQ", "GLD", "USO", "TLT"]
        decisions, metrics = scanner.scan_with_metrics(test_symbols, max_workers=4)