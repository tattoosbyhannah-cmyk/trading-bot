"""
Enhanced Sentiment Analyst — multi-source news with asset-group search.

Searches Alpaca News API using expanded symbol groups per asset class,
so geopolitical/macro news tagged on related tickers is captured even
when the target ETF itself has zero direct hits.

Includes debug diagnostics: per-query article counts, timestamps, and
top headlines are logged to agent_calls.jsonl for coverage auditing.
"""

import logging
from typing import TypedDict, Optional, Literal, List
from datetime import datetime, timedelta
from pathlib import Path
from pydantic import BaseModel, Field
import os
from dotenv import load_dotenv

from alpaca.data.historical import NewsClient
from alpaca.data.requests import NewsRequest

from fundamentals_analyst import asset_class
from agent_logger import log_agent_call

load_dotenv(Path(__file__).resolve().parent / '.env' if (Path(__file__).resolve().parent / '.env').exists() else None)


# ── Part 1: Asset Group Query Mapping ────────────────────────────────────────

# Context labels per asset class (for LLM prompt)
ASSET_CONTEXT = {
    "oil": "crude oil / petroleum / energy",
    "natgas": "natural gas / LNG / energy",
    "gold": "gold / precious metals / safe haven / Fed policy",
    "silver": "silver / precious metals / industrial metals",
}

# Legacy fallback — only used if registry lookup fails
_FALLBACK_SYMBOLS = {
    "oil": ["USO", "XLE", "CVX", "XOM"],
    "natgas": ["UNG", "BOIL", "KOLD"],
    "gold": ["GLD", "IAU", "GDX", "GDXJ"],
}


class EnhancedSentimentReport(BaseModel):
    symbol: str = Field(description="Ticker symbol analyzed")
    sentiment: Literal["bullish", "bearish", "neutral"] = Field(description="Overall news sentiment")
    confidence: int = Field(description="Confidence in sentiment reading 1-10", ge=1, le=10)
    news_volume: int = Field(description="Number of relevant articles found")
    key_themes: List[str] = Field(description="3-5 dominant news themes")
    headline_summary: str = Field(description="2-3 sentence summary of news impact")
    catalyst_alerts: List[str] = Field(description="Upcoming events or catalysts")
    economic_calendar: List[str] = Field(description="Relevant upcoming economic releases")
    diagnostics: dict = Field(
        default_factory=dict,
        description=(
            "Per-call fetch diagnostics: per-query article counts, total_raw, "
            "total_deduped, newest/oldest article timestamps, top_headlines. "
            "Populated by analyze_enhanced_sentiment from fetch_broader_news's "
            "diagnostics_dict; captured in agent_calls.jsonl via @log_agent_call."
        ),
    )


# ── Part 2: Multi-Query News Fetch ───────────────────────────────────────────

