"""SimPortfolio — backtest replacement for the live Alpaca portfolio fetch.

When master_orchestrator runs in backtest mode (state["as_of_date"] set),
risk_gatekeeper.evaluate_trade_risk must NOT query live Alpaca — the live
$100k paper account doesn't reflect what the simulated bot would have held
at a historical decision point. This module produces a synthesized
portfolio-context dict in the exact shape risk_gatekeeper expects, derived
from a stateful position tracker that ingests fills as they occur during
the backtest sweep.

Typical use in a future backtest runner:

    sim = SimPortfolio(starting_cash=100_000)
    for trading_day in dates:
        prices = load_eod_prices(trading_day)  # dict[symbol -> float]
        for symbol in active_universe:
            ctx = sim.to_risk_context(symbol, as_of_date=trading_day,
                                      prices=prices)
            decision = run_complete_trading_analysis(
                symbol,
                state_overrides={"as_of_date": str(trading_day),
                                 "portfolio_context": ctx})
            for fill in decision_to_fills(decision, prices):
                sim.apply_fill(fill)
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# Mirrors risk_gatekeeper.COMMODITY_SYMBOLS so the synthesized context's
# equity/commodity split matches what get_live_portfolio_context would produce.
# Kept inline to avoid a circular import; the canonical list lives in
# risk_gatekeeper.py.
COMMODITY_SYMBOLS = {
    # Oil
    "USO", "BNO", "DBO", "UCO", "SCO", "OIH", "XLE", "XOP",
    # Natural gas
    "UNG", "BOIL", "KOLD", "UNL", "FCG",
    # Precious metals
    "GLD", "IAU", "AAAU", "PHYS", "SLV", "PSLV",
    # Miners
    "GDX", "GDXJ", "NUGT", "DUST", "JNUG", "JDST",
    # Broad
    "DBC", "GSG", "USCI",
}


@dataclass
class Fill:
    """One simulated execution. Direction sign is encoded in qty:
    positive qty = LONG buy, negative qty = SHORT sell."""
    symbol: str
    qty: float           # signed: +open long / -open short / +cover short / -close long
    price: float
    timestamp: datetime
    side: str            # 'buy' or 'sell' — informational; qty sign is authoritative


@dataclass
class Position:
    qty: float           # signed; positive = LONG, negative = SHORT
    avg_price: float
    opened_at: datetime


@dataclass
class SimPortfolio:
    """Stateful position tracker for backtest. Apply fills chronologically;
    query portfolio state at any time via to_risk_context()."""

    starting_cash: float = 100_000.0
    cash: float = field(init=False)
    positions: dict = field(default_factory=dict)   # symbol -> Position
    fills: list = field(default_factory=list)       # chronological audit trail

    def __post_init__(self):
        self.cash = self.starting_cash

    # ── Fill application ─────────────────────────────────────────────────

    def apply_fill(self, fill: Fill) -> None:
        """Apply a fill to portfolio state. Handles opening, adding,
        partial-close, full-close, and reversal correctly via signed qty."""
        sym = fill.symbol.upper()
        existing = self.positions.get(sym)

        # Cash impact: signed qty × price. BUY (+qty) reduces cash, SELL (-qty)
        # adds cash. For SHORT-OPEN (-qty), we receive proceeds (cash increases).
        # For SHORT-COVER (+qty), we pay (cash decreases).
        self.cash -= fill.qty * fill.price

        if existing is None or existing.qty == 0:
            self.positions[sym] = Position(qty=fill.qty, avg_price=fill.price,
                                            opened_at=fill.timestamp)
        else:
            new_qty = existing.qty + fill.qty
            if new_qty == 0:
                # Fully closed
                del self.positions[sym]
            elif (existing.qty > 0) == (new_qty > 0):
                # Same direction (adding or partially closing without reversing)
                if abs(new_qty) > abs(existing.qty):
                    # Adding — weighted average cost
                    total_cost = existing.qty * existing.avg_price + fill.qty * fill.price
                    new_avg = total_cost / new_qty
                    self.positions[sym] = Position(qty=new_qty, avg_price=new_avg,
                                                    opened_at=existing.opened_at)
                else:
                    # Partial close — avg_price unchanged
                    self.positions[sym] = Position(qty=new_qty, avg_price=existing.avg_price,
                                                    opened_at=existing.opened_at)
            else:
                # Direction reversal — close old, open new at fill price
                self.positions[sym] = Position(qty=new_qty, avg_price=fill.price,
                                                opened_at=fill.timestamp)

        self.fills.append(fill)

    # ── Portfolio snapshot ───────────────────────────────────────────────

    def market_value(self, prices: dict) -> float:
        """Sum of |qty| × current_price across positions, using `prices`
        dict[symbol → price]. Missing prices contribute the position's
        avg_price as a fallback (so a price gap doesn't crash the cap math)."""
        total = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym.upper(), pos.avg_price)
            total += abs(pos.qty) * px
        return total

    def total_portfolio_value(self, prices: dict) -> float:
        """Cash + market value of open positions. Equivalent to Alpaca's
        account.portfolio_value at this moment."""
        return self.cash + self.market_value(prices)

    def to_risk_context(self, symbol: str, as_of_date: Optional[date] = None,
                        prices: Optional[dict] = None) -> dict:
        """Produce the dict shape risk_gatekeeper.evaluate_trade_risk expects,
        matching get_live_portfolio_context's output keys exactly.

        `prices` is a dict[symbol → float] snapshot for marking positions
        to market. If absent or missing a symbol, the position's avg_price
        is used as fallback (conservative for risk).
        """
        prices = prices or {}
        total_value = self.total_portfolio_value(prices)
        cash_pct = self.cash / total_value if total_value > 0 else 1.0

        equity_exposure = 0.0
        commodity_exposure = 0.0
        open_symbols = []
        symbol_current_position = 0.0

        for sym, pos in self.positions.items():
            px = prices.get(sym.upper(), pos.avg_price)
            market_value = abs(pos.qty) * px
            pct = market_value / total_value if total_value > 0 else 0.0
            open_symbols.append(sym)

            if sym.upper() in COMMODITY_SYMBOLS:
                commodity_exposure += pct
            else:
                equity_exposure += pct

            if sym.upper() == symbol.upper():
                symbol_current_position = pct

        return {
            "total_portfolio_value": round(total_value, 2),
            "current_equity_exposure": round(equity_exposure, 4),
            "current_commodity_exposure": round(commodity_exposure, 4),
            "current_cash": round(cash_pct, 4),
            "ytd_drawdown": 0.0,  # Future work: compute from fills history vs starting_cash
            "open_positions": open_symbols,
            f"{symbol.lower()}_current_position": round(symbol_current_position, 4),
            "data_source": "sim_portfolio",
            "as_of_date": str(as_of_date) if as_of_date else None,
        }
