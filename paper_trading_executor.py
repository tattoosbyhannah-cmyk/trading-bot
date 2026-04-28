"""
Paper Trading Executor — connects Master Orchestrator to broker via adapter.
Executes trading decisions with position management and performance tracking.

Includes:
- Kill switch enforcement (file-based halt)
- Fill record logging with slippage measurement
- Bid-ask spread estimation (informational + 200 bps reject circuit-breaker)
- Broker-agnostic via brokers/ adapter layer
"""

import json
import logging
import time
from typing import Optional, Dict, List
import os
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass

from pydantic import BaseModel, Field

from brokers.broker_factory import get_broker
from brokers.base_broker import OrderRequest, OrderSide, OrderType, OrderStatus

from master_orchestrator import run_complete_trading_analysis, MasterTradingDecision
from trading_dashboard import TradingPerformanceDB

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)

# ── Kill Switch ──────────────────────────────────────────────────────────────

KILL_SWITCH_FILE = Path(__file__).parent / "KILL_SWITCH"
FILL_LOG = Path(__file__).parent / "logs" / "fill_records.jsonl"


def check_kill_switch():
    """Raise if kill switch is engaged. Check BEFORE every order."""
    if KILL_SWITCH_FILE.exists():
        reason = KILL_SWITCH_FILE.read_text().strip() or "No reason given"
        raise RuntimeError(
            f"KILL SWITCH ENGAGED: {reason}. "
            f"Remove {KILL_SWITCH_FILE} to re-enable trading."
        )


# ── Fill Record / Slippage Tracking ──────────────────────────────────────────

class FillRecord(BaseModel):
    timestamp: str
    symbol: str
    side: str
    decision_price: float
    expected_price: float
    filled_price: float
    quantity: float
    slippage_bps: float
    spread_estimate_bps: float
    total_cost_bps: float
    order_id: str
    calculation_run_id: str = ""


def _log_fill(record: FillRecord):
    try:
        FILL_LOG.parent.mkdir(exist_ok=True)
        with open(FILL_LOG, "a") as f:
            f.write(record.model_dump_json() + "\n")
    except Exception as e:
        logging.warning(f"Failed to log fill record: {e}")


# ── Spread Estimation ────────────────────────────────────────────────────────

SPREAD_WARN_BPS = 50    # Informational only — logged for post-hoc analysis
SPREAD_REJECT_BPS = 200 # Circuit-breaker — reject trade entirely (flash-crash / halt)


def estimate_spread(symbol: str, broker_name: str = "alpaca") -> dict:
    """Get current bid-ask spread via broker adapter."""
    try:
        broker = get_broker(broker_name)
        quote = broker.get_latest_quote(symbol)
        if quote["spread_bps"] > SPREAD_WARN_BPS:
            logging.warning(
                f"[SPREAD] {symbol}: bid=${quote['bid']:.2f} ask=${quote['ask']:.2f} "
                f"mid=${quote['mid']:.2f} spread={quote['spread_bps']:.1f}bps "
                f"(>{SPREAD_WARN_BPS}bps — likely pre/post market or stale quote)"
            )
        return quote
    except Exception as e:
        logging.warning(f"Spread estimation failed for {symbol}: {e}")
        return {"bid": 0, "ask": 0, "mid": 0, "spread_bps": 0}


@dataclass
class PaperTradeResult:
    """Result of a paper trade execution."""
    success: bool
    order_id: Optional[str] = None
    filled_qty: Optional[float] = None
    filled_price: Optional[float] = None
    error_message: Optional[str] = None
    timestamp: str = ""


