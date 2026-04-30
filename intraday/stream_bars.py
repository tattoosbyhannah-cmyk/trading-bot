#!/usr/bin/env python3
"""
Intraday Bar Streamer — connects to Alpaca real-time websocket and streams
1-minute bars for symbols listed in the daily playbook.

Maintains a rolling 60-bar window (1 hour) per symbol with running VWAP.
Provides get_bars(symbol) and get_vwap(symbol) for the signal engine.

Usage:
    python intraday/stream_bars.py              # stream and print bars
    python intraday/stream_bars.py --quiet      # stream without printing

Exits cleanly if:
  - Market is closed (outside 9:30 AM - 4:00 PM ET)
  - Kill switch is engaged
  - SIGINT / SIGTERM received
"""

import asyncio
import json
import os
import signal
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed

# ── Config ────────────────────────────────────────────────────────────────────

TRADING_BOT_DIR = Path(__file__).resolve().parent.parent
KILL_SWITCH_FILE = TRADING_BOT_DIR / "KILL_SWITCH"
PLAYBOOK_FILE = TRADING_BOT_DIR / "playbook" / "daily_playbook.json"
WINDOW_SIZE = 60  # bars per symbol (60 x 1-min = 1 hour)
ET = ZoneInfo("America/New_York")
MARKET_OPEN_HOUR, MARKET_OPEN_MIN = 9, 30
MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN = 16, 0


# ── State ─────────────────────────────────────────────────────────────────────

_bar_windows: dict[str, deque] = {}
_vwap_state: dict[str, dict] = {}  # cumulative volume-weighted price
_callbacks: list[Callable] = []
_stream: Optional[StockDataStream] = None
_shutdown = False


# ── Log helper: ISO timestamp prefix on every line ──────────────────────────

def _log(*args, **kwargs):
    """Wraps print() with an ISO-timestamp prefix in America/New_York for service log correlation.
    A leading '\\n' in the first arg becomes a real blank line so the timestamp lands on the content."""
    if args and isinstance(args[0], str) and args[0].startswith("\n"):
        print()  # noqa: T201 — separator blank line; timestamp lands on the next call
        args = (args[0].lstrip("\n"),) + args[1:]
    ts = datetime.now(ET).strftime("%Y-%m-%dT%H:%M:%S%z")
    print(ts, *args, **kwargs)  # noqa: T201 — intentional print inside log helper


# ── Public API ────────────────────────────────────────────────────────────────

def get_bars(symbol: str) -> pd.DataFrame:
    """Return the rolling bar window for a symbol as a DataFrame."""
    window = _bar_windows.get(symbol, deque())
    if not window:
        return pd.DataFrame()
    return pd.DataFrame(list(window))


def get_vwap(symbol: str) -> float:
    """Return current session VWAP for a symbol."""
    state = _vwap_state.get(symbol)
    if not state or state["cum_volume"] == 0:
        return 0.0
    return state["cum_vwap_volume"] / state["cum_volume"]


def register_callback(fn: Callable):
    """Register a function to be called on each new bar.

    Signature: fn(symbol: str, bar: dict)
    """
    _callbacks.append(fn)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_market_hours() -> bool:
    now = datetime.now(ET)
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN,
                              second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN,
                               second=0, microsecond=0)
    # Also check weekday (Mon=0 .. Fri=4)
    if now.weekday() > 4:
        return False
    return market_open <= now <= market_close


def _check_kill_switch() -> bool:
    return KILL_SWITCH_FILE.exists()


def _load_playbook_symbols() -> list[str]:
    """Read daily_playbook.json and return symbols where allow_scalping is true."""
    if not PLAYBOOK_FILE.exists():
        _log(f"[STREAM] No playbook found at {PLAYBOOK_FILE}")
        return []
    try:
        playbook = json.loads(PLAYBOOK_FILE.read_text())
        symbols = []
        for sym, entry in playbook.get("symbols", {}).items():
            if entry.get("allow_scalping", False):
                symbols.append(sym)
        return symbols
    except Exception as e:
        _log(f"[STREAM] Error reading playbook: {e}")
        return []


def _bar_to_dict(bar) -> dict:
    return {
        "timestamp": bar.timestamp.isoformat(),
        "symbol": bar.symbol,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
        "trade_count": int(bar.trade_count),
        "vwap": float(bar.vwap),
    }


# ── Bar Handler ───────────────────────────────────────────────────────────────

async def _on_bar(bar):
    """Called by Alpaca websocket on each 1-minute bar."""
    global _shutdown

    if _check_kill_switch():
        _log("[STREAM] Kill switch engaged — disconnecting")
        _shutdown = True
        return

    symbol = bar.symbol
    bar_dict = _bar_to_dict(bar)

    # Update rolling window
    if symbol not in _bar_windows:
        _bar_windows[symbol] = deque(maxlen=WINDOW_SIZE)
    _bar_windows[symbol].append(bar_dict)

    # Update session VWAP
    if symbol not in _vwap_state:
        _vwap_state[symbol] = {"cum_vwap_volume": 0.0, "cum_volume": 0}
    state = _vwap_state[symbol]
    state["cum_vwap_volume"] += float(bar.vwap) * int(bar.volume)
    state["cum_volume"] += int(bar.volume)

    # Fire callbacks
    for cb in _callbacks:
        try:
            cb(symbol, bar_dict)
        except Exception as e:
            _log(f"[STREAM] Callback error: {e}")


