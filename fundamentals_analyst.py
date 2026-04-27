"""
Fundamentals Analyst — multi-asset fundamental analysis via routing.

Supports three asset classes:
  - Oil ETFs   (USO, UCO, SCO, …) → EIA crude inventory
  - Nat Gas ETFs (UNG, BOIL, KOLD, …) → EIA nat gas storage
  - Gold ETFs  (GLD, IAU, SGOL, …) → FRED macro (real yields, DXY, inflation)

Unsupported symbols get a neutral "no_analyzer" report so downstream agents
can handle them gracefully.

Architecture: asset_class(symbol) → fetch → compute_signals → generate_report
Each asset class implements its own fetch/compute/prompt trio.
"""

import os
import requests
from pathlib import Path
from typing import TypedDict, Optional, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, END
from agent_logger import log_agent_call

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)


# ── Symbol → Asset Class Routing (from instrument registry) ──────────────────

try:
    from config.instrument_registry import registry as _registry
    _REGISTRY_AVAILABLE = True
except Exception:
    _REGISTRY_AVAILABLE = False

# Legacy fallback sets (used if registry unavailable)
OIL_SYMBOLS = {"USO", "UCO", "SCO", "USL", "DBO", "BNO", "OIL"}
NATGAS_SYMBOLS = {"UNG", "BOIL", "KOLD", "GAZ", "UNL", "FCG"}
GOLD_SYMBOLS = {"GLD", "IAU", "SGOL", "AAAU", "OUNZ", "GLDM", "GDX", "GDXJ", "NEM", "GOLD"}
SUPPORTED_SYMBOLS = OIL_SYMBOLS | NATGAS_SYMBOLS | GOLD_SYMBOLS


def asset_class(symbol: str) -> str:
    """Route a symbol to its asset class, or 'unsupported'."""
    if _REGISTRY_AVAILABLE:
        ac = _registry.get_asset_class(symbol)
        if ac != "unsupported":
            return ac
    s = symbol.upper()
    if s in OIL_SYMBOLS:
        return "oil"
    if s in NATGAS_SYMBOLS:
        return "natgas"
    if s in GOLD_SYMBOLS:
        return "gold"
    return "unsupported"


# ── Shared Schema ─────────────────────────────────────────────────────────────

class FundamentalsReport(BaseModel):
    symbol: str = Field(description="Ticker symbol analyzed")
    bias: Literal["bullish", "bearish", "neutral"] = Field(description="Fundamentals-driven directional bias for the commodity")
    conviction: Literal["strong", "moderate", "weak"] = Field(description="Strength of the fundamental signal")
    key_driver: str = Field(description="The single most important fundamental factor in one sentence")
    rationale: str = Field(description="2-4 sentences citing specific fundamental signals by name")


class FundamentalsState(TypedDict):
    symbol: str
    calculation_run_id: Optional[str]
    raw_data: Optional[dict]
    signals: Optional[dict]
    fundamentals_report: Optional[FundamentalsReport]


# ── Oil: EIA Crude Inventory ──────────────────────────────────────────────────

def _fetch_oil(symbol: str) -> dict:
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
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    rows = r.json()["response"]["data"]
    rows = list(reversed(rows))
    inventory = [{"period": row["period"], "value": int(row["value"])} for row in rows]
    return {"inventory": inventory}


def _compute_oil_signals(raw: dict) -> dict:
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


def _oil_prompt(symbol: str, s: dict) -> str:
    return f"""You are a commodities fundamental analyst specializing in crude oil.
Analyze {symbol} using these pre-computed EIA crude inventory signals.

Crude oil physical supply signals (US commercial ex-SPR, weekly):
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
- Cite signal names (e.g., "week_surprise is build", "range_zone upper_quartile") in rationale.
- {symbol} is a crude-oil-tracking ETF, so crude fundamentals translate directly to its price.
"""


# ── Natural Gas: EIA Weekly Storage ───────────────────────────────────────────

def _fetch_natgas(symbol: str) -> dict:
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
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    rows = r.json()["response"]["data"]
    rows = list(reversed(rows))
    storage = [{"period": row["period"], "value": int(row["value"])} for row in rows]
    return {"storage": storage}


def _compute_natgas_signals(raw: dict) -> dict:
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


def _natgas_prompt(symbol: str, s: dict) -> str:
    return f"""You are a commodities fundamental analyst specializing in natural gas.
Analyze {symbol} using these pre-computed EIA natural gas storage signals.

Natural gas storage signals (Lower 48 working gas, weekly):
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
- Your bias must follow from the dominant signals, not individual numeric comparisons.
- Cite signal names in rationale (e.g., "week_surprise is injection", "range_zone lower_quartile").
- {symbol} is a natural-gas-tracking ETF, so nat gas fundamentals translate directly to its price.
"""


# ── Gold: FRED Macro Drivers ─────────────────────────────────────────────────

