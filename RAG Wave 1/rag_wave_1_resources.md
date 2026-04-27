# RAG Wave 1 — Resource Plan & Download Guide

**Date:** April 14, 2026
**Project:** Trading Bot — Phase 3 Paper Trading
**Goal:** Fill the oil market microstructure gap in the RAG knowledge base using freely available authoritative sources, plus Scribd-accessible books, combined with materials already on hand.

---

## TL;DR — Priority Download Order

1. **Scribd: Oil Trading Manual (Elsevier)** — replaces Kleinman, comprehensive
2. **Scribd: 40 Classic Crude Oil Trades (Owain Johnson, 2022)** — real-world trading examples
3. **EIA "What Drives Crude Oil Prices" series** — 9 pages, free, authoritative
4. **CME Roll Yield PDF** — best free source on USO/futures decay
5. **CAPP Crude Oil Market Fundamentals PDF** — supply/demand summary
6. **EIA WPSR methodology PDFs** — how to read inventory data
7. **USO Prospectus** — actual roll mechanics

---

## Part 1: Books You Already Have

### Load into RAG (Tier 1)

**Andreas Clenow — Following the Trend (epub)**
- File: `dokumen.pub_following-the-trend-diversified-managed-futures-trading-wiley-trading-*.epub`
- Why: Commodity trend-following methodology, position sizing, distinguishing trends from mean reversion
- Tags: `trend-following`, `position-sizing`, `commodity`, `backtest`

**Hilary Till — Intelligent Commodity Investing (SSRN-2612248)**
- File: `ssrn-2612248.pdf`
- Why: Historical commodity drivers, pitfalls, risk factors
- Tags: `commodity`, `risk`, `allocation`, `historical`

**Hilary Till — Commodity Investing & Risk Management (SSRN-5267612)**
- File: `ssrn-5267612.pdf`
- Why: Risk management frameworks, portfolio construction
- Tags: `commodity`, `risk-management`, `portfolio`

**Usiere Uko — Oil Trading 101**
- File: `940321744-Oil-Trading-101-Understanding-Usiere-Uko.pdf`
- Why: Basic oil vocabulary, CFDs/futures/options intro
- Tags: `oil`, `futures`, `options`, `introduction`

### Load Later (Tier 2 — when options-chain support is built)

**Crude Oil Options Handbook (INE)**
- File: `Handbook.pdf`
- Why: Options contract specs, exercise mechanics
- Tags: `options`, `crude-oil`, `hedging`, `contract-specs`

### Skip

- Rayner Teo guides (x2) — too beginner
- Watkins sample — incomplete chapter only

---

## Part 2: Scribd Downloads (Use Your Subscription)

### Priority A — Get These First

**Oil Trading Manual (Elsevier Science, ~1000 pages)**
URL: `https://www.scribd.com/book/282657255/Oil-Trading-Manual-A-Comprehensive-Guide-to-the-Oil-Markets`

This is the single best replacement for Kleinman — actually broader and deeper. Three sections:
1. Characteristics — oil as commodity, refinery processes, pricing
2. Instruments and markets — physical, forward, futures, options, swaps
3. Administration — operations, logistics, accounting, tax, contracts, regulation, risk

Compiled from internationally-respected practitioner contributions. Normally retails at ~$1,500. Updated 2003 but core content (futures mechanics, oil market structure) is still authoritative.

Tags: `oil-trading`, `physical-markets`, `derivatives`, `risk-management`, `pricing`

**40 Classic Crude Oil Trades — Owain Johnson (Routledge, 2022)**
URL: `https://www.scribd.com/document/807062311/40-Classic-Crude-Oil-Trades-Routledge-2022`

Real-world trading case studies from a Routledge series. Covers arbitrages (regional, quality, time), spread trading, and innovative crude oil strategies. Recent (2022) so it reflects current market structure.

Tags: `oil-trading`, `arbitrage`, `spreads`, `case-studies`, `recent`

### Priority B — Worth Downloading If Easily Available

**World of Oil Derivatives — Greg Newman**
URL: `https://www.scribd.com/document/713680496/World-of-Oil-Derivatives-a-Guide-to-Financial-Oil-Trading-in-a-Modern`

Modern (post-2008 financial crisis era) treatment of paper oil markets, OTC trading, regulatory environment, financial vs physical integration.

Tags: `oil-derivatives`, `OTC`, `regulation`, `modern-markets`