class PaperTradingManager:
    """Manages paper trading execution via broker adapter."""

    def __init__(self, broker_name: str = "alpaca"):
        self.broker = get_broker(broker_name)
        self.db = TradingPerformanceDB()

    def get_portfolio_value(self) -> float:
        try:
            return self.broker.get_account()["portfolio_value"]
        except Exception as e:
            print(f"Error getting portfolio value: {e}")
            return 100000.0

    def get_current_positions(self) -> Dict[str, float]:
        positions = {}
        try:
            for p in self.broker.get_all_positions():
                positions[p.symbol] = p.qty
        except Exception as e:
            print(f"Error getting positions: {e}")
        return positions

    def calculate_share_quantity(self, symbol: str, position_size_pct: float, current_price: float) -> int:
        portfolio_value = self.get_portfolio_value()
        position_value = portfolio_value * (position_size_pct / 100)
        shares = int(position_value / current_price)
        return max(shares, 1) if position_size_pct > 0 else 0

    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            return self.broker.get_current_price(symbol)
        except Exception as e:
            print(f"Error getting price for {symbol}: {e}")
            return None

    def _record_fill(self, symbol: str, side: str, order_id: str,
                     decision_price: float, expected_price: float,
                     filled_price: float, quantity: float,
                     spread_bps: float):
        if expected_price <= 0:
            return
        slippage_bps = (filled_price - expected_price) / expected_price * 10000
        if side == "sell":
            slippage_bps = -slippage_bps
        _log_fill(FillRecord(
            timestamp=datetime.now().isoformat(),
            symbol=symbol,
            side=side,
            decision_price=decision_price,
            expected_price=expected_price,
            filled_price=filled_price,
            quantity=quantity,
            slippage_bps=round(slippage_bps, 2),
            spread_estimate_bps=round(spread_bps, 2),
            total_cost_bps=round(abs(slippage_bps) + spread_bps, 2),
            order_id=order_id,
        ))

    def _execute_trade(self, symbol: str, shares: int, side: OrderSide,
                       decision_price: float = 0,
                       spread_bps: float = 0) -> PaperTradeResult:
        """Execute a trade via broker adapter."""
        check_kill_switch()
        try:
            expected_price = self.get_current_price(symbol) or decision_price

            order = OrderRequest(
                symbol=symbol, qty=shares, side=side,
                order_type=OrderType.MARKET, time_in_force="day",
            )
            result = self.broker.submit_order(order)
            order_id = result.order_id

            # Wait for fill
            if hasattr(self.broker, 'wait_for_fill'):
                filled_price = self.broker.wait_for_fill(order_id) or expected_price
            elif result.status == OrderStatus.FILLED:
                filled_price = result.filled_avg_price or expected_price
            else:
                filled_price = expected_price

            side_str = "buy" if side == OrderSide.BUY else "sell"
            self._record_fill(symbol, side_str, order_id,
                              decision_price, expected_price,
                              filled_price, shares, spread_bps)

            return PaperTradeResult(
                success=True, order_id=order_id,
                filled_qty=shares, filled_price=filled_price,
                timestamp=datetime.now().isoformat(),
            )
        except Exception as e:
            return PaperTradeResult(
                success=False, error_message=str(e),
                timestamp=datetime.now().isoformat(),
            )

    def execute_long_trade(self, symbol: str, shares: int,
                           decision_price: float = 0,
                           spread_bps: float = 0) -> PaperTradeResult:
        check_kill_switch()
        return self._execute_trade(symbol, shares, OrderSide.BUY,
                                   decision_price, spread_bps)

    def execute_short_trade(self, symbol: str, shares: int,
                            decision_price: float = 0,
                            spread_bps: float = 0) -> PaperTradeResult:
        check_kill_switch()
        return self._execute_trade(symbol, shares, OrderSide.SELL,
                                   decision_price, spread_bps)

    def _cancel_existing_stops(self, symbol: str):
        """Cancel any existing stop orders for this symbol before submitting new one."""
        try:
            # Get open orders from Alpaca and cancel stops for this symbol
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            open_orders = self.broker._trading.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol]))
            for order in open_orders:
                if "stop" in str(order.type).lower():
                    self.broker.cancel_order(str(order.id))
                    print(f"  Cancelled existing stop order {str(order.id)[:8]} for {symbol}")
        except Exception as e:
            logging.warning(f"Failed to cancel existing stops for {symbol}: {e}")

    def flatten_position(self, symbol: str, reason: str = "direction reversal") -> bool:
        """Close any existing position in a symbol before entering opposite direction."""
        positions = self.get_current_positions()
        if symbol not in positions or positions[symbol] == 0:
            return True
        qty = positions[symbol]
        direction = "LONG" if qty > 0 else "SHORT"
        print(f"  Closing existing {direction} {abs(qty):.0f} shares of {symbol} ({reason})")
        try:
            self._cancel_existing_stops(symbol)
            self.broker.close_position(symbol)
            return True
        except Exception as e:
            print(f"  Failed to flatten {symbol}: {e}")
            return False

    def set_stop_loss(self, symbol: str, stop_price: float) -> Optional[str]:
        check_kill_switch()
        try:
            positions = self.get_current_positions()
            if symbol not in positions or positions[symbol] == 0:
                print(f"No position found for {symbol} to set stop loss")
                return None

            # Bug 5 fix: Cancel existing stop orders before submitting new one
            self._cancel_existing_stops(symbol)

            qty = abs(positions[symbol])
            side = OrderSide.SELL if positions[symbol] > 0 else OrderSide.BUY

            order = OrderRequest(
                symbol=symbol, qty=qty, side=side,
                order_type=OrderType.STOP, stop_price=stop_price,
                time_in_force="gtc",
            )
            result = self.broker.submit_order(order)
            print(f"✅ Stop loss set for {symbol} at ${stop_price:.2f}")
            return result.order_id
        except Exception as e:
            print(f"Error setting stop loss: {e}")
            return None


