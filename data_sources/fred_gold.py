"""
FRED Gold Fundamentals — macro drivers (real yields, DXY, inflation expectations).
"""

import os
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(_ENV if _ENV.exists() else None)

from data_sources.base_source import BaseFundamentalsSource, FundamentalsSnapshot


class FREDGoldSource(BaseFundamentalsSource):

    def fetch(self, symbol: str) -> FundamentalsSnapshot:
        try:
            raw = self._fetch_macro()
        except Exception as e:
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="gold",
                timestamp=datetime.now().isoformat(),
                signals={}, llm_summary="", source_ids=["fred_gold"],
                error=str(e),
            )

        signals = self._compute_signals(raw)
        if signals.get("error"):
            return FundamentalsSnapshot(
                symbol=symbol, asset_class="gold",
                timestamp=datetime.now().isoformat(),
                signals=signals, llm_summary="", source_ids=["fred_gold"],
                error=signals["error"],
            )

        llm_summary = self._build_prompt(symbol, signals)
        return FundamentalsSnapshot(
            symbol=symbol, asset_class="gold",
            timestamp=datetime.now().isoformat(),
            signals=signals, llm_summary=llm_summary,
            source_ids=["fred_gold"],
        )

    def get_release_calendar(self) -> list[dict]:
        return [
            {"event": "FOMC Rate Decision", "day_of_week": "varies",
             "time_et": "2:00 PM", "description": "Federal Reserve interest rate decision (~8x/year)"},
            {"event": "CPI Inflation Data", "day_of_week": "varies",
             "time_et": "8:30 AM", "description": "Monthly consumer price index"},
            {"event": "NFP Jobs Report", "day_of_week": "Friday (1st)",
             "time_et": "8:30 AM", "description": "Monthly non-farm payrolls"},
        ]

    def _fred_latest(self, series_id: str, lookback: int = 60) -> list:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
            "series_id": series_id,
            "api_key": os.getenv("FRED_API_KEY"),
            "file_type": "json",
            "sort_order": "desc",
            "limit": lookback,
        }, timeout=10)
        r.raise_for_status()
        result = []
        for o in r.json().get("observations", []):
            if o["value"] != ".":
                result.append({"date": o["date"], "value": float(o["value"])})
        return list(reversed(result))

    def _fetch_macro(self) -> dict:
        return {
            "dxy": self._fred_latest("DTWEXBGS", 60),
            "real_yield_10y": self._fred_latest("DFII10", 60),
            "nominal_10y": self._fred_latest("DGS10", 60),
            "breakeven_10y": self._fred_latest("T10YIE", 60),
        }

    def _compute_signals(self, raw: dict) -> dict:
        def _trend(series, window=20):
            if len(series) < window + 1:
                return "insufficient"
            recent = series[-1]["value"]
            older = series[-(window + 1)]["value"]
            pct = (recent - older) / abs(older) * 100 if older != 0 else 0
            if pct > 1.0:
                return "rising"
            elif pct < -1.0:
                return "falling"
            return "flat"

        def _latest(series):
            return series[-1]["value"] if series else None

        def _change(series, window=5):
            if len(series) < window + 1:
                return None
            return round(series[-1]["value"] - series[-(window + 1)]["value"], 4)

        dxy = raw.get("dxy", [])
        real_yield = raw.get("real_yield_10y", [])
        breakeven = raw.get("breakeven_10y", [])

        if not all([dxy, real_yield, breakeven]):
            return {"error": "insufficient FRED data"}

        dxy_trend = _trend(dxy)
        real_yield_trend = _trend(real_yield)
        breakeven_trend = _trend(breakeven)

        bullish_count = 0
        bearish_count = 0
        if dxy_trend == "falling": bullish_count += 1
        elif dxy_trend == "rising": bearish_count += 1
        if real_yield_trend == "falling": bullish_count += 1
        elif real_yield_trend == "rising": bearish_count += 1
        if breakeven_trend == "rising": bullish_count += 1
        elif breakeven_trend == "falling": bearish_count += 1

        if bullish_count >= 2: macro_bias = "bullish"
        elif bearish_count >= 2: macro_bias = "bearish"
        else: macro_bias = "mixed"

        return {
            "asset_class": "gold",
            "dxy_latest": round(_latest(dxy), 2) if _latest(dxy) else None,
            "dxy_trend_20d": dxy_trend,
            "dxy_5d_change": _change(dxy, 5),
            "real_yield_10y": round(_latest(real_yield), 2) if _latest(real_yield) else None,
            "real_yield_trend_20d": real_yield_trend,
            "real_yield_5d_change": _change(real_yield, 5),
            "breakeven_10y": round(_latest(breakeven), 2) if _latest(breakeven) else None,
            "breakeven_trend_20d": breakeven_trend,
            "macro_bias": macro_bias,
            "bullish_signals": bullish_count,
            "bearish_signals": bearish_count,
        }

    def _build_prompt(self, symbol: str, s: dict) -> str:
        return f"""Gold macro driver signals:
- US Dollar Index (DXY): {s['dxy_latest']} | 20-day trend: {s['dxy_trend_20d']} | 5-day change: {s['dxy_5d_change']}
  (falling dollar = bullish gold; rising dollar = bearish gold)
- 10Y Real Yield (TIPS): {s['real_yield_10y']}% | 20-day trend: {s['real_yield_trend_20d']} | 5-day change: {s['real_yield_5d_change']}
  (falling real yields = bullish gold; rising = bearish — gold has zero yield)
- 10Y Breakeven Inflation: {s['breakeven_10y']}% | 20-day trend: {s['breakeven_trend_20d']}
  (rising inflation expectations = bullish gold as inflation hedge)
- Composite macro_bias: {s['macro_bias']} ({s['bullish_signals']} bullish / {s['bearish_signals']} bearish drivers)

Rules:
- Use ONLY the categorical signals (dxy_trend_20d, real_yield_trend_20d, breakeven_trend_20d, macro_bias).
- Gold is primarily driven by real yields and dollar strength — weight these heavily.
- Rising real yields are the single strongest headwind for gold.
- Cite signal names in rationale.
- {symbol} is a gold-tracking ETF, so gold macro fundamentals translate directly to its price."""
