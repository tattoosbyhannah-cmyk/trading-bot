# Morning Checklist — First Automated Trading Day

**Created:** 2026-04-27 evening
**For:** the morning after deploying scheduled timers + intraday executor
**Purpose:** verify tomorrow's automated run worked, catch problems early

---

## 1. First thing — check email (target: ~07:15 PT)

Three emails should land between 07:00 and 14:30 PT:

- [ ] **~07:00 PT — Daily Pipeline Summary**
  - Subject contains "TRADING BOT" or similar
  - Should list decisions for GLD, UNG, USO with direction + confidence
  - Red flag: missing email, error subject line, or no decisions

- [ ] **~13:10 PT — Intraday Summary**
  - First time this email reflects auto-scheduled intraday activity
  - Should list any trades the executor made (or "no trades today")
  - Red flag: error subject, or trades you didn't expect

- [ ] **~14:15 PT — (silent) trading-scorer second pass**
  - No email expected; runs in the background
  - Verify with `tail ~/trading-bot/logs/daily_pipeline.log`

## 2. Verify scheduled timers actually fired

```bash
systemctl --user list-timers | grep -E "trading|intraday|swing"
```

For each timer, the `LAST` column should show a time from this morning, not yesterday:

- [ ] `trading-pipeline.timer` — LAST should show today ~06:35 PT
- [ ] `intraday-swing.timer` — LAST should show today ~06:35 PT
- [ ] `intraday-summary.timer` — will show today ~13:10 once it fires
- [ ] `trading-scorer.timer` — will show today ~14:15 once it fires

If any show yesterday's date for the morning timers, they didn't fire. Check:

```bash
systemctl --user status trading-pipeline.service
systemctl --user status intraday-swing.service
journalctl --user -u trading-pipeline.service --since "06:00" -n 50
```

## 3. Read the intraday log

```bash
tail -100 ~/trading-bot/logs/intraday_swing.log
```

Looking for:

- [ ] Stream subscribed to bars (USO, possibly UNG/GLD)
- [ ] Signal evaluations with strength scores
- [ ] Trades placed (or clear reason why not)
- [ ] No tracebacks, no Python errors
- [ ] Wash-trade rejections — possibly recurring from yesterday's stale orders. Handled gracefully but worth noting.

## 4. Check paper account state

```bash
cd ~/trading-bot && source venv/bin/activate && python -c "
import sys; sys.path.insert(0, '.')
from brokers.broker_factory import get_broker
b = get_broker('alpaca')
acct = b.get_account()
print(f'Equity: \${acct[\"equity\"]:,.2f}  Cash: \${acct[\"cash\"]:,.2f}')
positions = b.get_all_positions()
print(f'Open positions: {len(positions)}')
for p in positions:
    print(f'  {p}')
"
```

Compare to last night's snapshot:

- Equity: $100,026.31
- Cash: $99,305.01
- Positions (3): GLD x1 @ $434.44, UNG x2 @ $10.84, USO x2 @ $128.11
- Pre-deployment unrealized P&L: +$8.97

Red flags: equity moved more than ~1% (would be unusual for paper account), positions in symbols other than USO/UNG/GLD, account in margin call.

## 5. Verify decisions written to DB

```bash
cd ~/trading-bot && source venv/bin/activate && python health_check.py
```

Outcomes line should show **35-36 total decisions** (32 from yesterday + 3 from today's morning run, possibly 4 if intraday added one).

## 6. Known issues to expect (not bugs)

- **Scorer SIP error**: `subscription does not permit querying recent SIP data`. Surfaced last night running `score_outcomes.py`. Fix: add `feed='iex'` to Alpaca bar fetches in `score_outcomes.py`. Doesn't crash the pipeline but blocks scoring of recent-horizon decisions until fixed.

- **Wash-trade rejections** in intraday log. Caused by stale stop/target orders attached to yesterday's positions (USO held_for_orders = 2). Bot handles gracefully. Will resolve once stale orders cancel or expire.

- **No emails before 07:00 PT** — the daily pipeline takes ~20 min to run, so the summary email lands around 06:55-07:05 PT.

## 7. Kill switches (in order of escalation)

```bash
# Soft stop — graceful shutdown of intraday executor
systemctl --user stop intraday-swing.service

# Disable timer — won't restart tomorrow
systemctl --user disable --now intraday-swing.timer

# Touch kill switch file — bot refuses new trades but keeps running
touch ~/trading-bot/.kill_switch  # verify path in paper_trading_executor.py

# Nuclear — stop and disable everything trading-related
systemctl --user stop intraday-swing.service trading-pipeline.service intraday-summary.service trading-scorer.service
systemctl --user disable intraday-swing.timer trading-pipeline.timer intraday-summary.timer trading-scorer.timer
```

## 8. If everything looks fine

- [ ] Don't change anything yet
- [ ] Don't fix the findings from yesterday's analysis (small sample)
- [ ] Don't enable `--execute` in `daily_pipeline.sh`
- [ ] Watch one more day of clean automated runs before any changes
- [ ] If you want to do work, fix the SIP issue (item 6 above) — it's the smallest, lowest-risk improvement and surfaces real data immediately

## 9. What "good" looks like by end of day

- 3 emails received, all sensible
- All 4 timers show today's LAST in `list-timers`
- intraday_swing.log shows clean execution from 10:00 ET to 16:00 ET
- 3-5 new decisions in DB (depending on intraday activity)
- Account equity within ±0.5% of last night
- No uninvestigated tracebacks anywhere

## 10. What to do tonight (Tuesday evening)

If today went clean:

- Fix the SIP issue (small, well-defined task)
- Re-run yesterday's decision analysis on a fresh date range to see if numbers shift
- Consider starting the backtest schematic implementation (the schematic itself is at `analysis/backtest_schematic_20260427.md`)
- Cancel stale held orders on Alpaca paper account once market is open

If today did NOT go clean:

- Stop. Document what went wrong. Don't make changes while debugging.
- Re-read yesterday's session log at `docs/session_log_20260427.md`
- Use the kill switches in section 7 if anything looks unsafe