def _print_bar(symbol: str, bar: dict):
    """Default callback: print bar to stdout."""
    vwap = get_vwap(symbol)
    window_len = len(_bar_windows.get(symbol, []))
    _log(f"  {bar['timestamp'][:19]}  {symbol:5}  "
          f"O={bar['open']:<8.2f} H={bar['high']:<8.2f} "
          f"L={bar['low']:<8.2f} C={bar['close']:<8.2f} "
          f"V={bar['volume']:<8}  VWAP={vwap:.2f}  "
          f"[{window_len}/{WINDOW_SIZE}]")


# ── Main Loop ─────────────────────────────────────────────────────────────────

async def _run_stream(symbols: list[str], quiet: bool = False):
    global _stream, _shutdown

    _stream = StockDataStream(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
        feed=DataFeed.IEX,
    )

    if not quiet:
        register_callback(_print_bar)

    _stream.subscribe_bars(_on_bar, *symbols)

    _log(f"[STREAM] Subscribed to 1-min bars: {', '.join(symbols)}")
    _log(f"[STREAM] Feed: IEX | Window: {WINDOW_SIZE} bars | Kill switch: {'ENGAGED' if _check_kill_switch() else 'clear'}")

    # Run the stream in a background task so we can check shutdown flag
    _auth_failures = 0

    async def _run():
        nonlocal _auth_failures
        global _shutdown
        try:
            await _stream._run_forever()
        except ValueError as e:
            err = str(e).lower()
            if "connection limit" in err or "auth failed" in err:
                _auth_failures += 1
                _log(f"[STREAM] Auth/connection error: {e} (attempt {_auth_failures})")
                if _auth_failures >= 3:
                    _log("[STREAM] Too many auth failures — exiting. "
                          "Check for orphaned connections or upgrade Alpaca plan.")
                    _shutdown = True
            else:
                _log(f"[STREAM] ValueError: {e}")
                _shutdown = True
        except Exception as e:
            if not _shutdown:
                _log(f"[STREAM] Connection error: {e}")
                _shutdown = True

    stream_task = asyncio.create_task(_run())

    # Poll for shutdown conditions every 5 seconds
    while not _shutdown:
        await asyncio.sleep(5)
        if _check_kill_switch():
            _log("[STREAM] Kill switch detected — shutting down")
            _shutdown = True
        if not _is_market_hours():
            _log("[STREAM] Market closed — shutting down")
            _shutdown = True

    # Cleanup
    try:
        await _stream.close()
    except Exception:
        pass
    stream_task.cancel()
    try:
        await stream_task
    except asyncio.CancelledError:
        pass


def main():
    quiet = "--quiet" in sys.argv

    # Signal handlers
    def _handle_signal(sig, frame):
        global _shutdown
        _log(f"\n[STREAM] Received signal {sig} — shutting down")
        _shutdown = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Kill switch check
    if _check_kill_switch():
        reason = KILL_SWITCH_FILE.read_text().strip()
        _log(f"[STREAM] Kill switch engaged: {reason}")
        sys.exit(0)

    # Load symbols from playbook
    symbols = _load_playbook_symbols()
    if not symbols:
        _log("[STREAM] No scalping-enabled symbols in playbook. Nothing to stream.")
        _log("[STREAM] Run the daily pipeline first to generate a playbook.")
        sys.exit(0)

    _log(f"[STREAM] Symbols from playbook: {symbols}")

    # Market hours check
    if not _is_market_hours():
        now_et = datetime.now(ET)
        _log(f"[STREAM] Market is closed (current ET: {now_et.strftime('%H:%M %A')})")
        _log(f"[STREAM] Market hours: 9:30 AM - 4:00 PM ET, Monday-Friday")

        # Still test the connection
        _log(f"[STREAM] Testing websocket connection...")
        try:
            test_stream = StockDataStream(
                api_key=os.getenv("ALPACA_API_KEY_ID"),
                secret_key=os.getenv("ALPACA_SECRET_KEY"),
                feed=DataFeed.IEX,
            )
            _log(f"[STREAM] Connection OK — websocket client initialized")
            _log(f"[STREAM] Will stream {', '.join(symbols)} when market opens")
        except Exception as e:
            _log(f"[STREAM] Connection FAILED: {e}")
            sys.exit(1)
        sys.exit(0)

    # Run
    _log(f"\n[STREAM] Starting 1-minute bar stream...")
    _log(f"{'─'*90}")
    asyncio.run(_run_stream(symbols, quiet=quiet))
    _log(f"\n[STREAM] Stream stopped.")


if __name__ == "__main__":
    main()