def execute_master_decision(decision: MasterTradingDecision) -> Dict:
    """Execute a Master Orchestrator decision in paper trading."""
    check_kill_switch()
    manager = PaperTradingManager()
    symbol = decision.symbol

    print(f"\n🔄 EXECUTING PAPER TRADE: {decision.final_decision}")
    print(f"Symbol: {symbol}")
    print(f"Position Size: {decision.position_size}%")

    # Get current price
    current_price = manager.get_current_price(symbol)
    if not current_price:
        return {
            "success": False,
            "error": f"Could not get current price for {symbol}"
        }

    print(f"Current Price: ${current_price:.2f}")

    # Estimate spread (informational + circuit-breaker only — no sizing adjustment)
    spread = estimate_spread(symbol)
    spread_bps = spread["spread_bps"]
    if spread_bps > 0:
        print(f"Spread: {spread_bps:.1f} bps (bid ${spread['bid']:.2f} / ask ${spread['ask']:.2f})")

    if spread_bps > SPREAD_REJECT_BPS:
        logging.critical(
            f"[SPREAD REJECT] {symbol} spread {spread_bps:.0f}bps > {SPREAD_REJECT_BPS}bps "
            f"circuit-breaker — trade rejected (flash-crash / halt protection)"
        )
        return {
            "success": False,
            "error": f"Spread {spread_bps:.0f}bps exceeds {SPREAD_REJECT_BPS}bps circuit-breaker",
            "symbol": symbol,
            "spread_bps": spread_bps,
            "current_price": current_price,
            "portfolio_value": manager.get_portfolio_value(),
            "timestamp": datetime.now().isoformat(),
        }

    position_pct = decision.position_size

    # Handle different decision types
    execution_result = {}

    # Bug 4 fix: Flatten opposite position before entering new direction
    if decision.final_decision.upper().startswith("LONG"):
        positions = manager.get_current_positions()
        if symbol in positions and positions[symbol] < 0:
            manager.flatten_position(symbol, "reversing SHORT → LONG")
    elif decision.final_decision.upper().startswith("SHORT"):
        positions = manager.get_current_positions()
        if symbol in positions and positions[symbol] > 0:
            manager.flatten_position(symbol, "reversing LONG → SHORT")

    if decision.final_decision.upper().startswith("LONG") and position_pct > 0:
        shares = manager.calculate_share_quantity(symbol, position_pct, current_price)
        print(f"Buying {shares} shares...")

        trade_result = manager.execute_long_trade(
            symbol, shares, decision_price=current_price, spread_bps=spread_bps)
        execution_result['trade'] = trade_result

        if trade_result.success:
            print(f"✅ Long trade executed: {shares} shares of {symbol}")
            if trade_result.filled_price:
                print(f"   Fill: ${trade_result.filled_price:.2f}")
            try:
                from alert_manager import alert_trade_executed
                alert_trade_executed(symbol, "LONG", shares,
                                     trade_result.filled_price or current_price, spread_bps)
            except Exception:
                pass
            if decision.stop_loss:
                stop_order_id = manager.set_stop_loss(symbol, decision.stop_loss)
                execution_result['stop_loss_order_id'] = stop_order_id
        else:
            print(f"❌ Trade failed: {trade_result.error_message}")

    elif decision.final_decision.upper().startswith("SHORT") and position_pct > 0:
        shares = manager.calculate_share_quantity(symbol, position_pct, current_price)
        print(f"Shorting {shares} shares...")

        trade_result = manager.execute_short_trade(
            symbol, shares, decision_price=current_price, spread_bps=spread_bps)
        execution_result['trade'] = trade_result

        if trade_result.success:
            print(f"✅ Short trade executed: {shares} shares of {symbol}")
            if trade_result.filled_price:
                print(f"   Fill: ${trade_result.filled_price:.2f}")
            try:
                from alert_manager import alert_trade_executed
                alert_trade_executed(symbol, "SHORT", shares,
                                     trade_result.filled_price or current_price, spread_bps)
            except Exception:
                pass
            if decision.stop_loss:
                stop_order_id = manager.set_stop_loss(symbol, decision.stop_loss)
                execution_result['stop_loss_order_id'] = stop_order_id
        else:
            print(f"❌ Trade failed: {trade_result.error_message}")

    elif decision.final_decision.upper() == "HOLD":
        print("📊 Decision: HOLD - No trade executed")
        execution_result = {
            "success": True,
            "action": "HOLD",
            "reason": "Master Orchestrator recommended no position change"
        }

    else:
        execution_result = {
            "success": False,
            "error": f"Unknown decision type: {decision.final_decision}"
        }

    execution_result['current_price'] = current_price
    execution_result['spread_bps'] = spread_bps
    execution_result['portfolio_value'] = manager.get_portfolio_value()
    execution_result['timestamp'] = datetime.now().isoformat()

    return execution_result


