"""
FRED Silver Fundamentals — macro drivers similar to gold + industrial demand.

Stub for future SLV expansion. Uses same macro signals as gold (real yields,
DXY, inflation) plus silver-specific industrial production indicators.
"""

import os
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(_ENV if _ENV.exists() else None)

from data_sources.base_source import BaseFundamentalsSource, FundamentalsSnapshot
from data_sources.fred_gold import FREDGoldSource


class FREDSilverSource(BaseFundamentalsSource):

    def __init__(self):
        # Silver shares most macro drivers with gold
        self._gold_source = FREDGoldSource()

    def fetch(self, symbol: str, as_of_date: str = None) -> FundamentalsSnapshot:
        # Start with gold's macro signals
        gold_snap = self._gold_source.fetch(symbol, as_of_date=as_of_date)
        if gold_snap.error:
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="silver",
                timestamp=datetime.now().isoformat(),
                signals={}, llm_summary="", source_ids=["fred_silver"],
                error=gold_snap.error,
            )

        # Add silver-specific: industrial production index
        try:
            ip_data = self._fetch_industrial_production(as_of_date=as_of_date)
            gold_snap.signals["industrial_production_trend"] = ip_data["trend"]
            gold_snap.signals["ip_latest"] = ip_data["latest"]
        except Exception:
            gold_snap.signals["industrial_production_trend"] = "unavailable"

        gold_snap.signals["asset_class"] = "silver"
        gold_snap.asset_class = "silver"
        gold_snap.source_ids = ["fred_silver", "fred_gold"]

        llm_summary = self._build_prompt(symbol, gold_snap.signals)
        gold_snap.llm_summary = llm_summary
        return gold_snap

    def get_release_calendar(self) -> list[dict]:
        cal = self._gold_source.get_release_calendar()
        cal.append({
            "event": "Industrial Production Index",
            "day_of_week": "varies (monthly)",
            "time_et": "9:15 AM",
            "description": "Fed industrial production — silver has significant industrial demand",
        })
        return cal

    def _fetch_industrial_production(self, as_of_date: str = None) -> dict:
        """Fetch FRED Industrial Production Index (INDPRO)."""
        params = {
            "series_id": "INDPRO",
            "api_key": os.getenv("FRED_API_KEY"),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 6,
        }
        if as_of_date:
            params["observation_end"] = as_of_date
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params=params, timeout=10,
        )
        r.raise_for_status()
        obs = [o for o in r.json().get("observations", [])
               if o["value"] != "." and (not as_of_date or o["date"] <= as_of_date)]
        if len(obs) < 3:
            return {"trend": "insufficient", "latest": None}
        latest = float(obs[0]["value"])
        prev = float(obs[2]["value"])
        pct = (latest - prev) / prev * 100
        if pct > 0.5:
            trend = "rising"
        elif pct < -0.5:
            trend = "falling"
        else:
            trend = "flat"
        return {"trend": trend, "latest": round(latest, 1)}

    def _build_prompt(self, symbol: str, s: dict) -> str:
        ip_note = ""
        if s.get("industrial_production_trend") != "unavailable":
            ip_note = f"""
- Industrial Production: {s.get('ip_latest')} | trend: {s.get('industrial_production_trend')}
  (rising IP = bullish silver via industrial demand; falling = bearish)"""

        return f"""Silver macro driver signals (shares gold drivers + industrial demand):
- US Dollar Index (DXY): {s.get('dxy_latest')} | 20-day trend: {s.get('dxy_trend_20d')}
  (falling dollar = bullish silver)
- 10Y Real Yield (TIPS): {s.get('real_yield_10y')}% | 20-day trend: {s.get('real_yield_trend_20d')}
  (falling real yields = bullish silver)
- 10Y Breakeven Inflation: {s.get('breakeven_10y')}% | 20-day trend: {s.get('breakeven_trend_20d')}
  (rising inflation expectations = bullish silver){ip_note}
- Composite macro_bias: {s.get('macro_bias')} ({s.get('bullish_signals', 0)} bullish / {s.get('bearish_signals', 0)} bearish)

Rules:
- Silver is driven by BOTH precious metals macro (like gold) AND industrial demand.
- When macro_bias and industrial_production disagree, weight industrial production higher
  because silver's industrial use is ~50% of demand (vs gold's <10%).
- Cite signal names in rationale.
- {symbol} is a silver-tracking ETF."""
