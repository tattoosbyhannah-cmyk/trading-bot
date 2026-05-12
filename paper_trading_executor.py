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
BEARISH_PROXIES_FILE = Path(__file__).parent / "config" / "bearish_proxies.yaml"
INSTRUMENTS_FILE = Path(__file__).parent / "config" / "instruments.yaml"
RISK_LIMITS_FILE = Path(__file__).parent / "config" / "risk_limits.yaml"
ROUTE_EVENTS_LOG = Path(__file__).parent / "logs" / "route_events.jsonl"


def check_kill_switch():
    """Raise if kill switch is engaged. Check BEFORE every order."""
    if KILL_SWITCH_FILE.exists():
        reason = KILL_SWITCH_FILE.read_text().strip() or "No reason given"
        raise RuntimeError(
            f"KILL SWITCH ENGAGED: {reason}. "
            f"Remove {KILL_SWITCH_FILE} to re-enable trading."
        )


# ── Bearish-proxy routing (W4) ───────────────────────────────────────────────

_bearish_proxies_cache: Optional[Dict[str, dict]] = None
_instruments_cache: Optional[Dict[str, dict]] = None
_risk_limits_cache: Optional[Dict] = None
_route_column_ready: bool = False


def _load_instruments() -> Dict[str, dict]:
    """Lazy-load + cache config/instruments.yaml. Returns the inner 'instruments' dict."""
    global _instruments_cache
    if _instruments_cache is None:
        try:
            import yaml
            full = yaml.safe_load(INSTRUMENTS_FILE.read_text()) or {}
            _instruments_cache = full.get("instruments", {})
        except Exception as e:
            logging.warning(f"instruments.yaml load failed: {e}")
            _instruments_cache = {}
    return _instruments_cache


def _load_risk_limits() -> Dict:
    """Lazy-load + cache config/risk_limits.yaml. Empty dict on failure."""
    global _risk_limits_cache
    if _risk_limits_cache is None:
        try:
            import yaml
            _risk_limits_cache = yaml.safe_load(RISK_LIMITS_FILE.read_text()) or {}
        except Exception as e:
            logging.warning(f"risk_limits.yaml load failed: {e}")
            _risk_limits_cache = {}
    return _risk_limits_cache


def _symbol_metadata(symbol: str) -> Optional[dict]:
    """Map a symbol to {asset_class, leverage} where leverage is signed.
    +1: direct exposure (e.g., UNG = +1x natgas)
    -2: inverse leveraged exposure (e.g., KOLD = -2x natgas)
    Returns None if symbol not registered as either a primary instrument or a known proxy.
    """
    sym = symbol.upper()
    instruments = _load_instruments()
    if sym in instruments:
        ac = instruments[sym].get("asset_class")
        if ac:
            return {"asset_class": ac, "leverage": 1}
    # Otherwise look for it as a proxy in any bearish_proxies entry
    for orig, route in _load_bearish_proxies().items():
        if (route or {}).get("proxy", "").upper() == sym:
            ac = route.get("asset_class")
            lev = int(route.get("leverage", 1))
            if ac:
                return {"asset_class": ac, "leverage": lev}
    return None