---

## Part 3: Free Government & Exchange Sources

### EIA — "What Drives Crude Oil Prices" Series

The EIA's comprehensive 7-factor framework for oil price drivers. This is essentially a free oil-economics textbook. Each page is dense, well-structured, and chunks cleanly.

| # | Topic | URL |
|---|-------|-----|
| 1 | Overview (master page) | `https://www.eia.gov/finance/markets/crudeoil/` |
| 2 | Supply — OPEC | `https://www.eia.gov/finance/markets/crudeoil/supply-opec.php` |
| 3 | Supply — Non-OPEC | `https://www.eia.gov/finance/markets/crudeoil/supply-nonopec.php` |
| 4 | Demand — OECD | `https://www.eia.gov/finance/markets/crudeoil/demand-oecd.php` |
| 5 | Demand — Non-OECD | `https://www.eia.gov/finance/markets/crudeoil/demand-nonoecd.php` |
| 6 | Balance — Inventories ⭐ | `https://www.eia.gov/finance/markets/crudeoil/balance.php` |
| 7 | Financial Markets | `https://www.eia.gov/finance/markets/crudeoil/financial_markets.php` |
| 8 | Spot Prices | `https://www.eia.gov/finance/markets/crudeoil/spot_prices.php` |
| 9 | Full PDF Presentation | `https://www.eia.gov/finance/markets/reports_presentations/eia_what_drives_crude_oil_prices.pdf` |

⭐ The Balance/Inventories page is the single most important — it explains how to interpret EIA inventory builds/draws relative to seasonal norms, which is exactly what your fundamentals analyst needs.

### EIA — Weekly Petroleum Status Report (WPSR) Methodology

Critical for understanding what the weekly data actually measures and how to interpret it.

| Source | URL |
|--------|-----|
| Sources & Methods | `https://www.eia.gov/petroleum/supply/weekly/pdf/sources.pdf` |
| Appendix B (Detailed Statistics Notes) | `https://www.eia.gov/petroleum/supply/weekly/pdf/appendixb.pdf` |
| Current WPSR Summary | `https://ir.eia.gov/wpsr/wpsrsummary.pdf` |
| Schedule | `https://www.eia.gov/petroleum/supply/weekly/schedule.php` |
| Petroleum Data Hub | `https://www.eia.gov/petroleum/data.php` |

### EIA — Forecasts & Outlooks

| Source | URL |
|--------|-----|
| Short-Term Energy Outlook (Global Oil) | `https://www.eia.gov/outlooks/steo/report/global_oil.php` |

### CME Group — Futures Mechanics

| Source | URL |
|--------|-----|
| Contango & Backwardation (basics) | `https://www.cmegroup.com/education/courses/introduction-to-ferrous-metals/what-is-contango-and-backwardation` |
| Fundamental Analysis — Supply & Demand | `https://www.cmegroup.com/education/courses/using-fundamental-analysis-when-evaluating-trades/fundamental-analysis-futures-supply-and-demand` |
| Trading Energy Calendar Spread Options | `https://www.cmegroup.com/articles/whitepapers/trading-energy-calendar-spread-options.html` |
| Deconstructing Futures Returns: Roll Yield ⭐ | `https://www.cmegroup.com/education/files/deconstructing-futures-returns-the-role-of-roll-yield.pdf` |
| Crude Oil Overview | `https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html` |

⭐ The Roll Yield PDF is the best free source on why ETFs like USO underperform spot oil during contango. Essential for your USO-specific analysis.

### USO ETF — Regulatory Filings

| Source | URL |
|--------|-----|
| USO Prospectus Amendment (2025) | `https://www.sec.gov/Archives/edgar/data/0001327068/000207187625000081/i25397_uso-424b3.htm` |
| USO Fund Website | `https://www.uscfinvestments.com/uso` |
| USCF Roll Methodology (2017 SEC filing) | `https://www.sec.gov/Archives/edgar/data/0001671686/000117120017000336/filename1.htm` |

The 2025 prospectus amendment is critical — it documents the change from 10-day to 5-day roll periods starting January 2026, which directly affects USO's contango drag profile.

### CAPP — Industry Summary

| Source | URL |
|--------|-----|
| Crude Oil Market Fundamentals (PDF) | `https://www.capp.ca/wp-content/uploads/2024/03/Crude-Oil-Market-Fundamentals.pdf` |

