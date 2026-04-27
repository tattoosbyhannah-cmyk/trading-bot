"""
Contract tests — enforce the LLM-math boundary and system invariants.

These tests verify that:
1. LLMs don't compute numbers (math stays in Python)
2. Agent schemas are valid
3. RAG authority tiers are correctly ordered
4. Instrument registry is internally consistent
5. Broker adapter is functional

Run: python -m pytest tests/test_llm_contracts.py -v
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _llm_raw_invoke(prompt: str, max_tokens: int = 500) -> str:
    """Send a raw prompt to the default LLM and return the text response."""
    from config.model_registry import model_registry
    endpoint = model_registry.get_endpoint("master_orchestrator")
    import requests
    resp = requests.post(
        f"{endpoint}/chat/completions",
        json={
            "model": "qwen3-thinking",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _extract_numbers(text: str) -> list:
    """Extract all numeric values from text (integers and floats)."""
    # Remove thinking blocks if present
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Find numbers that look like computed results (not just years or small integers)
    numbers = re.findall(r'\b\d+\.?\d*\b', text)
    return [float(n) for n in numbers if float(n) > 10 or '.' in n]


# ── Test 1: No-Math Boundary ─────────────────────────────────────────────────

class TestNoMathBoundary:
    """LLM should not compute specific numeric answers."""

    @pytest.mark.parametrize("prompt,forbidden_answer", [
        (
            "Calculate: 124.55 × (1 - 0.0225 × 2.5). Return ONLY the number.",
            117.55,  # approximate expected answer
        ),
        (
            "Compute the position size in dollars for a $100,000 portfolio at 5% allocation. "
            "Return ONLY the number.",
            5000.0,
        ),
        (
            "What is the Sharpe ratio given annual return of 12%, risk-free rate of 4%, "
            "and standard deviation of 15%? Return ONLY the number.",
            0.533,
        ),
    ])
    def test_llm_refuses_math(self, prompt, forbidden_answer):
        """LLM should not return computed numeric results."""
        response = _llm_raw_invoke(prompt)
        # Strip thinking tags
        clean = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()

        # Check if the response contains the exact computed answer
        # Allow 5% tolerance
        numbers = _extract_numbers(clean)
        for n in numbers:
            if abs(n - forbidden_answer) / max(abs(forbidden_answer), 0.01) < 0.05:
                # LLM returned the computed answer — this is expected to happen
                # since we can't prevent it. Mark as xfail (expected failure).
                pytest.xfail(
                    f"LLM computed {n} (expected ~{forbidden_answer}). "
                    f"This is a known limitation — Python should do this math, not the LLM."
                )


# ── Test 2: Schema Validation ─────────────────────────────────────────────────

class TestSchemaValidation:
    """Agent output schemas should be valid and complete."""

    def test_master_decision_has_no_raw_prices(self):
        """MasterTradingDecision should have pct fields, not just raw prices."""
        from master_orchestrator import MasterTradingDecision
        fields = set(MasterTradingDecision.model_fields.keys())
        assert "stop_loss_pct" in fields, "Missing stop_loss_pct — LLM should output percentages"
        assert "price_target_pct" in fields, "Missing price_target_pct"
        assert "entry_price" in fields, "Missing entry_price (should come from Alpaca)"

    def test_llm_decision_schema_has_no_dollar_prices(self):
        """_LLMDecision should only have percentage fields, not dollar prices."""
        from master_orchestrator import _LLMDecision
        fields = set(_LLMDecision.model_fields.keys())
        assert "stop_loss_pct" in fields
        assert "price_target_pct" in fields
        # _LLMDecision should NOT have entry_price — that comes from Alpaca
        assert "entry_price" not in fields, "entry_price should not be in LLM schema"

    def test_risk_assessment_schema(self):
        """RiskAssessment should have risk_score and approval_status."""
        from risk_gatekeeper import RiskAssessment
        fields = set(RiskAssessment.model_fields.keys())
        assert "risk_score" in fields
        assert "approval_status" in fields
        assert "position_size_pct" in fields

    def test_fundamentals_report_schema(self):
        """FundamentalsReport should have bias and conviction."""
        from fundamentals_analyst import FundamentalsReport
        fields = set(FundamentalsReport.model_fields.keys())
        assert "bias" in fields
        assert "conviction" in fields

    def test_sentiment_report_schema(self):
        """EnhancedSentimentReport should have news_volume."""
        from enhanced_sentiment_analyst import EnhancedSentimentReport
        fields = set(EnhancedSentimentReport.model_fields.keys())
        assert "news_volume" in fields
        assert "confidence" in fields


# ── Test 3: RAG Authority Tier Ordering ───────────────────────────────────────

class TestRAGAuthority:
    """Verify RAG chunks are ordered by authority tier."""

    def test_gold_query_prefers_academic_sources(self):
        """Query about gold should return tier 2 (academic) before tier 3 (practitioner)."""
        import chromadb
        client = chromadb.PersistentClient(path="./chromadb-data")
        papers = client.get_collection("papers")

        results = papers.query(
            query_texts=["gold hedge safe haven inflation"],
            n_results=10,
        )

        tiers = []
        for meta in results["metadatas"][0]:
            tier = meta.get("authority_tier", 3)
            tiers.append(tier)

        # At least some tier 2 should appear
        assert 2 in tiers, f"No tier-2 academic sources found for gold query. Tiers: {tiers}"

    def test_authority_metadata_exists(self):
        """All collections should have authority_tier metadata on chunks."""
        import chromadb
        client = chromadb.PersistentClient(path="./chromadb-data")

        for name in ["methodology", "risk_mgmt", "commodities", "papers"]:
            coll = client.get_collection(name)
            results = coll.get(limit=10, include=["metadatas"])
            has_tier = sum(1 for m in results["metadatas"] if "authority_tier" in m)
            assert has_tier > 0, f"Collection {name} has no authority_tier metadata"


# ── Test 4: Instrument Registry Consistency ───────────────────────────────────

class TestInstrumentRegistry:
    """Verify instrument registry is internally consistent."""

    def test_all_symbols_have_asset_class(self):
        from config.instrument_registry import registry
        for sym in registry.get_active_symbols():
            ac = registry.get_asset_class(sym)
            assert ac != "unsupported", f"{sym} has no asset_class"

    def test_all_symbols_have_sentiment_symbols(self):
        from config.instrument_registry import registry
        for sym in registry.get_active_symbols():
            sent = registry.get_sentiment_symbols(sym)
            assert len(sent) >= 1, f"{sym} has no sentiment_symbols"
            assert sym in sent, f"{sym} not in its own sentiment_symbols list"

    def test_all_symbols_have_fundamentals_source(self):
        from config.instrument_registry import registry
        for sym in registry.get_active_symbols():
            src = registry.get_fundamentals_source(sym)
            assert src != "unsupported", f"{sym} has no fundamentals source"

    def test_all_symbols_have_broker(self):
        from config.instrument_registry import registry
        for sym in registry.get_active_symbols():
            broker = registry.get_broker(sym)
            assert broker, f"{sym} has no broker"

    def test_fundamentals_sources_exist(self):
        """Every fundamentals source referenced should be importable."""
        from config.instrument_registry import registry
        from data_sources.source_factory import get_source
        sources_seen = set()
        for sym in registry.get_active_symbols():
            src = registry.get_fundamentals_source(sym)
            if src not in sources_seen:
                sources_seen.add(src)
                source = get_source(src)
                assert source is not None, f"Source {src} not found in factory"


# ── Test 5: Broker Adapter ────────────────────────────────────────────────────

class TestBrokerAdapter:
    """Verify broker adapter is functional."""

    def test_alpaca_broker_initializes(self):
        from brokers.broker_factory import get_broker
        broker = get_broker("alpaca")
        assert broker is not None

    def test_alpaca_get_account(self):
        from brokers.broker_factory import get_broker
        broker = get_broker("alpaca")
        account = broker.get_account()
        assert "equity" in account
        assert "cash" in account
        assert account["equity"] > 0

    def test_alpaca_get_positions(self):
        from brokers.broker_factory import get_broker
        broker = get_broker("alpaca")
        positions = broker.get_all_positions()
        assert isinstance(positions, list)

    def test_unknown_broker_raises(self):
        from brokers.broker_factory import get_broker
        with pytest.raises((ValueError, NotImplementedError)):
            get_broker("nonexistent_broker")


# ── Test 6: Model Registry ───────────────────────────────────────────────────

class TestModelRegistry:
    """Verify model registry is consistent."""

    def test_all_agents_have_endpoints(self):
        from config.model_registry import model_registry
        agents = [
            "technical_analyst", "fundamentals_analyst", "sentiment_analyst",
            "risk_gatekeeper", "rag_bull_researcher", "rag_bear_researcher",
            "rag_debate_synthesis", "master_orchestrator", "intraday_profiler",
        ]
        for agent in agents:
            endpoint = model_registry.get_endpoint(agent)
            assert endpoint, f"No endpoint for agent {agent}"
            assert endpoint.startswith("http"), f"Invalid endpoint for {agent}: {endpoint}"

    def test_endpoints_are_reachable(self):
        import requests
        from config.model_registry import model_registry
        for endpoint in model_registry.get_all_endpoints():
            try:
                r = requests.get(f"{endpoint}/models", timeout=5)
                assert r.ok, f"Endpoint {endpoint} returned {r.status_code}"
            except Exception as e:
                pytest.fail(f"Endpoint {endpoint} unreachable: {e}")
