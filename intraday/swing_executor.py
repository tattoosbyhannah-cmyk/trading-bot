#!/usr/bin/env python3
"""
Intraday Swing Executor — executes paper trades from signal engine within
playbook constraints. Single process that runs streamer + signals + execution.

Entry: limit order at signal price, cancel after 60s if unfilled.
Exit:  +0.3% take profit, -0.2% stop loss, 2h time stop, 3:45 PM hard close.

Usage:
    python intraday/swing_executor.py

Starts stream_bars, hooks signal_engine, and manages the full trade lifecycle.
Reads playbook/daily_playbook.json on startup.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

_BOTDIR = Path(__file__).resolve().parent.parent
load_dotenv(_BOTDIR / ".env")

sys.path.insert(0, str(_BOTDIR))
from brokers.broker_factory import get_broker
from brokers.base_broker import OrderRequest, OrderSide, OrderType, OrderStatus
from paper_trading_executor import (
    check_kill_switch, estimate_spread, adjust_for_costs, KILL_SWITCH_FILE
)
from intraday.signal_engine import SignalEngine, Signal
from intraday import stream_bars

ET = ZoneInfo("America/New_York")
PLAYBOOK_FILE = _BOTDIR / "playbook" / "daily_playbook.json"
TRADE_LOG = _BOTDIR / "logs" / "intraday_trades.jsonl"
TRADE_LOG.parent.mkdir(exist_ok=True)

# ── Trade lifecycle constants ─────────────────────────────────────────────────

# Defaults used only when ATR is unavailable
DEFAULT_TAKE_PROFIT_PCT = 0.3
DEFAULT_STOP_LOSS_PCT = 0.2
# ATR multipliers: stop = 2x 1-min ATR, target = 3x 1-min ATR
ATR_STOP_MULT = 2.0
ATR_TARGET_MULT = 3.0
TIME_STOP_MINUTES = 120
HARD_CLOSE_HOUR, HARD_CLOSE_MIN = 15, 45  # 3:45 PM ET
TRADING_START_HOUR, TRADING_START_MIN = 9, 45  # Avoid open volatility
TRADING_END_HOUR, TRADING_END_MIN = 15, 30  # Avoid close volatility
ORDER_TIMEOUT_SEC = 60
MIN_SIGNAL_STRENGTH = 0.5


# ── Intraday ATR computation ─────────────────────────────────────────────────

def _compute_intraday_atr_pct(bars_df: pd.DataFrame) -> float:
    """Compute 1-minute ATR as % of price from a DataFrame of bars.
    Uses up to 30 bars. Returns 0 if insufficient data."""
    if bars_df is None or len(bars_df) < 5:
        return 0.0
    df = bars_df.tail(30).copy()
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    closes = df["close"].astype(float)
    prev_c = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_c).abs(),
        (lows - prev_c).abs(),
    ], axis=1).max(axis=1)
    atr = tr.dropna().mean()
    price = closes.iloc[-1]
    return (atr / price) * 100 if price > 0 else 0.0


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    symbol: str
    direction: str
    entry_price: float
    shares: int
    entry_time: datetime
    order_id: str
    signal_strength: float
    signal_components: dict
    take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT
    daily_stop_price: float = 0.0  # Daily system's outer stop boundary


@dataclass
class TradeRecord:
    timestamp: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    exit_reason: str  # profit / stop / time / hard_close / daily_stop
    hold_time_minutes: float
    pnl_pct: float
    signal_strength: float
    signal_components: dict
    shares: int
    intraday_atr_pct: float = 0.0
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0


# ── Executor ──────────────────────────────────────────────────────────────────

class SwingExecutor:
    def __init__(self, playbook: dict):
        self.playbook = playbook  # symbol -> entry from daily_playbook
        self.broker = get_broker("alpaca")
        self.positions: dict[str, OpenPosition] = {}
        self.pending_orders: dict[str, dict] = {}
        self.trade_counts: dict[str, int] = {}
        self.daily_pnl: dict[str, float] = {}
        self.intraday_atr: dict[str, float] = {}  # symbol -> 1-min ATR %
        self.today = datetime.now(ET).date()

    def _reset_if_new_day(self):
        now = datetime.now(ET).date()
        if now != self.today:
            self.trade_counts.clear()
            self.daily_pnl.clear()
            self.today = now
            print(f"[EXEC] New trading day: {now}")

    def _in_trading_window(self) -> bool:
        now = datetime.now(ET)
        start = now.replace(hour=TRADING_START_HOUR, minute=TRADING_START_MIN,
                            second=0, microsecond=0)
        end = now.replace(hour=TRADING_END_HOUR, minute=TRADING_END_MIN,
                          second=0, microsecond=0)
        return start <= now <= end

    def _past_hard_close(self) -> bool:
        now = datetime.now(ET)
        hc = now.replace(hour=HARD_CLOSE_HOUR, minute=HARD_CLOSE_MIN,
                         second=0, microsecond=0)
        return now >= hc

    def _log_trade(self, record: TradeRecord):
        try:
            with open(TRADE_LOG, "a") as f:
                f.write(json.dumps(asdict(record), default=str) + "\n")
        except Exception as e:
            logging.warning(f"Failed to log trade: {e}")

    def _send_trade_alert(self, symbol: str, direction: str, shares: int,
                          price: float, spread_bps: float = 0):
        try:
            from alert_manager import alert_trade_executed
            alert_trade_executed(symbol, f"INTRADAY-{direction}", shares, price, spread_bps)
        except Exception:
            pass

    # ── Order management ──────────────────────────────────────────────────

    def _submit_limit_entry(self, sig: Signal, shares: int) -> Optional[str]:
        """Submit limit buy/sell. Returns order_id or None."""
        try:
            check_kill_switch()
            side = OrderSide.BUY if sig.direction == "LONG" else OrderSide.SELL
            order = OrderRequest(
                symbol=sig.symbol, qty=shares, side=side,
                order_type=OrderType.LIMIT,
                limit_price=round(sig.entry_price, 2),
                time_in_force="ioc",
            )
            result = self.broker.submit_order(order)
            return result.order_id
        except Exception as e:
            print(f"[EXEC] Order failed for {sig.symbol}: {e}")
            return None

    def _check_order_fill(self, order_id: str) -> Optional[float]:
        """Check if order filled. Returns fill price or None."""
        try:
            result = self.broker.get_order_status(order_id)
            if result.status == OrderStatus.FILLED and result.filled_avg_price > 0:
                return result.filled_avg_price
        except Exception:
            pass
        return None

    def _close_position(self, pos: OpenPosition, exit_price: float,
                        reason: str):
        """Market order to close, log, alert."""
        try:
            check_kill_switch()
            side = OrderSide.SELL if pos.direction == "LONG" else OrderSide.BUY
            order = OrderRequest(
                symbol=pos.symbol, qty=pos.shares, side=side,
                order_type=OrderType.MARKET, time_in_force="ioc",
            )
            self.broker.submit_order(order)
        except Exception as e:
            print(f"[EXEC] Close order failed for {pos.symbol}: {e}")

        hold_minutes = (datetime.now(ET) - pos.entry_time).total_seconds() / 60
        if pos.direction == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100

        record = TradeRecord(
            timestamp=datetime.now().isoformat(),
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            exit_reason=reason,
            hold_time_minutes=round(hold_minutes, 1),
            pnl_pct=round(pnl_pct, 3),
            signal_strength=pos.signal_strength,
            signal_components=pos.signal_components,
            shares=pos.shares,
            intraday_atr_pct=round(self.intraday_atr.get(pos.symbol, 0), 4),
            take_profit_pct=round(pos.take_profit_pct, 4),
            stop_loss_pct=round(pos.stop_loss_pct, 4),
        )
        self._log_trade(record)

        # Update daily P&L
        self.daily_pnl[pos.symbol] = self.daily_pnl.get(pos.symbol, 0) + pnl_pct

        emoji = "✅" if pnl_pct > 0 else "❌"
        print(f"  {emoji} EXIT {pos.symbol} {pos.direction} | {reason} | "
              f"${pos.entry_price:.2f} → ${exit_price:.2f} | "
              f"{pnl_pct:+.3f}% | {hold_minutes:.0f}min")

        self._send_trade_alert(pos.symbol, f"EXIT-{pos.direction}", pos.shares,
                               exit_price)

        del self.positions[pos.symbol]

    # ── Exit checking ─────────────────────────────────────────────────────

    def _check_exits(self, symbol: str, bar: dict):
        """Check take-profit, stop-loss, daily-stop, time-stop, hard-close."""
        pos = self.positions.get(symbol)
        if not pos:
            return

        close = bar["close"]
        high = bar["high"]
        low = bar["low"]

        # Daily system's outer stop boundary — never hold through it
        if pos.daily_stop_price and pos.daily_stop_price > 0:
            if pos.direction == "LONG" and low <= pos.daily_stop_price:
                self._close_position(pos, pos.daily_stop_price, "daily_stop")
                return
            if pos.direction == "SHORT" and high >= pos.daily_stop_price:
                self._close_position(pos, pos.daily_stop_price, "daily_stop")
                return

        # Intraday stop/target (ATR-scaled)
        if pos.direction == "LONG":
            tp_price = pos.entry_price * (1 + pos.take_profit_pct / 100)
            sl_price = pos.entry_price * (1 - pos.stop_loss_pct / 100)
            if high >= tp_price:
                self._close_position(pos, tp_price, "profit")
                return
            if low <= sl_price:
                self._close_position(pos, sl_price, "stop")
                return
        else:  # SHORT
            tp_price = pos.entry_price * (1 - pos.take_profit_pct / 100)
            sl_price = pos.entry_price * (1 + pos.stop_loss_pct / 100)
            if low <= tp_price:
                self._close_position(pos, tp_price, "profit")
                return
            if high >= sl_price:
                self._close_position(pos, sl_price, "stop")
                return

        # Time stop
        hold_minutes = (datetime.now(ET) - pos.entry_time).total_seconds() / 60
        if hold_minutes >= TIME_STOP_MINUTES:
            self._close_position(pos, close, "time")
            return

        # Hard close
        if self._past_hard_close():
            self._close_position(pos, close, "hard_close")
            return

    # ── Entry logic ───────────────────────────────────────────────────────

    def _try_entry(self, sig: Signal):
        """Validate all preconditions and enter a trade."""
        symbol = sig.symbol
        pb = self.playbook.get(symbol, {})

        # Precondition checks
        if KILL_SWITCH_FILE.exists():
            return
        if not pb.get("allow_scalping", False):
            return
        min_strength = pb.get("intraday_profile", {}).get("min_signal_strength", MIN_SIGNAL_STRENGTH)
        if sig.strength < min_strength:
            return
        if not self._in_trading_window():
            return
        if symbol in self.positions:
            return  # Already in a position

        max_trades = pb.get("max_intraday_trades", 0)
        if self.trade_counts.get(symbol, 0) >= max_trades:
            return

        max_loss = pb.get("max_intraday_loss_pct", 0.5)
        if self.daily_pnl.get(symbol, 0) <= -max_loss:
            print(f"  [EXEC] {symbol} daily loss limit hit ({self.daily_pnl[symbol]:.2f}%)")
            return

        # Position sizing
        spread = estimate_spread(symbol)
        base_pct = pb.get("position_size_pct", 0.2)
        adjusted_pct = adjust_for_costs(base_pct, spread["spread_bps"])
        # Scale by signal strength (capped at base)
        sized_pct = min(base_pct, adjusted_pct * (sig.strength / 0.7))

        try:
            portfolio_val = self.broker.get_account()["portfolio_value"]
        except Exception:
            portfolio_val = 100000.0

        position_val = portfolio_val * (sized_pct / 100)
        shares = max(1, int(position_val / sig.entry_price))

        # Submit limit order (IOC = fill immediately or cancel)
        order_id = self._submit_limit_entry(sig, shares)
        if not order_id:
            return

        # Check fill (IOC fills instantly or not at all)
        fill_price = self._check_order_fill(order_id)
        if not fill_price:
            print(f"  [EXEC] {symbol} limit order not filled @ ${sig.entry_price:.2f}")
            return

        # Compute ATR-scaled stops using profiler's ATR multiples
        profile = pb.get("intraday_profile", {})
        stop_mult = profile.get("stop_atr_multiple", ATR_STOP_MULT)
        target_mult = profile.get("target_atr_multiple", ATR_TARGET_MULT)

        bars_df = stream_bars.get_bars(symbol)
        atr_pct = _compute_intraday_atr_pct(bars_df) if not bars_df.empty else 0
        if atr_pct > 0:
            self.intraday_atr[symbol] = atr_pct
            tp_pct = atr_pct * target_mult
            sl_pct = atr_pct * stop_mult
        else:
            tp_pct = DEFAULT_TAKE_PROFIT_PCT
            sl_pct = DEFAULT_STOP_LOSS_PCT

        # Daily system's outer stop boundary
        daily_stop = pb.get("stop_loss") or 0

        # Position opened
        self.positions[symbol] = OpenPosition(
            symbol=symbol,
            direction=sig.direction,
            entry_price=fill_price,
            shares=shares,
            entry_time=datetime.now(ET),
            order_id=order_id,
            signal_strength=sig.strength,
            signal_components=sig.components,
            take_profit_pct=tp_pct,
            stop_loss_pct=sl_pct,
            daily_stop_price=daily_stop,
        )
        self.trade_counts[symbol] = self.trade_counts.get(symbol, 0) + 1

        print(f"  📈 ENTRY {symbol} {sig.direction} | {shares} shares @ ${fill_price:.2f} "
              f"| str={sig.strength:.2f} | #{self.trade_counts[symbol]}/{pb.get('max_intraday_trades',0)} "
              f"| ATR={atr_pct:.3f}% → stop={sl_pct:.3f}%/target={tp_pct:.3f}% "
              f"| {sig.reason}")
        self._send_trade_alert(symbol, sig.direction, shares, fill_price,
                               spread["spread_bps"])

    # ── Bar callback (wired into stream_bars) ─────────────────────────────

    def on_bar(self, symbol: str, bar_dict: dict):
        """Called on every 1-min bar from stream_bars."""
        self._reset_if_new_day()

        # 1. Check exits for open positions
        self._check_exits(symbol, bar_dict)

        # 2. Hard-close all if past deadline
        if self._past_hard_close() and symbol in self.positions:
            self._close_position(self.positions[symbol], bar_dict["close"], "hard_close")
            return

        # 3. Generate signal and try entry
        bars_df = stream_bars.get_bars(symbol)
        vwap = stream_bars.get_vwap(symbol)
        sig = self._signal_engine.on_bar(symbol, bars_df, vwap)
        if sig:
            self._try_entry(sig)

    def set_signal_engine(self, engine: SignalEngine):
        self._signal_engine = engine

    # ── Summary ───────────────────────────────────────────────────────────

    def print_summary(self):
        print(f"\n{'='*70}")
        print(f"📊 INTRADAY SUMMARY — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
        print(f"{'='*70}")
        print(f"Open positions: {len(self.positions)}")
        for sym, pos in self.positions.items():
            hold = (datetime.now(ET) - pos.entry_time).total_seconds() / 60
            print(f"  {sym} {pos.direction} {pos.shares}sh @ ${pos.entry_price:.2f} "
                  f"({hold:.0f}min)")
        print(f"\nTrade counts today:")
        for sym in sorted(set(list(self.trade_counts.keys()) + list(self.daily_pnl.keys()))):
            count = self.trade_counts.get(sym, 0)
            pnl = self.daily_pnl.get(sym, 0)
            max_t = self.playbook.get(sym, {}).get("max_intraday_trades", 0)
            print(f"  {sym}: {count}/{max_t} trades | P&L: {pnl:+.3f}%")
        print(f"{'='*70}\n")


# ── Main: unified intraday pipeline ──────────────────────────────────────────

def main():
    print(f"[SWING] Intraday Swing Executor starting")
    print(f"[SWING] Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}")

    # Kill switch
    if KILL_SWITCH_FILE.exists():
        reason = KILL_SWITCH_FILE.read_text().strip()
        print(f"[SWING] Kill switch engaged: {reason}")
        sys.exit(0)

    # Load playbook
    if not PLAYBOOK_FILE.exists():
        print(f"[SWING] No playbook at {PLAYBOOK_FILE} — run daily pipeline first")
        sys.exit(1)

    playbook = json.loads(PLAYBOOK_FILE.read_text())
    symbols_config = playbook.get("symbols", {})
    active_symbols = [s for s, cfg in symbols_config.items()
                      if cfg.get("allow_scalping", False)]

    if not active_symbols:
        print(f"[SWING] No scalping-enabled symbols in playbook")
        sys.exit(0)

    print(f"[SWING] Active symbols: {active_symbols}")
    for sym in active_symbols:
        cfg = symbols_config[sym]
        print(f"  {sym}: {cfg['direction']} conv={cfg['conviction']} "
              f"max_trades={cfg['max_intraday_trades']}")

    # Market hours check
    if not stream_bars._is_market_hours():
        now_et = datetime.now(ET)
        print(f"[SWING] Market closed ({now_et.strftime('%H:%M %A ET')})")
        print(f"[SWING] Will exit. Use systemd timer for auto-start at market open.")
        sys.exit(0)

    # Build components
    engine = SignalEngine(symbols_config)
    executor = SwingExecutor(symbols_config)
    executor.set_signal_engine(engine)

    # Register executor as the bar callback (replaces default printer)
    stream_bars.register_callback(executor.on_bar)

    # Signal handlers
    def _handle_signal(sig, frame):
        stream_bars._shutdown = True
        print(f"\n[SWING] Received signal {sig} — shutting down")
        # Close all open positions
        for sym in list(executor.positions.keys()):
            bars_df = stream_bars.get_bars(sym)
            if not bars_df.empty:
                executor._close_position(
                    executor.positions[sym],
                    bars_df["close"].iloc[-1],
                    "shutdown"
                )
        executor.print_summary()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"\n[SWING] Starting intraday bar stream + execution...")
    print(f"[SWING] Entry window: 9:45 AM - 3:30 PM ET")
    print(f"[SWING] Hard close: 3:45 PM ET")
    print(f"[SWING] Min signal strength: {MIN_SIGNAL_STRENGTH}")
    print(f"[SWING] Default TP: +{DEFAULT_TAKE_PROFIT_PCT}% | Default SL: -{DEFAULT_STOP_LOSS_PCT}% (ATR-scaled per trade)")
    print(f"{'─'*70}")

    # Run the stream (blocks until shutdown)
    asyncio.run(stream_bars._run_stream(active_symbols, quiet=True))

    # Final summary
    executor.print_summary()
    print("[SWING] Executor stopped.")


if __name__ == "__main__":
    main()