def _check_exposure_cap(manager, route: dict, equity: float) -> dict:
    """Pre-trade cross-position exposure cap. Sums same-asset-class same-direction
    economic exposure (effective notional = qty × price × abs(leverage)) and compares
    against the configured cap. Returns an audit dict; caller decides on action.
    """
    cap_pct = float(_load_risk_limits().get("exposure_cap_pct", 7.5))
    asset_class = (route or {}).get("asset_class")
    if not asset_class or equity <= 0:
        return {"action": "allowed", "reason": "no_asset_class_or_equity", "cap_pct": cap_pct}

    proxy_leverage = int(route.get("leverage", 1))
    # Routed trades always submit LONG on the proxy; economic direction toward the
    # underlying = signum(leverage). A -2x proxy LONG is bearish on the underlying;
    # a +1x "invert direction" proxy LONG is bullish on the underlying.
    proposed_direction = -1 if proxy_leverage < 0 else 1
    direction_label = "bearish" if proposed_direction == -1 else "bullish"

    proxy_symbol = route.get("executed_symbol", "")
    proxy_price = manager.get_current_price(proxy_symbol) or 0.0
    if proxy_price <= 0:
        return {"action": "allowed", "reason": "proxy_price_unavailable", "cap_pct": cap_pct,
                "asset_class": asset_class, "direction": direction_label}

    sized_pct = float(route.get("sized_pct", 0))
    proxy_shares = manager.calculate_share_quantity(proxy_symbol, sized_pct, proxy_price)
    proposed_effective = proxy_shares * proxy_price * abs(proxy_leverage)

    # Sum existing same-direction same-asset-class effective notional
    contributing = []
    existing_effective = 0.0
    try:
        positions = manager.get_current_positions()
    except Exception as e:
        logging.warning(f"exposure cap: get_current_positions failed: {e}")
        positions = {}

    for sym, qty in positions.items():
        if not qty:
            continue
        meta = _symbol_metadata(sym)
        if not meta or meta["asset_class"] != asset_class:
            continue
        sym_lev = meta["leverage"]
        # Economic direction toward underlying = signum(qty × leverage)
        product = qty * sym_lev
        sym_dir = -1 if product < 0 else 1
        if sym_dir != proposed_direction:
            continue  # opposite direction — doesn't add concentration risk
        sym_price = manager.get_current_price(sym) or 0.0
        if sym_price <= 0:
            continue
        eff = abs(qty) * sym_price * abs(sym_lev)
        existing_effective += eff
        contributing.append({"symbol": sym, "qty": qty, "leverage": sym_lev,
                             "price": round(sym_price, 4), "effective_notional": round(eff, 2)})

    combined = existing_effective + proposed_effective
    cap_notional = equity * (cap_pct / 100.0)

    return {
        "asset_class": asset_class,
        "direction": direction_label,
        "existing_pct": round(existing_effective / equity * 100, 3),
        "proposed_pct": round(proposed_effective / equity * 100, 3),
        "combined_pct": round(combined / equity * 100, 3),
        "cap_pct": cap_pct,
        "action": "allowed" if combined <= cap_notional else "skipped",
        "contributing_positions": contributing,
    }


def _check_direct_exposure_cap(manager, symbol: str, signal_direction: str,
                                sized_pct: float, equity: float) -> dict:
    """Pre-trade exposure cap for DIRECT entries (LONG/SHORT on a primary instrument).

    The W4 cap (_check_exposure_cap) was bolted onto the routing path. This wrapper
    extends the same cap to direct entries so persistent same-direction signals
    can't compound a single symbol past the cap. Constructs a synthetic 'route'-shaped
    dict where `leverage` carries the signed economic direction
    (signal_sign × symbol_leverage), then delegates to the shared cap calculator.

    Example:
      direct UNG LONG: signal=+1, symbol_leverage=+1 → effective_leverage=+1 → bullish
      direct UNG SHORT: signal=-1, symbol_leverage=+1 → effective_leverage=-1 → bearish
      direct KOLD LONG: signal=+1, symbol_leverage=-2 → effective_leverage=-2 → bearish on natgas
    """
    meta = _symbol_metadata(symbol)
    if not meta:
        return {"action": "allowed", "reason": "no_symbol_metadata"}
    signal_sign = 1 if signal_direction.upper().startswith("LONG") else -1
    effective_leverage = meta["leverage"] * signal_sign
    fake_route = {
        "executed_symbol": symbol,
        "leverage": effective_leverage,
        "asset_class": meta["asset_class"],
        "sized_pct": sized_pct,
    }
    return _check_exposure_cap(manager, fake_route, equity)