def _fred_latest(series_id: str, lookback: int = 60) -> list:
    """Fetch recent observations from FRED. Returns list of (date, value) tuples."""
    r = requests.get("https://api.stlouisfed.org/fred/series/observations", params={
        "series_id": series_id,
        "api_key": os.getenv("FRED_API_KEY"),
        "file_type": "json",
        "sort_order": "desc",
        "limit": lookback,
    }, timeout=10)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    result = []
    for o in obs:
        if o["value"] != ".":
            result.append({"date": o["date"], "value": float(o["value"])})
    return list(reversed(result))


def _fetch_gold(symbol: str) -> dict:
    return {
        "dxy": _fred_latest("DTWEXBGS", 60),
        "real_yield_10y": _fred_latest("DFII10", 60),
        "nominal_10y": _fred_latest("DGS10", 60),
        "breakeven_10y": _fred_latest("T10YIE", 60),
    }


def _compute_gold_signals(raw: dict) -> dict:
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
    nominal = raw.get("nominal_10y", [])
    breakeven = raw.get("breakeven_10y", [])

    if not all([dxy, real_yield, breakeven]):
        return {"error": "insufficient FRED data"}

    dxy_latest = _latest(dxy)
    real_yield_latest = _latest(real_yield)
    breakeven_latest = _latest(breakeven)

    dxy_trend = _trend(dxy)
    real_yield_trend = _trend(real_yield)
    breakeven_trend = _trend(breakeven)

    dxy_5d_chg = _change(dxy, 5)
    real_yield_5d_chg = _change(real_yield, 5)

    # Gold directional signals:
    # Falling DXY → bullish gold (inverse relationship)
    # Falling real yields → bullish gold (lower opportunity cost)
    # Rising inflation expectations → bullish gold (inflation hedge)
    bullish_count = 0
    bearish_count = 0

    if dxy_trend == "falling":
        bullish_count += 1
    elif dxy_trend == "rising":
        bearish_count += 1

    if real_yield_trend == "falling":
        bullish_count += 1
    elif real_yield_trend == "rising":
        bearish_count += 1

    if breakeven_trend == "rising":
        bullish_count += 1
    elif breakeven_trend == "falling":
        bearish_count += 1

    if bullish_count >= 2:
        macro_bias = "bullish"
    elif bearish_count >= 2:
        macro_bias = "bearish"
    else:
        macro_bias = "mixed"

    return {
        "asset_class": "gold",
        "dxy_latest": round(dxy_latest, 2) if dxy_latest else None,
        "dxy_trend_20d": dxy_trend,
        "dxy_5d_change": dxy_5d_chg,
        "real_yield_10y": round(real_yield_latest, 2) if real_yield_latest else None,
        "real_yield_trend_20d": real_yield_trend,
        "real_yield_5d_change": real_yield_5d_chg,
        "breakeven_10y": round(breakeven_latest, 2) if breakeven_latest else None,
        "breakeven_trend_20d": breakeven_trend,
        "macro_bias": macro_bias,
        "bullish_signals": bullish_count,
        "bearish_signals": bearish_count,
    }


def _gold_prompt(symbol: str, s: dict) -> str:
    return f"""You are a macro-fundamental analyst specializing in gold and precious metals.
Analyze {symbol} using these pre-computed macro signals from FRED.

Gold macro driver signals:
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
- Your bias must follow from the dominant signals, not individual numeric comparisons.
- Cite signal names in rationale (e.g., "real_yield_trend_20d is falling", "macro_bias is bullish").
- {symbol} is a gold-tracking ETF, so gold macro fundamentals translate directly to its price.
"""


# ── Router: Fetch / Compute / Report ─────────────────────────────────────────

FETCHERS = {
    "oil": _fetch_oil,
    "natgas": _fetch_natgas,
    "gold": _fetch_gold,
}

SIGNAL_COMPUTERS = {
    "oil": _compute_oil_signals,
    "natgas": _compute_natgas_signals,
    "gold": _compute_gold_signals,
}

PROMPT_BUILDERS = {
    "oil": _oil_prompt,
    "natgas": _natgas_prompt,
    "gold": _gold_prompt,
}


def fetch_data(state: FundamentalsState) -> FundamentalsState:
    """Fetch raw data via data source factory (registry-driven)."""
    symbol = state["symbol"]

    # Try registry-based source lookup first
    try:
        from config.instrument_registry import registry
        source_name = registry.get_fundamentals_source(symbol)
    except Exception:
        # Fallback to asset_class-based lookup
        ac = asset_class(symbol)
        source_name = {"oil": "eia_crude", "natgas": "eia_natgas",
                       "gold": "fred_gold"}.get(ac, "unsupported")

    if source_name == "unsupported":
        return {"raw_data": None}

    try:
        from data_sources.source_factory import get_source
        source = get_source(source_name)
        snapshot = source.fetch(symbol)
        return {"raw_data": {
            "_snapshot": snapshot,
            "_source_name": source_name,
        }}
    except Exception as e:
        # Fallback to legacy inline fetchers
        ac = asset_class(symbol)
        if ac in FETCHERS:
            try:
                raw = FETCHERS[ac](symbol)
                return {"raw_data": raw}
            except Exception as e2:
                return {"raw_data": {"error": str(e2)}}
        return {"raw_data": {"error": str(e)}}


