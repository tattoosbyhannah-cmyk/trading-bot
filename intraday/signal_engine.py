#!/usr/bin/env python3
"""
Intraday Signal Engine — pure-computation trade signal generator.

Hooks into stream_bars.py callbacks and generates directional signals from
four indicators: VWAP deviation, short-term momentum (EMA crossover),
volume spike confirmation, and mean-reversion fading.

No LLM in this path — signals are computed every bar in <1 ms.

Usage:
    # Live mode (import and register with stream_bars):
    from intraday.signal_engine import SignalEngine
    engine = SignalEngine(playbook)
    register_callback(engine.on_bar_callback)

    # Backtest mode:
    python intraday/signal_engine.py --backtest USO
    python intraday/signal_engine.py --backtest USO --days 10
"""

import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TRADING_BOT_DIR = Path(__file__).resolve().parent.parent
PLAYBOOK_FILE = TRADING_BOT_DIR / "playbook" / "daily_playbook.json"
SIGNAL_LOG = TRADING_BOT_DIR / "logs" / "intraday_signals.jsonl"
ET = ZoneInfo("America/New_York")


def _log(*args, **kwargs):
    """Wraps print() with an ISO-timestamp prefix in America/New_York for service log correlation.
    A leading '\\n' in the first arg becomes a real blank line so the timestamp lands on the content."""
    if args and isinstance(args[0], str) and args[0].startswith("\n"):
        print()  # noqa: T201 — separator blank line; timestamp lands on the next call
        args = (args[0].lstrip("\n"),) + args[1:]
    ts = datetime.now(ET).strftime("%Y-%m-%dT%H:%M:%S%z")
    print(ts, *args, **kwargs)  # noqa: T201 — intentional print inside log helper


@dataclass
class Signal:
    symbol: str
    timestamp: str
    direction: str       # "LONG" or "SHORT"
    strength: float      # 0.4 to 1.0
    components: dict     # individual signal scores
    entry_price: float
    reason: str          # human-readable summary


def _log_signal(sig: Signal):
    try:
        SIGNAL_LOG.parent.mkdir(exist_ok=True)
        with open(SIGNAL_LOG, "a") as f:
            f.write(json.dumps(asdict(sig), default=str) + "\n")
    except Exception:
        pass


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ── Individual Signals ────────────────────────────────────────────────────────

def _vwap_deviation(close: float, vwap: float) -> float:
    """Price vs session VWAP. Scale linearly: ±0.5% = ±0.33, ±1.5% = ±1.0."""
    if vwap <= 0:
        return 0.0
    pct = (close - vwap) / vwap * 100
    # Linear scale: 0.5% maps to ~0.33, 1.5% maps to 1.0
    score = pct / 1.5
    return max(-1.0, min(1.0, score))


def _momentum_ema(closes: pd.Series) -> float:
    """5-bar vs 20-bar EMA crossover. Returns -1 to +1 based on gap slope."""
    if len(closes) < 20:
        return 0.0
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)
    # Current gap as pct of price
    gap_pct = (ema5.iloc[-1] - ema20.iloc[-1]) / ema20.iloc[-1] * 100
    # Slope: change in gap over last 3 bars
    if len(ema5) >= 4 and len(ema20) >= 4:
        prev_gap = (ema5.iloc[-4] - ema20.iloc[-4]) / ema20.iloc[-4] * 100
        slope = gap_pct - prev_gap
    else:
        slope = 0.0
    # Combine gap and slope, scale to -1..+1
    raw = gap_pct * 0.5 + slope * 2.0
    return max(-1.0, min(1.0, raw))


def _volume_spike(volumes: pd.Series, close: float, prev_close: float,
                   spike_threshold: float = 2.0) -> float:
    """Volume spike confirmation. >threshold avg = spike. Direction from price change."""
    if len(volumes) < 20 or volumes.iloc[-1] == 0:
        return 0.0
    avg_vol = volumes.iloc[-21:-1].mean()
    if avg_vol <= 0:
        return 0.0
    ratio = volumes.iloc[-1] / avg_vol
    if ratio < spike_threshold:
        return 0.0  # No spike
    # Direction from price change
    price_dir = 1.0 if close > prev_close else -1.0 if close < prev_close else 0.0
    # Scale: threshold = 0.5, 2x threshold = 1.0
    spike_strength = min(1.0, (ratio - spike_threshold) / spike_threshold + 0.5)
    return price_dir * spike_strength


def _mean_reversion(close: float, vwap: float, atr: float,
                    playbook_direction: str) -> float:
    """Fade toward VWAP when price deviates >1 ATR. Only when aligned with playbook."""
    if vwap <= 0 or atr <= 0:
        return 0.0
    deviation = (close - vwap) / atr
    # Only signal when playbook agrees with the fade direction
    if abs(deviation) < 1.0:
        return 0.0  # Not enough deviation to fade

    if deviation > 1.0 and playbook_direction == "SHORT":
        # Price above VWAP by >1 ATR, playbook says SHORT → fade sell
        score = -min(1.0, (deviation - 1.0) / 2.0 + 0.3)
        return score
    elif deviation < -1.0 and playbook_direction == "LONG":
        # Price below VWAP by >1 ATR, playbook says LONG → fade buy
        score = min(1.0, (abs(deviation) - 1.0) / 2.0 + 0.3)
        return score

    return 0.0


