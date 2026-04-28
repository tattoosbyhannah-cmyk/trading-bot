"""
Position Sizing Contract Tests — enforce percent-units convention
and correct share calculations.

Convention: position_size_pct is ALWAYS in percent units (5.0 = 5%).
calculate_share_quantity divides by 100 exactly once.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestCalculateShareQuantity:
    """Verify calculate_share_quantity produces correct share counts."""

    def _calc(self, position_size_pct, current_price, portfolio_value=100_000):
        from paper_trading_executor import PaperTradingManager
        manager = PaperTradingManager.__new__(PaperTradingManager)
        manager.get_portfolio_value = lambda: portfolio_value
        return manager.calculate_share_quantity("TEST", position_size_pct, current_price)

    def test_5_pct_uso_price(self):
        """5% of $100k at $75/share = $5000 / $75 = 66 shares."""
        shares = self._calc(5.0, 75.0)
        assert shares == 66, f"Expected 66, got {shares}. Likely double-division bug."

    def test_375_pct_uso_price(self):
        """3.75% of $100k at $130/share = $3750 / $130 = 28 shares."""
        shares = self._calc(3.75, 130.0)
        assert shares == 28, f"Expected 28, got {shares}."

    def test_5_pct_ung_price(self):
        """5% of $100k at $10.50/share = $5000 / $10.50 = 476 shares."""
        shares = self._calc(5.0, 10.50)
        assert shares == 476, f"Expected 476, got {shares}."

    def test_5_pct_gld_price(self):
        """5% of $100k at $430/share = $5000 / $430 = 11 shares."""
        shares = self._calc(5.0, 430.0)
        assert shares == 11, f"Expected 11, got {shares}."

    def test_min_1_share(self):
        """Even tiny position sizes produce at least 1 share."""
        shares = self._calc(0.01, 500.0)
        assert shares == 1, f"Expected 1 (floor), got {shares}."

    def test_zero_pct_zero_shares(self):
        """0% position = 0 shares, not 1."""
        shares = self._calc(0.0, 100.0)
        assert shares == 0

    def test_not_fraction_bug(self):
        """If someone passes 0.05 (fraction) instead of 5.0 (percent),
        result should be 0 or 1 share — NOT 66. This catches the
        double-division class of bugs at the boundary."""
        shares = self._calc(0.05, 75.0)
        # 0.05% of 100k = $50 / $75 = 0 shares → max 1
        assert shares == 1, (
            f"Got {shares}. If this is 66, someone is treating "
            f"position_size_pct as a fraction (0.05) instead of percent (5.0)."
        )


class TestRiskGatekeeperOutput:
    """Verify risk gatekeeper output is in percent units."""

    def test_position_size_in_percent_range(self):
        """Risk gatekeeper position_size_pct must be in [0, 10] (percent units)."""
        from db.queries import load_recent_decisions
        decisions = load_recent_decisions("USO", days=30)
        if not decisions:
            pytest.skip("No recent USO decisions")
        for d in decisions:
            pct = d.get("position_size_pct")
            if pct is None:
                continue
            assert 0 <= pct <= 10, (
                f"position_size_pct={pct} outside [0, 10] percent range. "
                f"If this is 0.05, it's a fraction not a percent."
            )

    def test_no_accidental_fractions(self):
        """No position_size_pct should be in the (0, 0.1) range,
        which would indicate fraction-not-percent confusion."""
        from db.queries import load_all_decisions
        decisions = load_all_decisions()
        if not decisions:
            pytest.skip("No decisions")
        for d in decisions:
            pct = d.get("position_size_pct")
            if pct is None or pct == 0:
                continue
            assert pct >= 0.1, (
                f"position_size_pct={pct} is suspiciously small. "
                f"Likely a fraction (should be {pct * 100}% instead)."
            )


class TestSpreadCircuitBreaker:
    """Verify the 200 bps spread reject circuit-breaker.

    The cost adjuster was removed Apr 27, 2026. The only safety check on
    spread is now a hard reject above 200 bps — the flash-crash / halt case.
    Anything below 200 bps sizes at the gatekeeper's full position_size_pct.
    """

    def _exec(self, spread_bps, position_size=5.0, current_price=75.0):
        """Run execute_master_decision with mocked broker and given spread."""
        from unittest.mock import patch, MagicMock
        import paper_trading_executor as pte
        from master_orchestrator import MasterTradingDecision

        decision = MasterTradingDecision(
            symbol="USO",
            timestamp="2026-04-27T10:00:00",
            final_decision="LONG",
            confidence=7,
            position_size=position_size,
            entry_price=current_price,
            stop_loss=None,
            price_target=None,
            stop_loss_pct=None,
            price_target_pct=None,
            key_thesis="test",
            risk_factors=[],
            catalyst_timeline=[],
            agent_consensus={},
        )

        manager = MagicMock()
        manager.get_current_price.return_value = current_price
        manager.get_portfolio_value.return_value = 100_000
        manager.get_current_positions.return_value = {}
        manager.calculate_share_quantity.side_effect = (
            lambda sym, pct, price: max(int(100_000 * (pct / 100) / price), 1) if pct > 0 else 0
        )
        trade = MagicMock()
        trade.success = True
        trade.order_id = "test-order"
        trade.filled_qty = 66
        trade.filled_price = current_price
        manager.execute_long_trade.return_value = trade

        with patch.object(pte, "PaperTradingManager", return_value=manager), \
             patch.object(pte, "estimate_spread", return_value={
                 "bid": current_price - 0.01,
                 "ask": current_price + 0.01,
                 "mid": current_price,
                 "spread_bps": spread_bps,
             }):
            return pte.execute_master_decision(decision), manager

    def test_reject_above_200bps(self):
        """Spread > 200 bps must reject the trade entirely (no order submitted)."""
        result, manager = self._exec(spread_bps=201)
        assert result["success"] is False
        assert "circuit-breaker" in result["error"].lower()
        assert result["spread_bps"] == 201
        manager.execute_long_trade.assert_not_called()

    def test_reject_far_above_200bps(self):
        """Catastrophic stale-quote case (Apr 21 USO 283 bps)."""
        result, manager = self._exec(spread_bps=283.6)
        assert result["success"] is False
        manager.execute_long_trade.assert_not_called()

    def test_pass_at_exactly_200bps(self):
        """Threshold is strictly greater than 200 — 200 itself passes."""
        result, manager = self._exec(spread_bps=200)
        manager.execute_long_trade.assert_called_once()
        # Full position size used (no cost adjustment)
        call = manager.calculate_share_quantity.call_args
        assert call.args[1] == 5.0

    def test_pass_at_50bps_warn_threshold(self):
        """Spread at 50 bps logs WARNING but trade still goes through full-size."""
        result, manager = self._exec(spread_bps=75)
        manager.execute_long_trade.assert_called_once()
        call = manager.calculate_share_quantity.call_args
        assert call.args[1] == 5.0, (
            f"Expected 5.0% (full size, no cost adjustment), got {call.args[1]}"
        )

    def test_full_size_at_normal_spread(self):
        """Normal RTH spread (~5 bps) sizes at the gatekeeper's full pct."""
        result, manager = self._exec(spread_bps=5, position_size=3.75)
        manager.execute_long_trade.assert_called_once()
        call = manager.calculate_share_quantity.call_args
        assert call.args[1] == 3.75

    def test_no_cost_adjuster_function(self):
        """The cost adjuster was removed — these symbols must not exist."""
        import paper_trading_executor as pte
        assert not hasattr(pte, "adjust_for_costs"), (
            "adjust_for_costs should be removed (Option D, Apr 27 2026)."
        )
        assert not hasattr(pte, "_avg_historical_slippage"), (
            "_avg_historical_slippage should be removed (Option D, Apr 27 2026)."
        )