def compute_signals(state: FundamentalsState) -> FundamentalsState:
    """Compute signals — uses snapshot from data source factory if available."""
    symbol = state["symbol"]
    ac = asset_class(symbol)

    if ac == "unsupported":
        return {
            "signals": {
                "no_analyzer": True,
                "reason": (
                    f"{symbol} is not a supported symbol. "
                    f"Add a data source and instrument registry entry."
                ),
            }
        }

    raw = state.get("raw_data")
    if not raw:
        return {"signals": {"error": "no data fetched"}}

    # If we used the factory, the snapshot is already computed
    snapshot = raw.get("_snapshot") if isinstance(raw, dict) else None
    if snapshot:
        if snapshot.error:
            return {"signals": {"error": snapshot.error}}
        signals = snapshot.signals
        signals["_llm_summary"] = snapshot.llm_summary
        return {"signals": signals}

    # Legacy fallback
    if raw.get("error"):
        return {"signals": {"error": raw["error"]}}
    if ac in SIGNAL_COMPUTERS:
        return {"signals": SIGNAL_COMPUTERS[ac](raw)}
    return {"signals": {"error": f"no signal computer for {ac}"}}


from config.llm_factory import create_llm
llm_deep = create_llm("fundamentals_analyst", output_schema=FundamentalsReport, temperature_override=0.3, max_tokens_override=4000)


@log_agent_call(agent_name="fundamentals_analyst", model_lane="fast")
def generate_report(state: FundamentalsState) -> FundamentalsState:
    """Generate a structured fundamentals report via the appropriate prompt."""
    s = state["signals"]

    if s.get("no_analyzer"):
        report = FundamentalsReport(
            symbol=state["symbol"],
            bias="neutral",
            conviction="weak",
            key_driver=f"no_analyzer: {state['symbol']} asset class not yet supported",
            rationale=(
                f"No fundamental analyzer is implemented for {state['symbol']}. "
                f"{s['reason']} "
                f"This report carries no informational value — downstream agents "
                f"should treat the fundamentals signal as absent, not neutral-by-analysis."
            ),
        )
        return {"fundamentals_report": report}

    if s.get("error"):
        report = FundamentalsReport(
            symbol=state["symbol"],
            bias="neutral",
            conviction="weak",
            key_driver=f"data_error: {s['error']}",
            rationale=(
                f"Fundamentals data fetch/compute failed: {s['error']}. "
                f"Signal is unreliable; treat as absent."
            ),
        )
        return {"fundamentals_report": report}

    ac = asset_class(state["symbol"])

    # Use pre-built LLM summary from data source adapter if available
    llm_summary = s.pop("_llm_summary", None)
    if llm_summary:
        prompt = f"""You are a commodities fundamental analyst.
Analyze {state['symbol']} using these pre-computed signals.

{llm_summary}"""
    elif ac in PROMPT_BUILDERS:
        prompt = PROMPT_BUILDERS[ac](state["symbol"], s)
    else:
        prompt = f"Analyze fundamentals for {state['symbol']}: {s}"

    report: FundamentalsReport = llm_deep.invoke(prompt)
    if not report.symbol:
        report.symbol = state["symbol"]
    return {"fundamentals_report": report}


# ── LangGraph Wiring ─────────────────────────────────────────────────────────

workflow = StateGraph(FundamentalsState)
workflow.add_node("fetch_data", fetch_data)
workflow.add_node("compute_signals", compute_signals)
workflow.add_node("generate_report", generate_report)
workflow.set_entry_point("fetch_data")
workflow.add_edge("fetch_data", "compute_signals")
workflow.add_edge("compute_signals", "generate_report")
workflow.add_edge("generate_report", END)
graph = workflow.compile()


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"
    print(f"\n=== Running Fundamentals Analyst for {symbol} ===")
    print(f"Asset class: {asset_class(symbol)}\n")
    result = graph.invoke({"symbol": symbol})

    print("Signals:")
    for k, v in result["signals"].items():
        print(f"  {k}: {v}")

    report = result["fundamentals_report"]
    print(f"\n--- Structured Report ---")
    print(f"symbol:     {report.symbol}")
    print(f"bias:       {report.bias}")
    print(f"conviction: {report.conviction}")
    print(f"key_driver: {report.key_driver}")
    print(f"rationale:  {report.rationale}")
