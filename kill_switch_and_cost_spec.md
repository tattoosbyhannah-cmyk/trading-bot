# Kill Switch + Cost/Slippage Modeling — Implementation Spec

## Part 1: Kill Switch

### Problem
No way to halt trading instantly. If the system makes a bad decision or market conditions spike, we need to:
1. Stop all new orders immediately
2. Cancel any pending/open orders
3. Optionally liquidate all positions
4. Prevent the system from placing new orders until manually re-enabled

### Design

**Three components:**

#### 1A. Kill switch state file: `~/trading-bot/KILL_SWITCH`

Simple file-based flag. If the file exists, no orders can be placed.

- `touch ~/trading-bot/KILL_SWITCH` → system halted
- `rm ~/trading-bot/KILL_SWITCH` → system re-enabled

File-based is better than DB/env because:
- Works even if Python crashes
- Can be triggered from any terminal, cron, or monitoring script
- Survives reboots (persistent on disk)
- No dependencies

#### 1B. Kill switch enforcement in `paper_trading_executor.py`

Add a check at the top of every order submission path. Before calling `submit_order()`:

```python
from pathlib import Path

KILL_SWITCH_FILE = Path(__file__).parent / "KILL_SWITCH"

def check_kill_switch():
    """Raise if kill switch is engaged. Check BEFORE every order."""
    if KILL_SWITCH_FILE.exists():
        reason = KILL_SWITCH_FILE.read_text().strip() or "No reason given"
        raise RuntimeError(
            f"KILL SWITCH ENGAGED: {reason}. "
            f"Remove {KILL_SWITCH_FILE} to re-enable trading."
        )
```

Call `check_kill_switch()` as the FIRST line in any function that calls `submit_order()`.

Also add it to `majority_vote_orchestrator.py` at the top of each vote run — fail fast before spending 2.5 min on inference if trading is halted.

#### 1C. CLI kill switch script: `kill_switch.py`

Standalone script for emergency use:

```python
#!/usr/bin/env python3
"""
Emergency kill switch for trading bot.

Usage:
    python kill_switch.py engage [reason]     # Stop all trading + cancel orders
    python kill_switch.py engage --liquidate  # Stop + cancel + close all positions
    python kill_switch.py disengage           # Re-enable trading
    python kill_switch.py status              # Check current state
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

KILL_SWITCH_FILE = Path(__file__).parent / "KILL_SWITCH"

def engage(reason: str, liquidate: bool = False):
    """Engage kill switch: write flag file, cancel orders, optionally liquidate."""
    from dotenv import load_dotenv
    import os
    load_dotenv()
    
    from alpaca.trading.client import TradingClient
    
    client = TradingClient(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
        paper=True
    )
    
    timestamp = datetime.now().isoformat()
    flag_content = f"{timestamp} | {reason}"
    
    # 1. Write kill switch file FIRST (prevents new orders immediately)
    KILL_SWITCH_FILE.write_text(flag_content)
    print(f"[KILL] Kill switch ENGAGED at {timestamp}")
    print(f"[KILL] Reason: {reason}")
    
    # 2. Cancel all open orders
    try:
        cancelled = client.cancel_orders()
        print(f"[KILL] Cancelled all open orders")
    except Exception as e:
        print(f"[KILL] Warning: failed to cancel orders: {e}")
    
    # 3. Optionally liquidate all positions
    if liquidate:
        try:
            client.close_all_positions(cancel_orders=True)
            print(f"[KILL] Liquidated all positions")
        except Exception as e:
            print(f"[KILL] Warning: failed to liquidate: {e}")
    
    # 4. Log to agent_calls.jsonl for audit trail
    try:
        import json
        log_entry = {
            "timestamp": timestamp,
            "agent": "kill_switch",
            "action": "ENGAGED",
            "reason": reason,
            "liquidated": liquidate,
        }
        with open("logs/agent_calls.jsonl", "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass  # Don't let logging failure block the kill switch
    
    print(f"\n[KILL] Trading is HALTED. Run 'python kill_switch.py disengage' to resume.")


def disengage():
    """Remove kill switch file to re-enable trading."""
    if not KILL_SWITCH_FILE.exists():
        print("[KILL] Kill switch is not engaged. Nothing to do.")
        return
    
    old_content = KILL_SWITCH_FILE.read_text().strip()
    KILL_SWITCH_FILE.unlink()
    
    timestamp = datetime.now().isoformat()
    print(f"[KILL] Kill switch DISENGAGED at {timestamp}")
    print(f"[KILL] Was engaged: {old_content}")
    
    # Log
    try:
        import json
        log_entry = {
            "timestamp": timestamp,
            "agent": "kill_switch",
            "action": "DISENGAGED",
            "previous_reason": old_content,
        }
        with open("logs/agent_calls.jsonl", "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass


def status():
    """Check kill switch state and show open positions/orders."""
    if KILL_SWITCH_FILE.exists():
        content = KILL_SWITCH_FILE.read_text().strip()
        print(f"[KILL] Status: ENGAGED")
        print(f"[KILL] Details: {content}")
    else:
        print(f"[KILL] Status: DISENGAGED (trading enabled)")
    
    # Show current positions and orders
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        from alpaca.trading.client import TradingClient
        
        client = TradingClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            paper=True
        )
        
        account = client.get_account()
        positions = client.get_all_positions()
        orders = client.get_orders()
        
        print(f"\n[PORTFOLIO] Equity: ${float(account.equity):,.2f}")
        print(f"[PORTFOLIO] Cash: ${float(account.cash):,.2f}")
        print(f"[PORTFOLIO] Open positions: {len(positions)}")
        for p in positions:
            print(f"  {p.symbol}: {p.qty} shares @ ${float(p.avg_entry_price):.2f} (P&L: ${float(p.unrealized_pl):.2f})")
        print(f"[PORTFOLIO] Open orders: {len(orders)}")
        for o in orders:
            print(f"  {o.symbol}: {o.side} {o.qty} @ {o.type} ({o.status})")
    except Exception as e:
        print(f"\n[PORTFOLIO] Could not fetch portfolio: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading bot kill switch")
    parser.add_argument("command", choices=["engage", "disengage", "status"])
    parser.add_argument("reason", nargs="*", default=["Manual kill switch"])
    parser.add_argument("--liquidate", action="store_true", help="Also close all positions")
    
    args = parser.parse_args()
    
    if args.command == "engage":
        engage(" ".join(args.reason), liquidate=args.liquidate)
    elif args.command == "disengage":
        disengage()
    elif args.command == "status":
        status()
```

#### 1D. Bash alias for instant access

Add to `~/.bashrc`:
```bash
alias killbot='python ~/trading-bot/kill_switch.py engage'
alias killbot-liq='python ~/trading-bot/kill_switch.py engage --liquidate'
alias botstat='python ~/trading-bot/kill_switch.py status'
alias unkill='python ~/trading-bot/kill_switch.py disengage'
```

### Testing

```bash
# Test engage
python kill_switch.py engage "Testing kill switch"
# Verify file exists
cat KILL_SWITCH
# Test that executor refuses orders
python -c "from paper_trading_executor import check_kill_switch; check_kill_switch()"
# Should raise RuntimeError

# Test disengage
python kill_switch.py disengage
# Test status
python kill_switch.py status
```

---

## Part 2: Cost/Slippage Modeling

### Problem
Position sizing assumes frictionless execution. In reality:
- Spread costs (bid-ask) eat into entries and exits
- Slippage (price moves between decision and fill) can be significant in volatile assets
- These costs compound and can turn marginally profitable strategies into losers
- No measurement of expected vs actual fill quality

### Design

#### 2A. Slippage tracker in `paper_trading_executor.py`

After every order fill, record:

```python
class FillRecord(BaseModel):
    timestamp: str
    symbol: str
    side: str                    # "buy" or "sell"
    decision_price: float        # price when orchestrator made decision
    expected_price: float        # price when order was submitted
    filled_price: float          # actual fill price from Alpaca
    quantity: float
    slippage_bps: float          # (filled - expected) / expected * 10000
    spread_estimate_bps: float   # estimated bid-ask spread at time of order
    total_cost_bps: float        # slippage + spread
    order_id: str
```

