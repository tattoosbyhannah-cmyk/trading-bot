#!/bin/bash
set -e
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
cd "$(dirname "$0")"

echo "=== Methodology: Multi-Agent ==="
cd methodology/multi-agent
wget -q --show-progress -U "$UA" -O tradingagents-xiao-2025.pdf "https://arxiv.org/pdf/2412.20138v7"
wget -q --show-progress -U "$UA" -O quantagent.pdf "https://arxiv.org/pdf/2509.09995"
wget -q --show-progress -U "$UA" -O tradinggroup-qwen3.pdf "https://arxiv.org/pdf/2508.17565"
wget -q --show-progress -U "$UA" -O orchestration-framework.pdf "https://arxiv.org/pdf/2512.02227"
wget -q --show-progress -U "$UA" -O finrl-contests.pdf "https://arxiv.org/pdf/2504.02281v3"
wget -q --show-progress -U "$UA" -O llm-agent-trading-survey.pdf "https://arxiv.org/pdf/2408.06361"
cd ../..

echo "=== Methodology: Backtesting ==="
cd methodology/backtesting
wget -q --show-progress -U "$UA" -O arakelian-backtesting-framework.pdf "https://papers.ssrn.com/sol3/Delivery.cfm/4893677.pdf?abstractid=4893677&mirid=1"
wget -q --show-progress -U "$UA" -O bailey-overfitting-slides.pdf "https://portfoliooptimizationbook.com/slides/slides-backtesting.pdf"
cd ../..

echo "=== Risk Mgmt: Position Sizing ==="
cd risk-mgmt/position-sizing
wget -q --show-progress -U "$UA" -O blotnick-position-sizing-2025.pdf "https://papers.ssrn.com/sol3/Delivery.cfm/5363482.pdf?abstractid=5363482&mirid=1"
wget -q --show-progress -U "$UA" -O sandberg-cta-position-sizing.pdf "http://www.diva-portal.org/smash/get/diva2:730028/fulltext01.pdf"
wget -q --show-progress -U "$UA" -O scholz-asset-price-position.pdf "https://www.hsba.de/fileadmin/user_upload/publikationen/Managing_position_size_depending_on_asset_price_characteristics_2014.pdf"
cd ../..

echo "=== Risk Mgmt: Tail Risk ==="
cd risk-mgmt/tail-risk
wget -q --show-progress -U "$UA" -O tail-safe-hedging-cbf.pdf "https://arxiv.org/pdf/2510.04555"
wget -q --show-progress -U "$UA" -O risk-aware-portfolio-opt.pdf "https://arxiv.org/pdf/2503.04662"
wget -q --show-progress -U "$UA" -O tail-risk-caviar.pdf "https://arxiv.org/pdf/2412.06193"
cd ../..

echo "=== Risk Mgmt: Commodity Risk ==="
cd risk-mgmt/commodity-risk
wget -q --show-progress -U "$UA" -O fan-zhang-commodity-premia.pdf "https://acfr.aut.ac.nz/__data/assets/pdf_file/0007/815659/Fan-update-RiskMGT_Fan-and-Zhang.pdf"
wget -q --show-progress -U "$UA" -O poitras-commodity-risk-mgmt.pdf "http://www.sfu.ca/~poitras/CRM_proof.pdf"
cd ../..

echo "=== Risk Mgmt: Operational ==="
cd risk-mgmt/operational
wget -q --show-progress -U "$UA" -O lenglet-systemic-failures.pdf "https://pmc.ncbi.nlm.nih.gov/articles/PMC8978471/pdf/" 2>/dev/null || echo "PMC may need manual download"
wget -q --show-progress -U "$UA" -O kpmg-regulatory-algo-trading.pdf "https://assets.kpmg.com/content/dam/kpmg/cn/pdf/en/2020/07/regulatory-expectations-for-algorithmic-trading.pdf"
cd ../..

echo ""
echo "=== Download Summary ==="
find . -name "*.pdf" -printf "%s %p\n" | numfmt --to=iec --field=1 | sort -k2
echo ""
echo "MANUAL DOWNLOADS NEEDED:"
echo "  - Poudel backtesting paper: https://www.researchgate.net/publication/399129762"
echo "  - Lopez de Prado lectures: https://www.quantresearch.org/Lectures.htm"
echo "  - Lopez de Prado book (purchase): Wiley ISBN 978-1119482086"
echo "  - Handbook Energy Risk (purchase): Springer ISBN 978-1-4614-9035-7"
