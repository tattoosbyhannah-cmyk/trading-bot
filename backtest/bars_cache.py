"""
L1 Alpaca Bars Cache — JSON-backed cache for historical daily bars.

Backtest runs repeatedly call StockBarsRequest for the same windows. This
cache memoizes the *raw bar list* (open/high/low/close/volume + date) keyed
on (symbol, start_iso, end_iso, timeframe, feed). One file per key under
backtest/cache/bars/.

Why JSON not parquet: bars are small (45 rows × 6 fields), parquet brings
no win and adds a pyarrow dependency we don't have. Switch later if cache
size becomes a problem.

Live mode is never cached — only call this when backtest mode is active or
an as_of_date is set. Cached files are never invalidated automatically;
delete the cache dir to force a refetch.
"""

import json
import os
from datetime import datetime, date as _date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV if _ENV.exists() else None)


CACHE_DIR = Path(__file__).resolve().parent / "cache" / "bars"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(symbol: str, start: datetime, end: datetime,
               timeframe: str, feed: str) -> Path:
    s = start.date().isoformat() if hasattr(start, "date") else str(start)
    e = end.date().isoformat() if hasattr(end, "date") else str(end)
    return CACHE_DIR / f"{symbol.upper()}_{s}_{e}_{timeframe}_{feed}.json"


def _load(path: Path) -> Optional[list]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save(path: Path, bars: list):
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(bars, f)
        tmp.rename(path)
    except Exception:
        pass


def fetch_bars_cached(symbol: str, start: datetime, end: datetime,
                       timeframe: str = "Day", feed: str = "iex",
                       as_of: Optional[_date] = None) -> list:
    """Return raw bar list for the window, hitting cache first.

    Args:
        symbol: ticker
        start, end: datetime window — end is inclusive end-of-day in caller's
                    construction (Alpaca exclusive-end is handled outside).
        timeframe: 'Day' (only Day is wired today).
        feed: 'iex' (default; matches live data layer).
        as_of: optional date to defensively post-filter bars to date <= as_of.

    Returns the same list shape produced by technical_analyst.fetch_bars:
        [{"date": "YYYY-MM-DD", "o": ..., "h": ..., "l": ..., "c": ..., "v": ...}]
    """
    key = _cache_key(symbol, start, end, timeframe, feed)
    cached = _load(key)
    if cached is not None:
        if as_of:
            return [b for b in cached if b["date"] <= as_of.isoformat()]
        return cached

    client = StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )
    tf = TimeFrame.Day if timeframe == "Day" else TimeFrame.Day
    req = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=tf,
        start=start,
        end=end,
        feed=feed,
    )
    resp = client.get_stock_bars(req)
    bars = resp.data.get(symbol, [])
    bar_list = [
        {"date": str(b.timestamp.date()), "o": b.open, "h": b.high,
         "l": b.low, "c": b.close, "v": b.volume}
        for b in bars
    ]
    _save(key, bar_list)
    if as_of:
        return [b for b in bar_list if b["date"] <= as_of.isoformat()]
    return bar_list


def clear_cache():
    """Drop all cached bar files. Used when historical data corrections
    invalidate the cache (e.g. Alpaca republishes a corrected bar)."""
    for f in CACHE_DIR.glob("*.json"):
        try:
            f.unlink()
        except Exception:
            pass