Single-PDF overview with charts covering supply/demand balance, OPEC vs non-OPEC growth, US production trends, and inventory analysis. Great consolidated reference.

### IEA — Monthly Oil Market Reports

These are typically subscription-based, but the public summaries are free and dense with current data.

| Source | URL |
|--------|-----|
| Latest Oil Market Report (Aug 2025) | `https://www.iea.org/reports/oil-market-report-august-2025` |
| Oil Market Report (July 2025) | `https://www.iea.org/reports/oil-market-report-july-2025` |
| OMR landing page | `https://www.iea.org/reports/oil-market-report` |

---

## Part 4: Download Commands (Run in Terminal B)

### Setup

```bash
cd ~/trading-bot/trading-rag-knowledge/
mkdir -p raw/eia raw/cme raw/uso raw/iea raw/other raw/scribd
```

### EIA — "What Drives Crude Oil Prices" series

```bash
cd ~/trading-bot/trading-rag-knowledge/raw/eia/

wget -q "https://www.eia.gov/finance/markets/crudeoil/" -O wdcop_overview.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/supply-opec.php" -O wdcop_supply_opec.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/supply-nonopec.php" -O wdcop_supply_nonopec.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/demand-oecd.php" -O wdcop_demand_oecd.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/demand-nonoecd.php" -O wdcop_demand_nonoecd.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/balance.php" -O wdcop_balance.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/financial_markets.php" -O wdcop_financial.html
wget -q "https://www.eia.gov/finance/markets/crudeoil/spot_prices.php" -O wdcop_spot.html

wget -q "https://www.eia.gov/finance/markets/reports_presentations/eia_what_drives_crude_oil_prices.pdf" -O wdcop_full.pdf

# WPSR methodology
wget -q "https://www.eia.gov/petroleum/supply/weekly/pdf/sources.pdf" -O wpsr_sources.pdf
wget -q "https://www.eia.gov/petroleum/supply/weekly/pdf/appendixb.pdf" -O wpsr_appendixb.pdf
wget -q "https://ir.eia.gov/wpsr/wpsrsummary.pdf" -O wpsr_current_summary.pdf

# STEO
wget -q "https://www.eia.gov/outlooks/steo/report/global_oil.php" -O steo_global_oil.html
```

### CME Group

```bash
cd ~/trading-bot/trading-rag-knowledge/raw/cme/

wget -q "https://www.cmegroup.com/education/courses/introduction-to-ferrous-metals/what-is-contango-and-backwardation" -O contango_backwardation.html
wget -q "https://www.cmegroup.com/education/courses/using-fundamental-analysis-when-evaluating-trades/fundamental-analysis-futures-supply-and-demand" -O fundamental_analysis.html
wget -q "https://www.cmegroup.com/articles/whitepapers/trading-energy-calendar-spread-options.html" -O calendar_spreads.html
wget -q "https://www.cmegroup.com/education/files/deconstructing-futures-returns-the-role-of-roll-yield.pdf" -O roll_yield.pdf
wget -q "https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.html" -O crude_oil_overview.html
```

### USO

```bash
cd ~/trading-bot/trading-rag-knowledge/raw/uso/

wget -q "https://www.sec.gov/Archives/edgar/data/0001327068/000207187625000081/i25397_uso-424b3.htm" -O uso_prospectus_2025.html
wget -q "https://www.sec.gov/Archives/edgar/data/0001671686/000117120017000336/filename1.htm" -O uscf_roll_methodology.html
```

### Other (CAPP)

```bash
cd ~/trading-bot/trading-rag-knowledge/raw/other/

wget -q "https://www.capp.ca/wp-content/uploads/2024/03/Crude-Oil-Market-Fundamentals.pdf" -O capp_fundamentals.pdf
```

### IEA (free public summaries)

```bash
cd ~/trading-bot/trading-rag-knowledge/raw/iea/

wget -q "https://www.iea.org/reports/oil-market-report-august-2025" -O omr_aug_2025.html
wget -q "https://www.iea.org/reports/oil-market-report-july-2025" -O omr_jul_2025.html
```

### Scribd (manual download via browser)

