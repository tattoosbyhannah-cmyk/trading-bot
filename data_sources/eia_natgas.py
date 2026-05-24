"""
EIA Natural Gas Fundamentals — Lower 48 working gas storage from EIA API.
"""

import os
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(_ENV if _ENV.exists() else None)

from data_sources.base_source import BaseFundamentalsSource, FundamentalsSnapshot


class EIANatgasSource(BaseFundamentalsSource):

    def fetch(self, symbol: str, as_of_date: str = None) -> FundamentalsSnapshot:
        try:
            raw = self._fetch_storage(as_of_date=as_of_date)
        except Exception as e:
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="natgas",
                timestamp=datetime.now().isoformat(),
                signals={}, llm_summary="", source_ids=["eia_natgas"],
                error=str(e),
            )

        signals = self._compute_signals(raw)
        if signals.get("error"):
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="natgas",
                timestamp=datetime.now().isoformat(),
                signals=signals, llm_summary="", source_ids=["eia_natgas"],
                error=signals["error"],
            )

        llm_summary = self._build_prompt(symbol, signals)
        return FundamentalsSnapshot(
            symbol=symbol, asset_class="natgas",
            timestamp=datetime.now().isoformat(),
            signals=signals, llm_summary=llm_summary,
            source_ids=["eia_natgas"],
        )

    def get_release_calendar(self) -> list[dict]:
        return [
            {"event": "EIA Natural Gas Storage Report", "day_of_week": "Thursday",
             "time_et": "10:30 AM", "description": "Weekly Lower 48 working gas inventory"},
        ]

    def _fetch_storage(self, as_of_date: str = None) -> dict:
        url = "https://api.eia.gov/v2/natural-gas/stor/wkly/data/"
        params = {
            "api_key": os.getenv("EIA_API_KEY"),
            "frequency": "weekly",
            "data[0]": "value",
            "facets[process][]": "SWO",
            "facets[duoarea][]": "R48",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 52,
        }
        if as_of_date:
            params["end"] = as_of_date  # EIA: inclusive cutoff
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        rows = list(reversed(r.json()["response"]["data"]))
        if as_of_date:
            rows = [row for row in rows if row["period"] <= as_of_date]
        return {"storage": [{"period": row["period"], "value": int(row["value"])} for row in rows]}

    def _compute_signals(self, raw: dict) -> dict:
        stor = raw.get("storage")
        if not stor or len(stor) < 20:
            return {"error": "insufficient data"}

        values = [r["value"] for r in stor]
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

        if abs(week_change_pct) < 1.0:
            week_surprise = "inline"
        elif week_change_pct > 0:
            week_surprise = "injection"
        else:
            week_surprise = "withdrawal"

        return {
            "asset_class": "natgas",
            "latest_storage_bcf": latest,
            "weekly_change_bcf": week_change,
            "weekly_change_pct": round(week_change_pct, 2),
            "week_surprise": week_surprise,
            "avg_4w_bcf": round(avg_4w),
            "avg_52w_bcf": round(avg_52w),
            "above_52w_avg": latest > avg_52w,
            "range_pos_pct": round(range_pos, 1),
            "range_zone": range_zone,
            "trend_4w": trend_4w,
            "num_weeks": len(values),
        }

    def _build_prompt(self, symbol: str, s: dict) -> str:
        return f"""Natural gas storage signals (Lower 48 working gas, weekly):
- Latest storage: {s['latest_storage_bcf']:,} Bcf
- Weekly change: {s['weekly_change_bcf']:+,} Bcf ({s['weekly_change_pct']:+.2f}%)
- week_surprise: {s['week_surprise']} (injection = supply added = bearish natgas; withdrawal = supply drawn = bullish natgas)
- 4-week avg: {s['avg_4w_bcf']:,} Bcf
- 52-week avg: {s['avg_52w_bcf']:,} Bcf
- above_52w_avg: {s['above_52w_avg']} (True = storage abundant = bearish natgas)
- range_pos_pct: {s['range_pos_pct']}% of 52-week range (100 = at 52w high)
- range_zone: {s['range_zone']} (upper_quartile = bearish; lower_quartile = bullish; mid_range = neutral)
- trend_4w: {s['trend_4w']} (rising storage = bearish; falling = bullish)

Rules:
- Use ONLY the categorical signals (week_surprise, range_zone, trend_4w, above_52w_avg).
- Storage injection is BEARISH for natgas prices; withdrawal is BULLISH.
- Natural gas is highly seasonal — storage context matters more than single-week changes.
- Cite signal names in rationale.
- {symbol} is a natural-gas-tracking ETF, so nat gas fundamentals translate directly to its price."""
