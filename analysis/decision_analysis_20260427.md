# Decision Analysis — Paper Trading Performance
**Date:** April 27, 2026  
**Period:** April 16–27, 2026 (8 trading days)  
**Sample:** 32 decisions, 20 scored, 15 directional-scored

---

## 1. Sample Composition

| Symbol | Total | Scored | % of Sample |
|--------|-------|--------|-------------|
| USO    | 14    | 8      | 44%         |
| UNG    | 9     | 6      | 28%         |
| GLD    | 9     | 6      | 28%         |

| Direction | Total | Scored |
|-----------|-------|--------|
| LONG      | 13    | 8      |
| SHORT     | 11    | 7      |
| HOLD      | 8     | 5      |

12 unscored decisions are all from Apr 24–27 (too recent + Alpaca SIP bar fetch failures on the free tier).

## 2. Hit Rates (n=15 scored directional)

| Metric | Overall | USO | UNG | GLD |
|--------|---------|-----|-----|-----|
| **Target hit** | 13% | 17% | 0% | 25% |
| **Stop hit** | 33% | 67% | 20% | 0% |
| **Neither** | 60% | — | — | — |

**By direction:** LONG hits targets 25% of the time (SHORT: 0%). SHORT hits stops 43% (LONG: 25%). The system is better at picking LONG entries than SHORT entries.

**USO is the problem child** — 67% of USO decisions hit their stop. This aligns with the Apr 16 Iran ceasefire crash that wiped out pre-sentiment-fix positions.

## 3. Returns (1-day horizon, n=15)

| Metric | Overall | USO | UNG | GLD |
|--------|---------|-----|-----|-----|
| **Mean** | -1.03% | -4.00% | +1.50% | +0.25% |
| **Median** | -0.45% | -6.83% | +1.40% | +0.31% |
| **Win rate** | 40% | 17% | 60% | 50% |
| **Best** | +5.78% | — | — | — |
| **Worst** | -7.79% | — | — | — |

**UNG is the best performer** on 1-day returns (+1.50% mean, 60% win rate). **GLD is near neutral** (+0.25%, 50% win). **USO is deeply negative** (-4.00% mean, 17% win rate) — dominated by the Apr 16 crash.

5-day returns (n=7 only): -1.44% mean. Too few observations for reliability.

## 4. Confidence Calibration

The bot produces confidence 8 in 56% of decisions and confidence 9 in 34%. The distribution is heavily concentrated:

```
 7: ██ (2)
 8: ██████████████████ (18)
 9: ███████████ (11)
10: █ (1)
```

**Confidence does NOT predict outcomes in this sample:**
- High confidence (≥8): mean -1.18%, 36% win rate
- Low confidence (<8): mean +1.07%, 100% win rate (n=1, not meaningful)

There is only 1 decision below confidence 8, so we cannot assess whether lower confidence correlates with worse outcomes. The confidence scores are not discriminating — the bot is almost always "confident 8."

## 5. Day-of-Week Patterns

| Day | n | Mean Return | Win Rate |
|-----|---|-------------|----------|
| Monday | 3 | -2.85% | 0% |
| Tuesday | 2 | +4.28% | 100% |
| Wednesday | 2 | +2.22% | 50% |
| Thursday | 5 | -3.12% | 20% |
| Friday | 3 | -1.45% | 67% |

Monday and Thursday look bad; Tuesday looks good. **Heavily underpowered** — n=2-5 per day. No conclusions possible.

## 6. HOLD Opportunity Cost

5 scored HOLD decisions. The literature_winner direction would have yielded +1.65% on average. 60% of HOLDs missed a profitable trade; 40% correctly avoided a loss.

**Interpretation:** The bot HOLDs too often when it should be trading. But this is confounded by the whipsaw detector forcing HOLDs on UNG — those may have been correct risk management even though the counterfactual return was positive.

## 7. Honest Limitations

- **Sample size:** 15 directional-scored decisions. No finding here is statistically significant. A 40% win rate with n=15 has a 95% confidence interval of roughly [16%, 68%] — we cannot distinguish the bot from random.
- **Period:** 8 trading days in a single market regime (Iran ceasefire + oil volatility). The USO results are dominated by one event.
- **Code changes during the period:** The sentiment fix (Apr 17), position sizing overhaul (Apr 24), and risk gatekeeper refactor (Apr 24) mean earlier and later decisions were produced by materially different systems. Pooling them is analytically questionable.
- **Survivorship:** 12 of 32 decisions are unscored due to Alpaca SIP subscription limits. The scored sample is biased toward older (pre-fix) decisions.
- **No transaction costs** are reflected. With the old 0.2% sizing, costs were trivial. With the new 5% sizing, spread costs matter.
- **Confidence is not calibrated.** The LLM produces confidence 8 for 56% of decisions. This is not useful signal — it's a reporting artifact, not a discriminating measure.
