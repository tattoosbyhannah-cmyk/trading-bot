"""
Decision → Fill converter.

Maps a MasterTradingDecision (LONG/SHORT/HOLD) into a list[Fill] applied
to the SimPortfolio. For backtest fidelity, decisions execute at the
next session's open — but we don't pull intraday bars (cost-prohibitive
for a multi-week backtest), so we model fills at the as_of_date close
itself. That is a known simplification: live, the order would queue at
market close and fill at next-open; here we approximate close-to-close.

Position-sizing math mirrors execute_trade.py: pct_of_equity × equity ÷ price.
HOLD produces zero fills. A reversal (LONG when SHORT is open, or vice
versa) produces two fills — close the prior, open the new.
"""

from datetime import datetime
from typing import Optional

from backtest.portfolio_sim import Fill, SimPortfolio


def decision_to_fills(decision, sim: SimPortfolio, as_of_date,
                       price: Optional[float] = None) -> list:
    """Convert a MasterTradingDecision to fills applied at the day's close.

    Args:
        decision: MasterTradingDecision (has final_decision, position_size, symbol,
                  entry_price).
        sim: the SimPortfolio — used to read current position and total value.
        as_of_date: datetime.date for the fill timestamp.
        price: optional override for the fill price. Defaults to decision.entry_price.

    Returns a list of Fill objects (may be empty for HOLD or zero-size).
    """
    symbol = (getattr(decision, "symbol", "") or "").upper()
    side = (getattr(decision, "final_decision", "") or "").upper()
    if side not in ("LONG", "SHORT"):
        return []

    fill_price = price or getattr(decision, "entry_price", None)
    if not fill_price or fill_price <= 0:
        return []

    pct = getattr(decision, "position_size", 0) or 0
    if pct <= 0:
        return []

    ts = datetime.combine(as_of_date, datetime.min.time().replace(hour=16))

    # Target qty: signed. LONG = +, SHORT = -.
    total_value = sim.total_portfolio_value(prices={symbol: fill_price})
    target_notional = (pct / 100.0) * total_value
    target_qty = target_notional / fill_price
    if side == "SHORT":
        target_qty = -target_qty

    current = sim.positions.get(symbol)
    current_qty = current.qty if current else 0.0

    delta = target_qty - current_qty
    if abs(delta) < 1e-6:
        return []

    fills = []
    # If reversing (sign change with non-zero existing), emit one fill that
    # crosses zero — apply_fill handles full close + reopen via the
    # direction-reversal branch.
    if current_qty != 0 and (current_qty > 0) != (target_qty > 0):
        # Reversal: first close, then open at the same price/timestamp
        close_qty = -current_qty
        fills.append(Fill(symbol=symbol, qty=close_qty, price=fill_price,
                          timestamp=ts, side=("sell" if close_qty < 0 else "buy")))
        open_qty = target_qty
        fills.append(Fill(symbol=symbol, qty=open_qty, price=fill_price,
                          timestamp=ts, side=("buy" if open_qty > 0 else "sell")))
    else:
        fills.append(Fill(symbol=symbol, qty=delta, price=fill_price,
                          timestamp=ts, side=("buy" if delta > 0 else "sell")))

    return fills