def _flatten_routed_proxies(manager, original_symbol: str, new_signal_direction: str) -> list:
    """When a direct entry signal fires for `original_symbol`, identify any
    currently-open proxy positions that were placed as the BEARISH PROXY ROUTE
    for `original_symbol`, and flatten them if their economic direction OPPOSES
    the new signal.

    Background: when the system first routed a UNG SHORT signal (UNG not
    shortable that day) to KOLD LONG via bearish_proxies.yaml, it opened a -2x
    bearish-natgas position. If UNG later flips direction to LONG, the KOLD
    position is a stale hedge that contradicts the new thesis. The existing
    flatten_position only flattens the ORIGINAL symbol — not its routed proxies.
    This helper closes that gap.

    Returns list of (proxy_symbol, qty) tuples that were flattened.
    """
    proxies = _load_bearish_proxies().get(original_symbol)
    if not proxies:
        return []
    # bearish_proxies.yaml maps original → {proxy, leverage, ...}
    proxy_symbol = proxies.get("proxy")
    proxy_leverage = int(proxies.get("leverage", 1))
    if not proxy_symbol:
        return []
    try:
        positions = manager.get_current_positions()
    except Exception:
        return []
    proxy_qty = positions.get(proxy_symbol, 0)
    if not proxy_qty:
        return []

    # Economic direction of the existing proxy position toward the underlying:
    #   signum(qty × leverage). For KOLD LONG (+qty, -2 lev) → product < 0 → bearish.
    proxy_econ_dir = -1 if (proxy_qty * proxy_leverage) < 0 else 1
    # New signal's economic direction toward the same underlying:
    new_dir = 1 if new_signal_direction.upper().startswith("LONG") else -1
    if proxy_econ_dir == new_dir:
        return []  # Same direction — proxy is congruent with new signal, don't touch

    # Opposite — flatten the proxy as a stale hedge
    print(f"  [PROXY-FLATTEN] {proxy_symbol} (opened as {original_symbol} bearish route) "
          f"opposes new {original_symbol} {new_signal_direction} signal — flattening")
    ok = manager.flatten_position(proxy_symbol,
                                  reason=f"stale {original_symbol}-routed proxy; "
                                         f"new {new_signal_direction} signal reverses thesis")
    return [(proxy_symbol, proxy_qty)] if ok else []


def _load_bearish_proxies() -> Dict[str, dict]:
    """Lazy-load + cache config/bearish_proxies.yaml. Empty dict on failure."""
    global _bearish_proxies_cache
    if _bearish_proxies_cache is None:
        try:
            import yaml
            _bearish_proxies_cache = yaml.safe_load(BEARISH_PROXIES_FILE.read_text()) or {}
        except FileNotFoundError:
            _bearish_proxies_cache = {}
        except Exception as e:
            logging.warning(f"bearish_proxies.yaml load failed: {e}")
            _bearish_proxies_cache = {}
    return _bearish_proxies_cache


def _resolve_short_route(original_symbol: str, original_pct: float) -> Optional[dict]:
    """Return a route_taken dict if a proxy is configured, else None.

    Two routing modes:
      leverage == -2 (inverse ETF): SHORT signal on USO → LONG on SCO at sized_pct=original/2
      leverage == 1 with note 'invert direction' (bear-N× ETF): SHORT on DUST → LONG on GDX at original_pct
    """
    cfg = _load_bearish_proxies().get(original_symbol)
    if not cfg:
        return None
    proxy = cfg.get("proxy")
    leverage = int(cfg.get("leverage", 1))
    asset_class = cfg.get("asset_class", "")
    note = (cfg.get("note") or "").lower()

    if leverage == -2:
        sized_pct = round(original_pct / 2.0, 4)
    elif leverage == 1 and "invert" in note:
        sized_pct = round(original_pct, 4)
    else:
        logging.warning(
            f"bearish_proxies entry for {original_symbol} has unsupported "
            f"leverage={leverage} note={note!r} — skipping route"
        )
        return None

    return {
        "original_symbol": original_symbol,
        "original_direction": "SHORT",
        "executed_symbol": proxy,
        "executed_direction": "LONG",
        "leverage": leverage,
        "sized_pct": sized_pct,
        "original_pct": round(original_pct, 4),
        "asset_class": asset_class,
    }


