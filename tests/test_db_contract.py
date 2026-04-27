"""
DB Contract Test — verifies SQL results use canonical keys and types.

Every row from db.queries must:
  - contain "timestamp" (canonical) and NOT "created_at" (raw DB column)
  - have float (not Decimal) for all numeric fields

The _normalize_row mapping in db/connection.py handles both.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.queries import load_recent_decisions, load_all_decisions, load_todays_agent_calls


@pytest.mark.parametrize("loader,args", [
    (load_recent_decisions, ("USO", 30)),
    (load_all_decisions, ()),
    (load_todays_agent_calls, ()),
])
def test_canonical_keys(loader, args):
    """Query results must have 'timestamp', not 'created_at'."""
    rows = loader(*args)
    if not rows:
        pytest.skip(f"{loader.__name__} returned no rows")

    for i, row in enumerate(rows[:5]):
        assert "timestamp" in row, (
            f"Row {i} from {loader.__name__} missing 'timestamp' key. "
            f"Keys: {list(row.keys())}"
        )
        assert "created_at" not in row, (
            f"Row {i} from {loader.__name__} has raw DB column 'created_at'. "
            f"Should be normalized to 'timestamp' by _normalize_row."
        )


@pytest.mark.parametrize("loader,args", [
    (load_recent_decisions, ("USO", 30)),
    (load_all_decisions, ()),
])
def test_numeric_fields_are_float(loader, args):
    """Numeric fields must be float, not Decimal. Boundary coercion in _normalize_row."""
    rows = loader(*args)
    if not rows:
        pytest.skip(f"{loader.__name__} returned no rows")

    numeric_keys = {
        "entry_price", "stop_loss", "stop_loss_pct", "price_target",
        "price_target_pct", "position_size_pct", "price_1d", "price_5d",
        "price_30d", "return_1d_pct", "return_5d_pct", "return_30d_pct",
        "opportunity_cost_pct",
    }

    for i, row in enumerate(rows[:5]):
        for key in numeric_keys:
            val = row.get(key)
            if val is None:
                continue
            assert not isinstance(val, Decimal), (
                f"Row {i} from {loader.__name__}: {key}={val!r} is Decimal, "
                f"should be float. _normalize_row boundary coercion missing?"
            )
