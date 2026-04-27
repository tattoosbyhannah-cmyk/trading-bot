"""
Base Broker — abstract interface for all broker adapters.

Adding a new broker = implement this interface in a new file.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"
    idempotency_key: Optional[str] = None


@dataclass
class OrderResult:
    order_id: str
    broker_order_id: str
    status: OrderStatus
    filled_qty: float = 0
    filled_avg_price: float = 0
    broker: str = ""
    raw_response: Optional[dict] = field(default=None)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    side: str  # "long" or "short"


class BaseBroker(ABC):
    """Abstract broker interface. All broker-specific code goes in subclasses."""

    @abstractmethod
    def get_account(self) -> dict:
        """Return account info: equity, cash, buying_power."""
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a single symbol, or None if no position."""
        ...

    @abstractmethod
    def get_all_positions(self) -> list[Position]:
        """Get all open positions."""
        ...

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order. Returns result with order_id and status."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID. Returns True if successfully cancelled."""
        ...

    @abstractmethod
    def replace_order(self, order_id: str, qty: float = None,
                      limit_price: float = None,
                      stop_price: float = None) -> OrderResult:
        """Modify an existing order (for ratcheting stops, etc.)."""
        ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Check current status of an order."""
        ...

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Get latest trade price for a symbol."""
        ...

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> dict:
        """Get latest bid/ask quote. Returns {bid, ask, mid, spread_bps}."""
        ...

    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str, limit: int) -> list:
        """Get historical bars. timeframe: '1Min', '1Day', etc."""
        ...

    @abstractmethod
    def close_position(self, symbol: str) -> OrderResult:
        """Close an entire position in a symbol."""
        ...

    @abstractmethod
    def close_all_positions(self) -> list[OrderResult]:
        """Close all open positions."""
        ...

    @abstractmethod
    def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        ...