def _compute_proxy_stop(original_stop: float, original_entry: float,
                        original_leverage: int, proxy_entry: float,
                        proxy_leverage: int) -> Optional[float]:
    """Translate a stop-loss from the original symbol's price space into the
    proxy symbol's price space using leverage-aware math.

    Math (daily-rebalance approximation; ignores multi-day path-dependence drift):
      stop_dist_signed = (original_stop - original_entry) / original_entry
      underlying_change = stop_dist_signed / L_orig
      proxy_change      = underlying_change × L_proxy
      proxy_stop_price  = proxy_entry × (1 + proxy_change)

    Worked example (UNG SHORT → KOLD LONG):
      UNG entry=$10.29, stop=$10.91 → stop_dist_signed = +0.0602 (stop above for SHORT)
      L_orig = +1, L_proxy = -2 → proxy_change = +0.0602 × (-2) / 1 = -0.1204
      KOLD entry=$25.92 → proxy_stop = $25.92 × (1 - 0.1204) = $22.80

    Returns None if math is degenerate or would put the stop on the WRONG side
    of the proxy entry (which would mean the routing config has a direction error;
    a LONG proxy must always have stop < entry).
    """
    if (original_entry is None or original_entry <= 0
            or proxy_entry <= 0 or original_leverage == 0):
        return None
    stop_dist_signed = (original_stop - original_entry) / original_entry
    proxy_change = stop_dist_signed * proxy_leverage / original_leverage
    proxy_stop = proxy_entry * (1 + proxy_change)
    # Routed proxy positions are always LONG → stop must be below entry.
    # If the math puts it above, the routing config has a direction inversion bug;
    # refuse to submit a meaningless stop and let the caller decide.
    if proxy_stop >= proxy_entry:
        logging.warning(
            f"_compute_proxy_stop: math produced proxy_stop=${proxy_stop:.2f} >= "
            f"proxy_entry=${proxy_entry:.2f} (proxy LONG would have stop above entry). "
            f"Likely a direction-inversion error in bearish_proxies.yaml; refusing."
        )
        return None
    # Sanity floor: stop within 50% of entry to avoid pathological edge cases
    if proxy_stop < proxy_entry * 0.5:
        logging.warning(
            f"_compute_proxy_stop: proxy_stop=${proxy_stop:.2f} is <50% of "
            f"proxy_entry=${proxy_entry:.2f}; refusing as likely arithmetic error."
        )
        return None
    return round(proxy_stop, 2)


def _ensure_route_taken_column() -> bool:
    """Idempotent: add decisions.route_taken JSONB if missing. Cached after first success."""
    global _route_column_ready
    if _route_column_ready:
        return True
    try:
        from db.connection import db_cursor
        with db_cursor() as cur:
            cur.execute("ALTER TABLE decisions ADD COLUMN IF NOT EXISTS route_taken JSONB")
        _route_column_ready = True
    except Exception as e:
        logging.warning(f"ALTER decisions ADD route_taken failed: {e}")
    return _route_column_ready