def fetch_broader_news(symbol: str, hours_back: int = 48,
                        as_of_date: Optional[str] = None) -> tuple:
    """Fetch news using expanded asset-group symbol queries.

    Returns (articles_list, diagnostics_dict).

    When as_of_date is provided (ISO 'YYYY-MM-DD'), the news window ends at
    end-of-day(as_of) instead of now() — backtest mode, so future news
    doesn't leak into a historical decision.
    """
    ac = asset_class(symbol)

    # Build symbol list from instrument registry
    try:
        from config.instrument_registry import registry
        reg_symbols = registry.get_sentiment_symbols(symbol)
    except Exception:
        reg_symbols = _FALLBACK_SYMBOLS.get(ac, [])

    all_symbols = [symbol.upper()]
    for s in reg_symbols:
        if s.upper() != symbol.upper() and s not in all_symbols:
            all_symbols.append(s)

    news_client = NewsClient(
        api_key=os.getenv("ALPACA_API_KEY_ID"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )

    if as_of_date:
        try:
            end_time = datetime.fromisoformat(as_of_date).replace(
                hour=23, minute=59, second=59
            )
        except Exception:
            end_time = datetime.now()
    else:
        end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours_back)

    # Alpaca allows comma-separated symbols (up to ~20).
    # Batch into chunks of 10 to stay safe.
    all_articles = []
    diagnostics = {
        "queries": [],
        "total_raw": 0,
        "total_deduped": 0,
        "newest": None,
        "oldest": None,
        "top_headlines": [],
    }

    for batch_start in range(0, len(all_symbols), 10):
        batch = all_symbols[batch_start:batch_start + 10]
        symbol_str = ",".join(batch)
        try:
            req = NewsRequest(
                symbols=symbol_str,
                start=start_time,
                end=end_time,
                sort="desc",
                limit=30,
                include_content=True,
            )
            resp = news_client.get_news(req)
            articles = resp.data.get("news", [])
            diagnostics["queries"].append({
                "symbols": symbol_str,
                "count": len(articles),
            })
            diagnostics["total_raw"] += len(articles)
            all_articles.extend(articles)
        except Exception as e:
            logging.warning(f"News fetch failed for batch {symbol_str}: {e}")
            diagnostics["queries"].append({
                "symbols": symbol_str,
                "count": 0,
                "error": str(e),
            })

    # Deduplicate by headline
    seen_headlines = set()
    unique_articles = []
    for article in all_articles:
        headline = article.headline.strip()
        if headline not in seen_headlines:
            seen_headlines.add(headline)
            unique_articles.append(article)

    # Sort by recency
    unique_articles.sort(key=lambda a: a.created_at, reverse=True)

    # Build diagnostics
    diagnostics["total_deduped"] = len(unique_articles)
    if unique_articles:
        diagnostics["newest"] = unique_articles[0].created_at.isoformat()
        diagnostics["oldest"] = unique_articles[-1].created_at.isoformat()
        diagnostics["top_headlines"] = [
            {
                "headline": a.headline[:120],
                "source": a.source,
                "time": a.created_at.isoformat(),
                "symbols": list(a.symbols or [])[:6],
            }
            for a in unique_articles[:10]
        ]

    # Convert to dicts for the LLM prompt
    news_list = []
    for article in unique_articles:
        news_list.append({
            "headline": article.headline,
            "summary": (article.summary or "")[:300],
            "source": article.source,
            "created_at": article.created_at.isoformat(),
            "symbols": list(article.symbols or []),
            "url": getattr(article, "url", ""),
        })

    return news_list, diagnostics


# ── LLM ──────────────────────────────────────────────────────────────────────

from config.llm_factory import create_llm
llm_deep = create_llm("sentiment_analyst", output_schema=EnhancedSentimentReport, max_tokens_override=4000)


# ── Part 3: Analyze with fixed confidence for no-news ────────────────────────

