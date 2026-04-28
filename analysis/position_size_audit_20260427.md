# Position Size Audit — April 27, 2026

## Root Cause

The 1-share symptom was NOT a double-division bug. It was caused by the
old 0.2% base position size (set Apr 15, in production through Apr 23).
At 0.2% of $100k = $200 / $130 per USO share = 1 share.

The 5% base was set Apr 24. The risk gatekeeper was immediately rejecting
everything (risk_score 9-10) because it saw "5% position" in its prompt.
That was fixed the same day by removing position_size from the gatekeeper
prompt. But `--execute` was disabled in daily_pipeline.sh on Apr 25 before
the fixed gatekeeper ran with `--execute` enabled.

**The new 5% base has never actually executed a trade.**

## Audit Table — position_size_pct at every site

| File:Line | Variable | Scale | Value (example) | Math correct? |
|-----------|----------|-------|-----------------|---------------|
| master_orchestrator.py:183 | `base_position_size` | percent | 5.0 | ✓ source of truth |
| risk_gatekeeper.py:201 | `position_size_pct` (input) | percent | 5.0 | ✓ passed through |
| risk_gatekeeper.py:275 | `assessment.position_size_pct` (output) | percent | 3.75 (risk_score 5) | ✓ scaling correct |
| master_orchestrator.py:441 | `final_position_size = risk.position_size_pct` | percent | 3.75 | ✓ pass-through |
| master_orchestrator.py:232 | `position_size=risk_position_pct` in MasterTradingDecision | percent | 3.75 | ✓ stored as-is |
| majority_vote_orchestrator.py:150 | playbook `position_size_pct` | percent | 5.0 (hardcoded) | ✓ correct |
| majority_vote_orchestrator.py:239 | run_record `position_size` | percent | 3.75 (from decision) | ✓ logging only |
| majority_vote_orchestrator.py:337 | outcome record `position_size_pct` | percent | 5.0 (hardcoded) | ✓ correct |
| paper_trading_executor.py:170-174 | `calculate_share_quantity` | percent | 3.75 → `value * (pct / 100)` | ✓ divides by 100 once |
| paper_trading_executor.py:343 | `adjust_for_costs(decision.position_size, ...)` | percent | 3.75 → adjusted | ✓ returns percent |
| intraday/swing_executor.py:335 | `portfolio_val * (sized_pct / 100)` | percent | from playbook | ✓ divides by 100 once |

## Convention

**Single source of truth:** `position_size_pct` is always in PERCENT UNITS.
- 5.0 means 5% of portfolio
- Never 0.05 (fraction)
- `calculate_share_quantity` divides by 100 exactly once
- No double-division anywhere in the chain

## Conclusion

No code fix needed for the scaling chain. The chain is correct.
The 1-share symptom was from the old 0.2% base, not a bug.