def _persist_route_event(route: dict, calculation_run_id: str = "", trade_result=None):
    """Append to route_events.jsonl + UPDATE decisions.route_taken (best-effort)."""
    record = {
        "timestamp": datetime.now().isoformat(),
        "calculation_run_id": calculation_run_id,
        "route_taken": route,
        "filled_price": getattr(trade_result, "filled_price", None) if trade_result else None,
        "filled_qty": getattr(trade_result, "filled_qty", None) if trade_result else None,
        "order_id": getattr(trade_result, "order_id", None) if trade_result else None,
    }
    try:
        ROUTE_EVENTS_LOG.parent.mkdir(exist_ok=True)
        with open(ROUTE_EVENTS_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logging.warning(f"route event JSONL write failed: {e}")

    if calculation_run_id and _ensure_route_taken_column():
        try:
            from db.connection import db_cursor
            with db_cursor() as cur:
                cur.execute(
                    "UPDATE decisions SET route_taken = %s "
                    "WHERE calculation_run_id = %s AND symbol = %s",
                    (json.dumps(route), calculation_run_id, route["original_symbol"]),
                )
        except Exception as e:
            logging.warning(f"decisions.route_taken UPDATE failed: {e}")


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
        # Per-session cache for shortability lookups. Asset shortability rarely
        # changes mid-day, so a single get_asset() call per symbol per process is
        # sufficient. Cache key is the uppercased symbol; value is bool.
        self._shortable_cache: Dict[str, bool] = {}

    def is_shortable(self, symbol: str) -> bool:
        """True if the broker reports the symbol as shortable. Fail-open on
        lookup errors so the broker can do the final reject."""
        sym = symbol.upper()
        if sym in self._shortable_cache:
            return self._shortable_cache[sym]
        try:
            asset = self.broker.get_asset(sym)
            shortable = bool(getattr(asset, "shortable", False))
        except Exception as e:
            logging.warning(f"Shortability lookup failed for {sym}: {e}")
            shortable = True  # fail open
        self._shortable_cache[sym] = shortable
        return shortable

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

        # Pre-cancel stale orders on this symbol to avoid Alpaca code 40310000
        # (potential wash-trade rejection when a new BUY/SELL collides with an
        # existing opposite-side stop order). Mirrors the W4 step-3 pattern in
        # intraday/swing_executor.py:_try_entry. Stops from prior fills should
        # have been cancelled at flatten time but sometimes persist when the
        # decision is same-direction add-on (e.g., LONG signal on existing LONG).
        #
        # Alpaca's cancel API is async: cancel_order returns immediately on
        # accept, but the order stays in `pending_cancel` for ~1 sec while
        # Alpaca propagates. If we submit the new BUY/SELL during that window
        # the wash-trade reject still fires, citing the still-pending order.
        # Poll each cancelled order until it's fully `canceled` before continuing.
        try:
            open_orders = self.broker.get_open_orders(symbol=symbol)
            cancelled_ids = []
            for order in open_orders:
                self.broker.cancel_order(order.id)
                cancelled_ids.append(str(order.id))
                print(f"  [EXEC] {symbol} cancelled stale order {str(order.id)[:8]} "
                      f"({order.side} {order.qty})")
            # Wait for each cancellation to fully propagate (cap 5 s per order)
            for oid in cancelled_ids:
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    try:
                        status = str(self.broker._trading.get_order_by_id(oid).status).lower()
                        if "canceled" in status or "cancelled" in status:
                            break
                        if "filled" in status or "rejected" in status or "expired" in status:
                            break
                    except Exception:
                        break
                    time.sleep(0.2)
        except Exception as e:
            print(f"  [EXEC] {symbol} order cancel pre-check failed: {e}")

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

    # Stale-routed-proxy cleanup: if a prior routed SHORT trade left a bearish
    # proxy position (e.g., KOLD LONG from a UNG-non-shortable day) and today's
    # signal direction is opposite, flatten the stale proxy. Runs before the
    # direct flatten check below so the broker state is clean before sizing.
    if decision.final_decision.upper().startswith(("LONG", "SHORT")):
        try:
            _flatten_routed_proxies(manager, symbol, decision.final_decision)
        except Exception as e:
            logging.warning(f"_flatten_routed_proxies failed for {symbol}: {e}")

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
        # Direct-entry exposure cap: prevents persistent same-direction signals
        # from compounding a single symbol past the asset-class cap. Same cap
        # math as W4 routing — extends to non-routed primary instruments.
        equity = manager.get_portfolio_value()
        direct_cap = _check_direct_exposure_cap(manager, symbol, "LONG", position_pct, equity)
        if direct_cap.get("action") == "skipped":
            cap = direct_cap.get("cap_pct")
            ac = direct_cap.get("asset_class")
            combined = direct_cap.get("combined_pct")
            print(f"[CAP] {symbol} {ac} exposure would exceed {cap}% "
                  f"(combined {combined}%) — skipping LONG entry")
            return {
                "success": False, "skipped": True, "reason": "exposure_cap_exceeded",
                "symbol": symbol, "exposure_check": direct_cap,
                "current_price": current_price, "spread_bps": spread_bps,
                "portfolio_value": equity, "timestamp": datetime.now().isoformat(),
            }

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
        # Alpaca paper accounts cannot short every symbol. Pre-check shortability;
        # if not shortable, attempt bearish-proxy routing (W4) before falling back to [SKIP].
        if not manager.is_shortable(symbol):
            calc_run_id = (decision.agent_consensus or {}).get("calculation_run_id", "")
            route = _resolve_short_route(symbol, position_pct)

            if route is None:
                print(f"[SKIP] {symbol} SHORT signal — not shortable on Alpaca paper account, no proxy configured")
                return {
                    "success": False,
                    "skipped": True,
                    "reason": "not_shortable",
                    "symbol": symbol,
                }

            # Route to LONG on the proxy
            proxy_symbol = route["executed_symbol"]
            sized_pct = route["sized_pct"]
            print(f"[ROUTE] {symbol} SHORT → {proxy_symbol} LONG (leverage adjusted: {sized_pct}%)")

            proxy_price = manager.get_current_price(proxy_symbol)
            if not proxy_price:
                logging.warning(f"could not fetch proxy price for {proxy_symbol}")
                return {
                    "success": False,
                    "skipped": True,
                    "reason": "proxy_price_lookup_failed",
                    "symbol": symbol,
                    "route_taken": route,
                }

            # Cross-position exposure cap (prevents stacking same-direction same-asset-class
            # exposure beyond the configured limit). See config/risk_limits.yaml.
            equity = manager.get_portfolio_value()
            exposure_check = _check_exposure_cap(manager, route, equity)
            route["exposure_check"] = exposure_check
            if exposure_check.get("action") == "skipped":
                cap = exposure_check.get("cap_pct")
                ac = exposure_check.get("asset_class")
                combined = exposure_check.get("combined_pct")
                print(f"[CAP] {symbol} {ac} exposure would exceed {cap}% "
                      f"(combined {combined}%) — skipping")
                _persist_route_event(route, calculation_run_id=calc_run_id, trade_result=None)
                return {
                    "success": False,
                    "skipped": True,
                    "reason": "exposure_cap_exceeded",
                    "symbol": symbol,
                    "route_taken": route,
                }

            proxy_shares = manager.calculate_share_quantity(proxy_symbol, sized_pct, proxy_price)
            print(f"Buying {proxy_shares} shares of {proxy_symbol} (routed from {symbol} SHORT)...")
            trade_result = manager.execute_long_trade(
                proxy_symbol, proxy_shares,
                decision_price=proxy_price, spread_bps=spread_bps,
            )
            execution_result['trade'] = trade_result
            execution_result['route_taken'] = route

            if trade_result.success:
                print(f"✅ Routed long trade executed: {proxy_shares} shares of {proxy_symbol}")
                if trade_result.filled_price:
                    print(f"   Fill: ${trade_result.filled_price:.2f}")
                try:
                    from alert_manager import alert_trade_executed
                    alert_trade_executed(
                        proxy_symbol, f"LONG (routed from {symbol} SHORT)",
                        proxy_shares, trade_result.filled_price or proxy_price, spread_bps,
                    )
                except Exception:
                    pass

                # Translate the original-space stop into proxy price space and submit.
                # Without this, the routed LONG position has no protection against
                # adverse overnight moves — fix for "Priority 2: Proxy-aware stop-loss".
                if decision.stop_loss and decision.entry_price:
                    orig_meta = _symbol_metadata(symbol)
                    if orig_meta:
                        proxy_fill_price = trade_result.filled_price or proxy_price
                        proxy_stop_price = _compute_proxy_stop(
                            original_stop=float(decision.stop_loss),
                            original_entry=float(decision.entry_price),
                            original_leverage=int(orig_meta["leverage"]),
                            proxy_entry=float(proxy_fill_price),
                            proxy_leverage=int(route["leverage"]),
                        )
                        proxy_stop_payload = {
                            "original_stop": round(float(decision.stop_loss), 4),
                            "original_entry": round(float(decision.entry_price), 4),
                            "original_leverage": int(orig_meta["leverage"]),
                            "proxy_entry": round(float(proxy_fill_price), 4),
                            "proxy_leverage": int(route["leverage"]),
                            "stop_distance_pct": round(
                                (float(decision.stop_loss) - float(decision.entry_price))
                                / float(decision.entry_price), 4),
                            "computed_proxy_stop": proxy_stop_price,
                        }
                        if proxy_stop_price is not None:
                            stop_order_id = manager.set_stop_loss(proxy_symbol, proxy_stop_price)
                            proxy_stop_payload["stop_order_id"] = stop_order_id
                            execution_result['stop_loss_order_id'] = stop_order_id
                        else:
                            proxy_stop_payload["stop_order_id"] = None
                            proxy_stop_payload["skipped_reason"] = "proxy_stop_math_invalid"
                            print(f"  [WARN] proxy stop math invalid for {proxy_symbol}; no stop submitted")
                        route["proxy_stop"] = proxy_stop_payload
                    else:
                        logging.warning(f"no _symbol_metadata for {symbol}; skipping proxy stop")
                else:
                    logging.info(f"no decision.stop_loss for {symbol} routed trade; no proxy stop computed")
            else:
                print(f"❌ Routed trade failed: {trade_result.error_message}")

            _persist_route_event(route, calculation_run_id=calc_run_id, trade_result=trade_result)
            # Fall through to attach metadata at end of function
        else:
            # Direct-entry exposure cap on shortable symbols too
            equity = manager.get_portfolio_value()
            direct_cap = _check_direct_exposure_cap(manager, symbol, "SHORT", position_pct, equity)
            if direct_cap.get("action") == "skipped":
                cap = direct_cap.get("cap_pct")
                ac = direct_cap.get("asset_class")
                combined = direct_cap.get("combined_pct")
                print(f"[CAP] {symbol} {ac} exposure would exceed {cap}% "
                      f"(combined {combined}%) — skipping SHORT entry")
                return {
                    "success": False, "skipped": True, "reason": "exposure_cap_exceeded",
                    "symbol": symbol, "exposure_check": direct_cap,
                    "current_price": current_price, "spread_bps": spread_bps,
                    "portfolio_value": equity, "timestamp": datetime.now().isoformat(),
                }

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