@log_agent_call(agent_name="sentiment_analyst", model_lane="fast")
def analyze_enhanced_sentiment(symbol: str, as_of_date: Optional[str] = None) -> EnhancedSentimentReport:
    """Comprehensive sentiment analysis using asset-group news search."""
    news_articles, diagnostics = fetch_broader_news(
        symbol, hours_back=48, as_of_date=as_of_date
    )

    ac = asset_class(symbol)
    context_label = ASSET_CONTEXT.get(ac, symbol)

    # Part 3 fix: no news = LOW confidence neutral, not high confidence
    if not news_articles:
        return EnhancedSentimentReport(
            symbol=symbol,
            sentiment="neutral",
            confidence=2,
            news_volume=0,
            key_themes=["No relevant news found in 48-hour window"],
            headline_summary=(
                f"Zero articles found for {symbol} and related {context_label} "
                f"tickers in the last 48 hours. Absence of news does NOT mean "
                f"absence of risk — treat this signal as uninformative, not neutral."
            ),
            catalyst_alerts=["No data — monitor manually for breaking developments"],
            economic_calendar=[],
            diagnostics=diagnostics,
        )

    # Fix 3: Compute confidence floor from article statistics
    n_articles = len(news_articles)
    base_confidence = min(10, max(1, n_articles // 3))  # 3 articles = 1, 30 = 10

    # Build news context for the LLM
    news_context = (
        f"Recent news for {symbol} ({context_label}), "
        f"last 48 hours — {n_articles} articles:\n\n"
    )
    for i, article in enumerate(news_articles[:15], 1):
        tagged = ", ".join(article["symbols"][:5])
        news_context += f"{i}. {article['headline']}\n"
        if article["summary"]:
            news_context += f"   {article['summary'][:200]}\n"
        news_context += f"   Source: {article['source']} | {article['created_at']} | Tags: [{tagged}]\n\n"

    prompt = f"""Analyze comprehensive market sentiment for {symbol} based on recent news.
{symbol} is a {context_label} ETF/asset. News below includes related tickers in the same asset group.

{news_context}

ARTICLE STATISTICS (computed by code — do not override):
- Articles found: {n_articles}
- Minimum confidence floor: {base_confidence}/10 (based on article volume)
- Your confidence rating MUST be >= {base_confidence}. You may adjust upward based on
  article content quality and directional clarity, but not below this floor.

Provide detailed sentiment analysis considering:

1. **Direct Impact**: How does this news affect {symbol} specifically?
2. **Market Themes**: Dominant narratives (supply/demand, geopolitics, policy, macro).
3. **Timing**: Which events are immediate catalysts vs background noise?
4. **Confidence**: Your confidence MUST be >= {base_confidence} (the floor from article volume).
   Adjust upward for clear directional signal or high-impact content.

For {context_label} assets, prioritize:
- Supply/demand fundamentals from news
- Geopolitical developments (sanctions, conflicts, diplomacy)
- Central bank policy impacts (rate decisions, QE)
- Economic growth / recession signals
- Sector-specific events (OPEC, EIA, Fed meetings, etc.)

Also identify any upcoming catalysts or economic releases mentioned in the articles.
Be specific about dates and expected impacts."""

    report = llm_deep.invoke(prompt)
    if not report.symbol:
        report.symbol = symbol
    report.news_volume = n_articles

    # Fix 3: Enforce confidence floor — LLM cannot rate below what article volume warrants
    if report.confidence < base_confidence:
        report.confidence = base_confidence

    # Attach fetch diagnostics so @log_agent_call captures them in agent_calls.jsonl
    report.diagnostics = diagnostics

    return report


# ── Part 4: Debug / Test Mode ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"

    print(f"=== ENHANCED SENTIMENT ANALYSIS for {symbol} ===")
    print(f"Asset class: {asset_class(symbol)}")
    ac = asset_class(symbol)
    group = ASSET_GROUP_QUERIES.get(ac, {})
    print(f"Search symbols: {group.get('symbols', [symbol])}\n")

    print("Fetching news (48h window)...")
    news_articles, diagnostics = fetch_broader_news(symbol, hours_back=48)

    # Part 4: Diagnostics
    print(f"\n{'─'*70}")
    print(f"📰 NEWS FETCH DIAGNOSTICS")
    print(f"{'─'*70}")
    for q in diagnostics["queries"]:
        err = f" ERROR: {q['error']}" if q.get("error") else ""
        print(f"  Query [{q['symbols']}]: {q['count']} articles{err}")
    print(f"  Total raw: {diagnostics['total_raw']}")
    print(f"  After dedup: {diagnostics['total_deduped']}")
    if diagnostics["newest"]:
        print(f"  Newest: {diagnostics['newest']}")
        print(f"  Oldest: {diagnostics['oldest']}")
    else:
        print(f"  (no articles)")

    if diagnostics["top_headlines"]:
        print(f"\n  Top {min(10, len(diagnostics['top_headlines']))} headlines:")
        for i, h in enumerate(diagnostics["top_headlines"][:10], 1):
            syms = ", ".join(h["symbols"][:4])
            print(f"    {i}. [{h['time'][:16]}] {h['headline'][:90]}")
            print(f"       src={h['source']}  tags=[{syms}]")

    print(f"\n{'─'*70}")
    print(f"Running LLM sentiment analysis...")
    print(f"{'─'*70}\n")

    report = analyze_enhanced_sentiment(symbol)

    print(f"NEWS VOLUME: {report.news_volume} articles analyzed")
    print(f"SENTIMENT: {report.sentiment.upper()} (Confidence: {report.confidence}/10)\n")

    print(f"Summary: {report.headline_summary}\n")

    print("Key Themes:")
    for i, theme in enumerate(report.key_themes, 1):
        print(f"  {i}. {theme}")

    print(f"\nCatalyst Alerts:")
    for i, alert in enumerate(report.catalyst_alerts, 1):
        print(f"  {i}. {alert}")

    if report.economic_calendar:
        print(f"\nEconomic Calendar:")
        for i, event in enumerate(report.economic_calendar, 1):
            print(f"  {i}. {event}")