def _compute_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                 period: int = 14) -> float:
    """Average True Range over the bar window."""
    if len(closes) < period + 1:
        return 0.0
    prev_closes = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_closes).abs(),
        (lows - prev_closes).abs(),
    ], axis=1).max(axis=1)
    return tr.iloc[-period:].mean()


# ── Signal Engine ─────────────────────────────────────────────────────────────

class SignalEngine:
    WEIGHTS = {
        "vwap": 0.30,
        "momentum": 0.30,
        "volume": 0.20,
        "mean_reversion": 0.20,
    }
    DEFAULT_THRESHOLD = 0.4
    DEFAULT_VOL_SPIKE = 2.0

    def __init__(self, playbook: dict):
        self.playbook = playbook  # symbol -> entry dict from daily_playbook.json

    def on_bar(self, symbol: str, bars_df: pd.DataFrame,
               vwap: float) -> Optional[Signal]:
        """Compute signals from current bar window. Returns Signal or None."""
        pb = self.playbook.get(symbol)
        if not pb:
            return None
        if not pb.get("allow_scalping", False):
            return None

        playbook_dir = pb.get("direction", "HOLD")
        if playbook_dir == "HOLD":
            return None

        if len(bars_df) < 20:
            return None

        # Read thresholds from intraday_profile (set by daily profiler)
        profile = pb.get("intraday_profile", {})
        threshold = profile.get("min_signal_strength", self.DEFAULT_THRESHOLD)
        vol_spike_mult = profile.get("volume_spike_threshold", self.DEFAULT_VOL_SPIKE)

        closes = bars_df["close"].astype(float)
        volumes = bars_df["volume"].astype(float)
        highs = bars_df["high"].astype(float)
        lows = bars_df["low"].astype(float)
        close = closes.iloc[-1]
        prev_close = closes.iloc[-2] if len(closes) >= 2 else close

        # Daily stop boundary check: don't signal entry if price is already
        # past the daily system's stop loss level
        daily_stop = pb.get("stop_loss")
        if daily_stop and daily_stop > 0:
            if playbook_dir == "LONG" and close <= daily_stop:
                return None  # Below daily stop — don't enter
            if playbook_dir == "SHORT" and close >= daily_stop:
                return None  # Above daily stop — don't enter

        # Compute individual signals
        atr = _compute_atr(highs, lows, closes)
        components = {
            "vwap": _vwap_deviation(close, vwap),
            "momentum": _momentum_ema(closes),
            "volume": _volume_spike(volumes, close, prev_close, vol_spike_mult),
            "mean_reversion": _mean_reversion(close, vwap, atr, playbook_dir),
        }

        # Weighted composite
        composite = sum(
            components[k] * self.WEIGHTS[k] for k in self.WEIGHTS
        )

        # Direction filter: never signal against playbook
        if playbook_dir == "LONG" and composite <= 0:
            return None
        if playbook_dir == "SHORT" and composite >= 0:
            return None

        # Threshold gate (asset-class-specific)
        if abs(composite) < threshold:
            return None

        direction = "LONG" if composite > 0 else "SHORT"
        strength = min(1.0, abs(composite))

        # Build reason string from active components
        active = []
        for name, score in components.items():
            if abs(score) > 0.1:
                label = name.replace("_", " ")
                sign = "+" if score > 0 else "-"
                active.append(f"{label} ({sign}{abs(score):.2f})")
        reason = " + ".join(active) if active else "composite threshold"

        ts = bars_df["timestamp"].iloc[-1] if "timestamp" in bars_df.columns else datetime.now().isoformat()

        sig = Signal(
            symbol=symbol,
            timestamp=str(ts),
            direction=direction,
            strength=round(strength, 3),
            components={k: round(v, 4) for k, v in components.items()},
            entry_price=round(close, 2),
            reason=reason,
        )
        _log_signal(sig)
        return sig

    def on_bar_callback(self, symbol: str, bar_dict: dict):
        """Adapter for stream_bars.register_callback. Imports at call time."""
        from intraday.stream_bars import get_bars, get_vwap
        bars_df = get_bars(symbol)
        vwap = get_vwap(symbol)
        sig = self.on_bar(symbol, bars_df, vwap)
        if sig:
            _log(f"  🔔 SIGNAL: {sig.direction} {sig.symbol} "
                  f"str={sig.strength:.2f} @ ${sig.entry_price:.2f} "
                  f"| {sig.reason}")


# ── Backtest Mode ─────────────────────────────────────────────────────────────