Sign in to Scribd, then:
1. Navigate to Oil Trading Manual: `https://www.scribd.com/book/282657255/Oil-Trading-Manual-A-Comprehensive-Guide-to-the-Oil-Markets`
2. Download as PDF (or save offline) → save to `~/trading-bot/trading-rag-knowledge/raw/scribd/oil_trading_manual.pdf`
3. Navigate to 40 Classic Crude Oil Trades: `https://www.scribd.com/document/807062311/40-Classic-Crude-Oil-Trades-Routledge-2022`
4. Download → save to `~/trading-bot/trading-rag-knowledge/raw/scribd/40_classic_oil_trades.pdf`

---

## Part 5: Conversion to Text

### HTML → Text

```bash
cd ~/trading-bot/trading-rag-knowledge/

for dir in raw/eia raw/cme raw/uso raw/iea; do
    for f in "$dir"/*.html; do
        [ -f "$f" ] || continue
        python3 -c "
from bs4 import BeautifulSoup
with open('$f') as fh:
    soup = BeautifulSoup(fh, 'html.parser')
    for s in soup(['script','style','nav','footer','header','aside']):
        s.decompose()
    print(soup.get_text(separator='\n', strip=True))
" > "${f%.html}.txt"
    done
done
```

### PDF → Text

```bash
cd ~/trading-bot/trading-rag-knowledge/

for dir in raw/eia raw/cme raw/uso raw/iea raw/other raw/scribd; do
    for f in "$dir"/*.pdf; do
        [ -f "$f" ] || continue
        pdftotext "$f" "${f%.pdf}.txt"
    done
done
```

If you don't have `pdftotext`:

```bash
sudo apt install poppler-utils
```

### Epub → Text (Clenow)

```bash
# Option 1: pandoc
pandoc ~/trading-bot/books/dokumen.pub_following-the-trend-*.epub -t plain -o ~/trading-bot/trading-rag-knowledge/raw/other/clenow_following_trend.txt

# Option 2: calibre (if pandoc fails)
ebook-convert ~/trading-bot/books/dokumen.pub_following-the-trend-*.epub ~/trading-bot/trading-rag-knowledge/raw/other/clenow_following_trend.txt
```

---

## Part 6: Chunking & Loading into ChromaDB

Use your existing chunking pipeline with these recommended settings:

- **Chunk size:** 500 tokens
- **Overlap:** 50 tokens
- **Embedding model:** nomic-embed-text (already configured)

### Metadata Tagging Schema

Each chunk should be tagged with:

```python
metadata = {
    "source": "eia-balance",          # short identifier
    "source_full": "EIA — What Drives Crude Oil Prices: Balance",  # human-readable
    "category": "oil-fundamentals",   # broad bucket
    "url": "https://www.eia.gov/finance/markets/crudeoil/balance.php",
    "topic_tags": ["inventory", "supply-demand", "seasonal", "EIA"],
    "authority": "government",        # government | exchange | book | paper
    "year": 2024,                     # publication year
}
```

### Suggested Source IDs

| Source File | source ID | category |
|-------------|-----------|----------|
| EIA WDCOP Overview | `eia-overview` | oil-fundamentals |
| EIA WDCOP Supply OPEC | `eia-supply-opec` | oil-fundamentals |
| EIA WDCOP Supply Non-OPEC | `eia-supply-nonopec` | oil-fundamentals |
| EIA WDCOP Demand OECD | `eia-demand-oecd` | oil-fundamentals |
| EIA WDCOP Demand Non-OECD | `eia-demand-nonoecd` | oil-fundamentals |
| EIA WDCOP Balance | `eia-balance` | oil-fundamentals |
| EIA WDCOP Financial | `eia-financial` | oil-fundamentals |
| EIA WDCOP Spot Prices | `eia-spot` | oil-fundamentals |
| EIA WDCOP Full PDF | `eia-wdcop-full` | oil-fundamentals |
| EIA WPSR Sources | `eia-wpsr-sources` | inventory-methodology |
| EIA WPSR Appendix B | `eia-wpsr-appendixb` | inventory-methodology |
| EIA STEO Global Oil | `eia-steo` | oil-forecast |
| CME Contango/Backwardation | `cme-contango` | futures-mechanics |
| CME Fundamental Analysis | `cme-fundamental` | futures-mechanics |
| CME Calendar Spreads | `cme-calendar-spreads` | futures-mechanics |
| CME Roll Yield PDF | `cme-roll-yield` | futures-mechanics |
| USO Prospectus | `uso-prospectus-2025` | uso-mechanics |
| USCF Roll Methodology | `uscf-roll-method` | uso-mechanics |
| CAPP Fundamentals | `capp-fundamentals` | oil-fundamentals |
| IEA OMR Aug 2025 | `iea-omr-aug-2025` | oil-current |
| Oil Trading Manual | `otm-elsevier` | oil-trading-reference |
| 40 Classic Crude Oil Trades | `johnson-40-trades` | oil-case-studies |
| Clenow Following the Trend | `clenow` | trend-following |
| Till SSRN 2008 | `till-2008` | commodity-investing |
| Till SSRN 2010 | `till-2010` | risk-management |
| Uko Oil Trading 101 | `uko-101` | oil-introduction |
| INE Options Handbook | `ine-options` | options-mechanics |