def run_live_paper_trading(symbol: str):
    """Run complete analysis and execute in paper trading."""
    print(f"\n{'🚀 LIVE PAPER TRADING PIPELINE':=^70}")
    print(f"Symbol: {symbol}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    # Step 1: Run complete trading analysis
    decision = run_complete_trading_analysis(symbol)
    
    # Step 2: Execute the decision in paper trading
    execution_result = execute_master_decision(decision)
    
    # Step 3: Display results
    print(f"\n📈 EXECUTION SUMMARY")
    print(f"Portfolio Value: ${execution_result.get('portfolio_value', 0):,.2f}")
    if execution_result.get('success'):
        if 'trade' in execution_result:
            trade = execution_result['trade']
            print(f"Trade Status: {'SUCCESS' if trade.success else 'FAILED'}")
            if trade.success:
                print(f"Order ID: {trade.order_id}")
                print(f"Shares: {trade.filled_qty}")
        else:
            print(f"Action: {execution_result.get('action', 'UNKNOWN')}")
    else:
        print(f"❌ Error: {execution_result.get('error', 'Unknown error')}")
    
    return decision, execution_result


def slippage_report(since_days: int = 30):
    """Summarize fill quality from logs/fill_records.jsonl."""
    cutoff = datetime.now() - timedelta(days=since_days)
    records = []

    if not FILL_LOG.exists():
        print("No fill records yet.")
        return

    with open(FILL_LOG) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if datetime.fromisoformat(r["timestamp"]) > cutoff:
                records.append(r)

    if not records:
        print(f"No fills in last {since_days} days.")
        return

    slippages = [r["slippage_bps"] for r in records]
    spreads = [r["spread_estimate_bps"] for r in records]
    costs = [r["total_cost_bps"] for r in records]

    print(f"Fill records: {len(records)} trades in last {since_days} days")
    print(f"Avg slippage: {sum(slippages)/len(slippages):.1f} bps")
    print(f"Avg spread:   {sum(spreads)/len(spreads):.1f} bps")
    print(f"Avg total cost: {sum(costs)/len(costs):.1f} bps")
    print(f"Max slippage: {max(slippages):.1f} bps")
    sorted_slip = sorted(slippages)
    print(f"P95 slippage: {sorted_slip[int(len(sorted_slip)*0.95)]:.1f} bps")

    by_symbol: Dict[str, list] = {}
    for r in records:
        by_symbol.setdefault(r["symbol"], []).append(r["total_cost_bps"])
    print(f"\nPer-symbol avg cost:")
    for sym, costs_list in sorted(by_symbol.items()):
        print(f"  {sym}: {sum(costs_list)/len(costs_list):.1f} bps ({len(costs_list)} fills)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "slippage":
        slippage_report()
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        # Show current paper trading status
        manager = PaperTradingManager()
        print(f"\n📊 PAPER TRADING ACCOUNT STATUS")
        print(f"Portfolio Value: ${manager.get_portfolio_value():,.2f}")
        
        positions = manager.get_current_positions()
        if positions:
            print(f"\nCurrent Positions:")
            for symbol, qty in positions.items():
                direction = "LONG" if qty > 0 else "SHORT"
                print(f"  {symbol}: {qty:,.0f} shares ({direction})")
        else:
            print("\nNo current positions")
            
    else:
        # Run live paper trading pipeline
        symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"
        decision, execution = run_live_paper_trading(symbol)