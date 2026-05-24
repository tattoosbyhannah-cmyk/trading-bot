"""
EIA Crude Oil Fundamentals — US commercial crude inventory from EIA API.
"""

import os
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(_ENV if _ENV.exists() else None)

from data_sources.base_source import BaseFundamentalsSource, FundamentalsSnapshot


class EIACrudeSource(BaseFundamentalsSource):

    def fetch(self, symbol: str, as_of_date: str = None) -> FundamentalsSnapshot:
        try:
            raw = self._fetch_inventory(as_of_date=as_of_date)
        except Exception as e:
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="oil",
                timestamp=datetime.now().isoformat(),
                signals={}, llm_summary="", source_ids=["eia_crude"],
                error=str(e),
            )

        signals = self._compute_signals(raw)
        if signals.get("error"):
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="oil",
                timestamp=datetime.now().isoformat(),
                signals=signals, llm_summary="", source_ids=["eia_crude"],
                error=signals["error"],
            )

        llm_summary = self._build_prompt(symbol, signals)
        return FundamentalsSnapshot(
            symbol=symbol, asset_class="oil",
            timestamp=datetime.now().isoformat(),
            signals=signals, llm_summary=llm_summary,
            source_ids=["eia_crude"],
        )

    def get_release_calendar(self) -> list[dict]:
        return [
            {"event": "EIA Crude Oil Inventories", "day_of_week": "Wednesday",
             "time_et": "10:30 AM", "description": "Weekly US commercial crude stockpiles"},
        ]

    def _fetch_inventory(self, as_of_date: str = None) -> dict:
        url = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
        params = {
            "api_key": os.getenv("EIA_API_KEY"),
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": "WCRSTUS1",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 52,
        }
        if as_of_date:
            params["end"] = as_of_date  # EIA: inclusive cutoff
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        rows = list(reversed(r.json()["response"]["data"]))
        # Defensive: post-filter in case API returned extra
        if as_of_date:
            rows = [row for row in rows if row["period"] <= as_of_date]
        return {"inventory": [{"period": row["period"], "value": int(row["value"])} for row in rows]}

    def _compute_signals(self, raw: dict) -> dict:
        inv = raw.get("inventory")
        if not inv or len(inv) < 20:
            return {"error": "insufficient data"}

        values = [r["value"] for r in inv]
        latest = values[-1]
        prev_week = values[-2]
        week_change = latest - prev_week
        week_change_pct = (week_change / prev_week) * 100

        avg_4w = sum(values[-4:]) / 4
        avg_52w = sum(values) / len(values)
        max_52w = max(values)
        min_52w = min(values)

        range_pos = ((latest - min_52w) / (max_52w - min_52w)) * 100 if max_52w != min_52w else 50
        trend_4w = "rising" if avg_4w > values[-8] else "falling" if avg_4w < values[-8] else "flat"

        if range_pos > 75:
            range_zone = "upper_quartile"
        elif range_pos < 25:
            range_zone = "lower_quartile"
        else:
            range_zone = "mid_range"

        if abs(week_change_pct) < 0.3:
            week_surprise = "inline"
        elif week_change_pct > 0:
            week_surprise = "build"
        else:
            week_surprise = "draw"

        return {
            "asset_class": "oil",
            "latest_inventory_kbbl": latest,
            "weekly_change_kbbl": week_change,
            "weekly_change_pct": round(week_change_pct, 2),
            "week_surprise": week_surprise,
            "avg_4w_kbbl": round(avg_4w),
            "avg_52w_kbbl": round(avg_52w),
            "above_52w_avg": latest > avg_52w,
            "range_pos_pct": round(range_pos, 1),
            "range_zone": range_zone,
            "trend_4w": trend_4w,
            "num_weeks": len(values),
        }

    def _build_prompt(self, symbol: str, s: dict) -> str:
        return f"""Crude oil physical supply signals (US commercial ex-SPR, weekly):
- Latest inventory: {s['latest_inventory_kbbl']:,} thousand barrels
- Weekly change: {s['weekly_change_kbbl']:+,} kbbl ({s['weekly_change_pct']:+.2f}%)
- week_surprise: {s['week_surprise']} (build = supply added = bearish crude; draw = supply drawn = bullish crude)
- 4-week avg: {s['avg_4w_kbbl']:,} kbbl
- 52-week avg: {s['avg_52w_kbbl']:,} kbbl
- above_52w_avg: {s['above_52w_avg']} (True = supply abundant = bearish crude)
- range_pos_pct: {s['range_pos_pct']}% of 52-week range (100 = at 52w high)
- range_zone: {s['range_zone']} (upper_quartile = bearish; lower_quartile = bullish; mid_range = neutral)
- trend_4w: {s['trend_4w']} (rising supply = bearish; falling = bullish)

Rules:
- Use ONLY the categorical signals (week_surprise, range_zone, trend_4w, above_52w_avg).
- Inventory building is BEARISH for crude prices; inventory drawing is BULLISH.
- Your bias must follow from the dominant signals, not individual numeric comparisons.
- Cite signal names in rationale.
- {symbol} is a crude-oil-tracking ETF, so crude fundamentals translate directly to its price."""