Log to `logs/fill_records.jsonl`.

Implementation: after `submit_order()` returns, poll `get_order_by_id()` until filled, then compute slippage.

#### 2B. Spread estimation

Before submitting an order, fetch the latest quote (bid/ask) from Alpaca:

```python
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.data.historical import StockHistoricalDataClient

def estimate_spread(symbol: str) -> dict:
    """Get current bid-ask spread."""
    quote = data_client.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=symbol)
    )
    q = quote[symbol]
    mid = (q.bid_price + q.ask_price) / 2
    spread_bps = (q.ask_price - q.bid_price) / mid * 10000
    return {
        "bid": q.bid_price,
        "ask": q.ask_price,
        "mid": mid,
        "spread_bps": spread_bps,
    }
```

#### 2C. Cost-adjusted position sizing

In `master_orchestrator.py` or `paper_trading_executor.py`, before submitting:

```python
def adjust_for_costs(base_position_pct: float, estimated_round_trip_bps: float) -> float:
    """Reduce position size if cost drag exceeds threshold.
    
    If round-trip costs (entry spread + slippage + exit spread + slippage)
    exceed 1% of expected gain, reduce position proportionally.
    """
    COST_CEILING_BPS = 50  # 0.5% round-trip ceiling
    
    if estimated_round_trip_bps > COST_CEILING_BPS:
        reduction_factor = COST_CEILING_BPS / estimated_round_trip_bps
        return base_position_pct * reduction_factor
    return base_position_pct
```

For paper trading: estimate round-trip costs as 2x current spread + 2x average historical slippage (from fill_records.jsonl).

#### 2D. Slippage dashboard query

Add a helper to analyze accumulated fill data:

```python
def slippage_report(since_days: int = 30):
    """Summarize fill quality from logs/fill_records.jsonl."""
    import json
    from datetime import datetime, timedelta
    from pathlib import Path
    
    cutoff = datetime.now() - timedelta(days=since_days)
    records = []
    
    path = Path("logs/fill_records.jsonl")
    if not path.exists():
        print("No fill records yet.")
        return
    
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if datetime.fromisoformat(r['timestamp']) > cutoff:
                records.append(r)
    
    if not records:
        print(f"No fills in last {since_days} days.")
        return
    
    slippages = [r['slippage_bps'] for r in records]
    spreads = [r['spread_estimate_bps'] for r in records]
    costs = [r['total_cost_bps'] for r in records]
    
    print(f"Fill records: {len(records)} trades in last {since_days} days")
    print(f"Avg slippage: {sum(slippages)/len(slippages):.1f} bps")
    print(f"Avg spread:   {sum(spreads)/len(spreads):.1f} bps")
    print(f"Avg total cost: {sum(costs)/len(costs):.1f} bps")
    print(f"Max slippage: {max(slippages):.1f} bps")
    print(f"P95 slippage: {sorted(slippages)[int(len(slippages)*0.95)]:.1f} bps")
    
    # Per-symbol breakdown
    by_symbol = {}
    for r in records:
        by_symbol.setdefault(r['symbol'], []).append(r['total_cost_bps'])
    
    print(f"\nPer-symbol avg cost:")
    for sym, costs_list in sorted(by_symbol.items()):
        print(f"  {sym}: {sum(costs_list)/len(costs_list):.1f} bps ({len(costs_list)} fills)")
```

### Integration points

1. `paper_trading_executor.py` — add `check_kill_switch()` call, add `FillRecord` logging, add spread estimation
2. `majority_vote_orchestrator.py` — add `check_kill_switch()` at top of each vote run
3. `kill_switch.py` — new standalone CLI script
4. `~/.bashrc` — add aliases

### Testing

```bash
# Kill switch
python kill_switch.py status
python kill_switch.py engage "Test"
python kill_switch.py status
python kill_switch.py disengage

# Cost model (needs a paper trade to generate fill data)
# After at least one fill:
python -c "from paper_trading_executor import slippage_report; slippage_report()"
```

### Estimated time: 2-3 hours total
- Kill switch (1A-1D): ~1 hour
- Cost/slippage (2A-2D): ~1.5 hours
