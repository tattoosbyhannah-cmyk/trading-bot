"""
EOD price loader — daily closing prices for marking the SimPortfolio.

Uses the same L1 bars cache as technical_analyst so the runner doesn't
re-hit Alpaca for prices it already pulled during analysis. Returns a
dict[symbol → close] for a given trading day.
"""

from datetime import datetime, timedelta, date as _date
from typing import Iterable, Optional

from backtest.bars_cache import fetch_bars_cached


def load_eod_prices(symbols: Iterable[str], as_of_date: _date,
                     lookback_days: int = 7) -> dict:
    """Return {symbol → close_price} for the most recent bar on or before as_of.

    lookback_days widens the window to handle holidays/weekends — we ask for
    a small window and take the last bar with date <= as_of.
    """
    end_dt = datetime.combine(as_of_date, datetime.max.time())
    start_dt = datetime.combine(as_of_date, datetime.min.time()) - timedelta(days=lookback_days)

    out = {}
    for sym in symbols:
        sym = sym.upper()
        try:
            bars = fetch_bars_cached(
                sym, start_dt, end_dt,
                timeframe="Day", feed="iex", as_of=as_of_date,
            )
        except Exception:
            continue
        if bars:
            out[sym] = float(bars[-1]["c"])
    return out


def load_eod_price(symbol: str, as_of_date: _date,
                     lookback_days: int = 7) -> Optional[float]:
    """Single-symbol convenience wrapper."""
    p = load_eod_prices([symbol], as_of_date, lookback_days=lookback_days)
    return p.get(symbol.upper())