---

## Part 7: Validation Plan

After loading, verify the RAG quality has improved:

```bash
cd ~/trading-bot
source venv/bin/activate

# Run bull and bear researchers standalone, 3x each
for i in 1 2 3; do
    echo "=== Bull Run $i ==="
    python3 rag_bull_researcher.py USO 2>&1 | tee /tmp/bull_run_$i.log
    echo "=== Bear Run $i ==="
    python3 rag_bear_researcher.py USO 2>&1 | tee /tmp/bear_run_$i.log
done

# Check what sources got cited
grep -E "source|page|chapter|EIA|CME|Clenow|OTM" /tmp/bull_run_*.log /tmp/bear_run_*.log
```

### Success Criteria

1. **Citation specificity**: Researchers should reference specific sources (e.g., "per EIA WDCOP Balance section, inventory builds above 5-year average...") rather than vague claims.
2. **Reduced flip frequency**: Across 3 runs, the literature winner should flip less than once per 3 runs (currently ~1/3).
3. **Conviction calibration**: Conviction scores should show more variance across runs that reflect actual evidence quality, not just GEMM noise.

---

## Part 8: Coverage Map — Closing the Kleinman Gap

| Knowledge Area | Now Covered By |
|---------------|----------------|
| EIA inventory interpretation | EIA Balance + WPSR Sources/Appendix B |
| 5-year average comparisons | EIA Balance |
| OPEC supply dynamics & spare capacity | EIA Supply-OPEC + IEA OMR + CAPP |
| Non-OPEC (US shale) production | EIA Supply Non-OPEC + CAPP |
| OECD demand patterns | EIA Demand OECD |
| Non-OECD (China/India) demand | EIA Demand Non-OECD |
| Contango / backwardation | CME Contango + CME Roll Yield + Wikipedia |
| USO ETF roll mechanics | USO Prospectus + CME Roll Yield |
| WTI vs Brent differentials | EIA Spot Prices + CME |
| Geopolitical risk premium | EIA Spot Prices + IEA OMR |
| Futures market structure | CME suite + EIA Financial Markets |
| Calendar spread trading | CME Calendar Spreads + 40 Classic Trades |
| Physical oil markets | Oil Trading Manual (Scribd) |
| Refinery processes | Oil Trading Manual (Scribd) |
| Real-world arbitrage examples | 40 Classic Crude Oil Trades (Scribd) |
| Trend vs mean reversion | Clenow Following the Trend |
| Position sizing under volatility | Clenow + Till papers |
| Commodity portfolio risk | Till SSRN papers |
| Oil trading basics | Uko Oil Trading 101 |
| Options mechanics (later) | INE Handbook |

This combination provides ~95% of what Kleinman's "Trading Oil" would have covered, with the added benefit of authoritative primary sources and recent (2022+) practitioner case studies.

---

## Estimated Time Investment

| Task | Time |
|------|------|
| Download all free sources | 15 min |
| Download Scribd books | 10 min |
| Convert HTML/PDF to text | 5 min |
| Chunk + load into ChromaDB | 1-2 hours |
| Validation runs (3 bull + 3 bear) | 30 min |
| **Total** | **~3 hours** |

Originally estimated as 4 hours in the priority list. The shorter time is because most sources are now web-scrapable rather than requiring book scanning.

---

## Notes

- **EIA content is government-published** — no copyright issues for RAG ingestion.
- **CME content is publicly accessible educational material** — fine for RAG use.
- **Scribd content** — for personal/internal use within your project. Do not redistribute.
- **Update cadence**: EIA STEO, IEA OMR, and WPSR summary should be re-fetched monthly to keep the RAG corpus current with market conditions.
