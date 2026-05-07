"""
Alpaca Broker Adapter — implements BaseBroker for Alpaca paper/live trading.

All Alpaca-specific imports and API calls live here.
"""

import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(_ENV if _ENV.exists() else None)

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest as AlpacaMarketOrder,
    LimitOrderRequest as AlpacaLimitOrder,
    StopOrderRequest as AlpacaStopOrder,
    ReplaceOrderRequest,
)
from alpaca.trading.enums import (
    OrderSide as AlpacaSide,
    TimeInForce as AlpacaTIF,
)
from alpaca.common.exceptions import APIError
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import (
    StockLatestTradeRequest,
    StockLatestQuoteRequest,
    StockBarsRequest,
)
from alpaca.data.timeframe import TimeFrame

from brokers.base_broker import (
    BaseBroker, OrderRequest, OrderResult, Position,
    OrderSide, OrderType, OrderStatus,
)


# Map our enums to Alpaca's
_SIDE_MAP = {
    OrderSide.BUY: AlpacaSide.BUY,
    OrderSide.SELL: AlpacaSide.SELL,
}

_TIF_MAP = {
    "day": AlpacaTIF.DAY,
    "gtc": AlpacaTIF.GTC,
    "ioc": AlpacaTIF.IOC,
}

_STATUS_MAP = {
    "new": OrderStatus.PENDING,
    "accepted": OrderStatus.ACCEPTED,
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "pending_new": OrderStatus.PENDING,
}


class AlpacaBroker(BaseBroker):

    def __init__(self, paper: bool = True):
        self._trading = TradingClient(
            api_key=os.getenv("ALPACA_API_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            paper=paper,
        )
        self._data = StockHistoricalDataClient(
            api_key=os.getenv("ALPACA_API_KEY_ID"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
        )

    def get_account(self) -> dict:
        acct = self._trading.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
        }

    def get_position(self, symbol: str) -> Optional[Position]:
        try:
            p = self._trading.get_open_position(symbol)
            qty = float(p.qty)
            return Position(
                symbol=p.symbol,
                qty=qty,
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
                side="long" if qty > 0 else "short",
            )
        except APIError:
            return None

    def get_all_positions(self) -> list[Position]:
        positions = []
        for p in self._trading.get_all_positions():
            qty = float(p.qty)
            positions.append(Position(
                symbol=p.symbol,
                qty=qty,
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
                side="long" if qty > 0 else "short",
            ))
        return positions

    def submit_order(self, order: OrderRequest) -> OrderResult:
        side = _SIDE_MAP[order.side]
        tif = _TIF_MAP.get(order.time_in_force, AlpacaTIF.DAY)

        if order.order_type == OrderType.MARKET:
            req = AlpacaMarketOrder(
                symbol=order.symbol, qty=order.qty,
                side=side, time_in_force=tif,
            )
        elif order.order_type == OrderType.LIMIT:
            req = AlpacaLimitOrder(
                symbol=order.symbol, qty=order.qty,
                side=side, time_in_force=tif,
                limit_price=order.limit_price,
                extended_hours=order.extended_hours,
            )
        elif order.order_type == OrderType.STOP:
            req = AlpacaStopOrder(
                symbol=order.symbol, qty=order.qty,
                side=side, time_in_force=tif,
                stop_price=order.stop_price,
            )
        else:
            raise ValueError(f"Order type {order.order_type} not implemented")

        resp = self._trading.submit_order(req)
        return self._to_result(resp)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._trading.cancel_order_by_id(order_id)
            return True
        except APIError:
            return False

    def get_open_orders(self, symbol: Optional[str] = None) -> list:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[symbol] if symbol else None,
            limit=100,
        )
        return self._trading.get_orders(filter=req)

    def get_asset(self, symbol: str):
        return self._trading.get_asset(symbol)

    def replace_order(self, order_id: str, qty: float = None,
                      limit_price: float = None,
                      stop_price: float = None) -> OrderResult:
        req = ReplaceOrderRequest(
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        resp = self._trading.replace_order_by_id(order_id, req)
        return self._to_result(resp)

    def get_order_status(self, order_id: str) -> OrderResult:
        resp = self._trading.get_order_by_id(order_id)
        return self._to_result(resp)

    def get_current_price(self, symbol: str) -> float:
        trade = self._data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol))
        return float(trade[symbol].price)

    def get_latest_quote(self, symbol: str) -> dict:
        # Use Alpaca's DELAYED_SIP feed (15-min lag, free on paper subscription)
        # rather than the IEX-only default. IEX is one of ~16 NMS venues and posts
        # systematically wide cosmetic quotes for some ETFs (USO routinely shows
        # 200-700 bps on IEX vs 3-10 bps on the consolidated tape). The 200 bps
        # circuit-breaker exists for flash-crash / halt detection — events that
        # persist for many minutes — so 15-min-lagged consolidated NBBO is the
        # right input. See docs/uso_spread_investigation.md for the audit.
        from alpaca.data.enums import DataFeed
        quote = self._data.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.DELAYED_SIP))
        q = quote[symbol]
        mid = (q.bid_price + q.ask_price) / 2
        spread_bps = ((q.ask_price - q.bid_price) / mid * 10000) if mid > 0 else 0
        return {
            "bid": q.bid_price,
            "ask": q.ask_price,
            "mid": mid,
            "spread_bps": spread_bps,
        }

    def get_bars(self, symbol: str, timeframe: str, limit: int) -> list:
        tf_map = {
            "1Min": TimeFrame.Minute,
            "1Day": TimeFrame.Day,
            "1Hour": TimeFrame.Hour,
        }
        tf = tf_map.get(timeframe, TimeFrame.Day)
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=limit * 2)  # Buffer for weekends
        req = StockBarsRequest(
            symbol_or_symbols=[symbol], timeframe=tf,
            start=start, end=end,
        )
        resp = self._data.get_stock_bars(req)
        bars = []
        for b in resp.data.get(symbol, [])[-limit:]:
            bars.append({
                "timestamp": b.timestamp.isoformat(),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": int(b.volume),
                "vwap": float(b.vwap),
            })
        return bars

    def close_position(self, symbol: str) -> OrderResult:
        try:
            resp = self._trading.close_position(symbol)
            return self._to_result(resp)
        except APIError as e:
            return OrderResult(
                order_id="", broker_order_id="",
                status=OrderStatus.REJECTED, broker="alpaca",
                raw_response={"error": str(e)},
            )

    def close_all_positions(self) -> list[OrderResult]:
        results = []
        try:
            self._trading.close_all_positions(cancel_orders=True)
        except APIError:
            pass
        return results

    def cancel_all_orders(self) -> bool:
        try:
            self._trading.cancel_orders()
            return True
        except APIError:
            return False

    def wait_for_fill(self, order_id: str, timeout_sec: int = 30) -> Optional[float]:
        """Alpaca-specific: poll until filled and return fill price."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            result = self.get_order_status(order_id)
            if result.status == OrderStatus.FILLED and result.filled_avg_price > 0:
                return result.filled_avg_price
            time.sleep(0.5)
        return None

    def _to_result(self, resp) -> OrderResult:
        status_str = str(resp.status).lower().replace("orderstatus.", "")
        return OrderResult(
            order_id=str(resp.id),
            broker_order_id=str(resp.id),
            status=_STATUS_MAP.get(status_str, OrderStatus.PENDING),
            filled_qty=float(resp.filled_qty or 0),
            filled_avg_price=float(resp.filled_avg_price or 0),
            broker="alpaca",
        )