def _fetch_historical_bars(symbol: str, days: int = 5) -> pd.DataFrame:
    """Fetch intraday 1-min bars from Alpaca historical API."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )
    end = datetime.now()
    start = end - timedelta(days=days + 2)  # extra buffer for weekends

    req = StockBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
    )
    resp = client.get_stock_bars(req)
    bars = resp.data.get(symbol, [])

    records = []
    for b in bars:
        records.append({
            "timestamp": b.timestamp.isoformat(),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": int(b.volume),
            "vwap": float(b.vwap),
        })
    return pd.DataFrame(records)


def _run_backtest(symbol: str, days: int = 5):
    """Simulate the signal engine over historical 1-min bars."""
    _log(f"=== BACKTEST: {symbol} ({days} days of 1-min bars) ===\n")
    _log("Fetching historical bars...")

    all_bars = _fetch_historical_bars(symbol, days)
    if all_bars.empty:
        _log("No bars fetched.")
        return

    _log(f"Fetched {len(all_bars)} bars "
          f"({all_bars['timestamp'].iloc[0][:10]} to {all_bars['timestamp'].iloc[-1][:10]})\n")

    # Simulate playbook: use LONG for backtest (signals in both directions tested)
    sys.path.insert(0, str(TRADING_BOT_DIR))
    from fundamentals_analyst import asset_class as get_ac
    sym_ac = get_ac(symbol)
    for test_dir in ("LONG", "SHORT"):
        playbook = {
            symbol: {
                "direction": test_dir,
                "allow_scalping": True,
                "conviction": 8,
                "asset_class": sym_ac,
            }
        }
        engine = SignalEngine(playbook)

        signals = []
        window_size = 60

        # Session VWAP state (reset each day)
        cum_vwap_vol = 0.0
        cum_vol = 0
        current_date = None

        for i in range(20, len(all_bars)):
            row = all_bars.iloc[i]
            bar_date = row["timestamp"][:10]

            # Reset VWAP on new day
            if bar_date != current_date:
                current_date = bar_date
                cum_vwap_vol = 0.0
                cum_vol = 0

            cum_vwap_vol += row["vwap"] * row["volume"]
            cum_vol += row["volume"]
            session_vwap = cum_vwap_vol / cum_vol if cum_vol > 0 else row["close"]

            window_start = max(0, i - window_size + 1)
            window = all_bars.iloc[window_start:i + 1].copy()

            sig = engine.on_bar(symbol, window, session_vwap)
            if sig:
                # Check if price continued in signal direction 15 bars later
                future_idx = min(i + 15, len(all_bars) - 1)
                future_close = all_bars.iloc[future_idx]["close"]
                if sig.direction == "LONG":
                    win = future_close > sig.entry_price
                else:
                    win = future_close < sig.entry_price
                signals.append({
                    "signal": sig,
                    "future_close": future_close,
                    "win": win,
                    "return_pct": ((future_close - sig.entry_price) / sig.entry_price * 100)
                                  * (1 if sig.direction == "LONG" else -1),
                })

        # Report
        _log(f"── Playbook direction: {test_dir} ──")
        _log(f"  Signals generated: {len(signals)}")
        if signals:
            wins = sum(1 for s in signals if s["win"])
            win_rate = wins / len(signals) * 100
            avg_strength = np.mean([s["signal"].strength for s in signals])
            avg_return = np.mean([s["return_pct"] for s in signals])
            _log(f"  Win rate (15-bar): {wins}/{len(signals)} = {win_rate:.1f}%")
            _log(f"  Avg signal strength: {avg_strength:.3f}")
            _log(f"  Avg directional return: {avg_return:+.3f}%")

            # Show first 5 signals
            _log(f"\n  Sample signals:")
            for s in signals[:5]:
                sig = s["signal"]
                outcome = "WIN" if s["win"] else "LOSS"
                _log(f"    {sig.timestamp[:16]} {sig.direction} "
                      f"str={sig.strength:.2f} @ ${sig.entry_price:.2f} "
                      f"→ ${s['future_close']:.2f} ({s['return_pct']:+.2f}%) "
                      f"[{outcome}] | {sig.reason}")
        _log()


if __name__ == "__main__":
    if "--backtest" in sys.argv:
        idx = sys.argv.index("--backtest")
        symbol = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "USO"
        days = 5
        if "--days" in sys.argv:
            didx = sys.argv.index("--days")
            if didx + 1 < len(sys.argv):
                days = int(sys.argv[didx + 1])
        _run_backtest(symbol, days)
    else:
        _log("Usage:")
        _log("  python intraday/signal_engine.py --backtest USO")
        _log("  python intraday/signal_engine.py --backtest USO --days 10")
        _log("\nFor live mode, import and register with stream_bars:")
        _log("  from intraday.signal_engine import SignalEngine")
        _log("  engine = SignalEngine(playbook)")
        _log("  register_callback(engine.on_bar_callback)")
