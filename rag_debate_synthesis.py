"""
RAG-Enhanced Debate Synthesis — judges Bull vs Bear with literature evaluation.
Considers both argument quality AND academic backing strength.

DETERMINISM PATCH (2026-04-14):
- temperature set to 0.0 (greedy sampling) for reproducible verdicts
- seed=42 pinned for llama.cpp RNG determinism at non-zero temps (belt-and-suspenders)
- Same (bull_arg, bear_arg, symbol) inputs now produce bit-exact outputs
"""

from typing import TypedDict, Optional
from pydantic import BaseModel, Field
import chromadb

from rag_bull_researcher import generate_rag_bull_case, RagBullArgument
from rag_bear_researcher import generate_rag_bear_case, RagBearArgument
from agent_logger import log_agent_call

# Initialize ChromaDB for fact-checking citations
client = chromadb.PersistentClient(path="./chromadb-data")
methodology_collection = client.get_collection("methodology")


class LiteratureDebateSynthesis(BaseModel):
    symbol: str = Field(description="Ticker symbol")
    winning_side: str = Field(description="Bull or Bear - which argument was stronger")
    confidence: int = Field(description="Confidence in the winning side 1-10", ge=1, le=10)
    literature_quality: str = Field(description="Assessment of citation quality and academic backing")
    key_thesis: str = Field(description="The winning argument's core thesis")
    decisive_factors: list[str] = Field(description="2-4 factors that determined the winner")
    trade_recommendation: str = Field(description="Concrete trading action with position sizing guidance")
    risk_considerations: list[str] = Field(description="Key risk factors to monitor")


from config.llm_factory import create_llm
llm_deep = create_llm("rag_debate_synthesis", output_schema=LiteratureDebateSynthesis, max_tokens_override=6000)


def _compute_citation_metrics(citations) -> dict:
    """Count citation statistics — deterministic, not LLM work."""
    total = len(citations)
    grounded = sum(1 for c in citations
                   if c.chunk_ids and c.chunk_ids != ["NONE"])
    general = total - grounded
    unique_chunks = set()
    unique_authors = set()
    for c in citations:
        for cid in c.chunk_ids:
            if cid != "NONE":
                unique_chunks.add(cid)
        if c.author:
            unique_authors.add(c.author.split("(")[0].strip())

    # Authority tier distribution (from chunk IDs — METH/RISK are mostly tier 2-3,
    # PAPER is tier 2, COMM is tier 3). Approximate from prefix.
    tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    prefix_tier = {"PAPER": 2, "RISK": 2, "METH": 3, "COMM": 3}
    for c in citations:
        for cid in c.chunk_ids:
            if cid != "NONE":
                prefix = cid.split("-")[0] if "-" in cid else ""
                tier = prefix_tier.get(prefix, 3)
                tier_counts[tier] += 1

    return {
        "total_citations": total,
        "rag_grounded": grounded,
        "general_knowledge": general,
        "unique_chunks": len(unique_chunks),
        "unique_sources": len(unique_authors),
        "tier_1_2_citations": tier_counts[1] + tier_counts[2],
        "tier_3_4_citations": tier_counts[3] + tier_counts[4],
    }


@log_agent_call(agent_name="literature_judge", model_lane="deep")
def synthesize_literature_debate(bull_arg: RagBullArgument, bear_arg: RagBearArgument, symbol: str) -> LiteratureDebateSynthesis:
    """Judge Bull vs Bear debate considering literature quality and academic backing."""

    # Fix 4: Compute citation metrics in Python, pass as structured data
    bull_metrics = _compute_citation_metrics(bull_arg.literature_citations)
    bear_metrics = _compute_citation_metrics(bear_arg.literature_citations)

    prompt = f"""You are an expert judge evaluating a literature-backed Bull vs Bear debate for {symbol}.
Your job is to determine which side presented the stronger case based on BOTH argument quality AND academic backing.

BULL ARGUMENT:
Strength: {bull_arg.strength} | Conviction: {bull_arg.conviction}/10
Thesis: {bull_arg.thesis}
Key Supports: {bull_arg.key_supports}
Literature Citations:
{chr(10).join(f'  [{", ".join(c.chunk_ids)}] {c.author}: {c.claim}' for c in bull_arg.literature_citations)}
Rebuttals: {bull_arg.rebuttals}

BEAR ARGUMENT:
Strength: {bear_arg.strength} | Conviction: {bear_arg.conviction}/10
Thesis: {bear_arg.thesis}
Key Risks: {bear_arg.key_risks}
Literature Citations:
{chr(10).join(f'  [{", ".join(c.chunk_ids)}] {c.author}: {c.claim}' for c in bear_arg.literature_citations)}
Bull Rebuttals: {bear_arg.bull_rebuttals}

CITATION METRICS (computed by code — these counts are authoritative):
- Bull: {bull_metrics['total_citations']} citations ({bull_metrics['rag_grounded']} RAG-grounded, {bull_metrics['general_knowledge']} general knowledge) from {bull_metrics['unique_sources']} unique sources using {bull_metrics['unique_chunks']} unique chunks | Authority: {bull_metrics['tier_1_2_citations']} academic (tier 1-2), {bull_metrics['tier_3_4_citations']} practitioner (tier 3-4)
- Bear: {bear_metrics['total_citations']} citations ({bear_metrics['rag_grounded']} RAG-grounded, {bear_metrics['general_knowledge']} general knowledge) from {bear_metrics['unique_sources']} unique sources using {bear_metrics['unique_chunks']} unique chunks | Authority: {bear_metrics['tier_1_2_citations']} academic (tier 1-2), {bear_metrics['tier_3_4_citations']} practitioner (tier 3-4)

EVALUATION CRITERIA:
1. **Citation Quality**: Use the CITATION METRICS above (do not recount). More RAG-grounded citations from more unique sources = stronger evidence base.
2. **Historical Precedent**: Which side provided better documented examples from the literature chunks?
3. **Argument Coherence**: Which side better integrated academic insights with market signals?
4. **Rebuttal Strength**: Which side more effectively countered opposing literature?
5. **Practical Applicability**: Which thesis is more actionable for trading?

JUDGE FAIRLY: Don't automatically favor Bull or Bear. The side with superior evidence and reasoning wins.
Consider that high conviction with weak presentation might lose to moderate conviction with strong literature backing.

Your decision should result in a clear trading recommendation with risk management guidance."""

    synthesis = llm_deep.invoke(prompt)
    if not synthesis.symbol:
        synthesis.symbol = symbol

    return synthesis


def run_complete_literature_debate(symbol: str):
    """Run complete RAG-enhanced Bull vs Bear debate with literature evaluation."""
    print(f"=== LITERATURE-BACKED DEBATE for {symbol} ===\n")

    # Get analyst reports
    from dual_analyst import dual_graph
    analyst_result = dual_graph.invoke({"symbol": symbol})

    print("Step 1: RAG Bull Researcher building literature-backed optimistic case...")
    bull_result = generate_rag_bull_case({
        "symbol": symbol,
        "technical_report": analyst_result["technical_report"],
        "fundamentals_report": analyst_result["fundamentals_report"]
    })

    print("Step 2: RAG Bear Researcher building literature-backed pessimistic case...")
    bear_result = generate_rag_bear_case({
        "symbol": symbol,
        "technical_report": analyst_result["technical_report"],
        "fundamentals_report": analyst_result["fundamentals_report"]
    })

    print("Step 3: Literature-aware synthesis judge evaluating academic backing...\n")
    synthesis = synthesize_literature_debate(
        bull_result["rag_bull_argument"],
        bear_result["rag_bear_argument"],
        symbol
    )

    # Display results
    bull = bull_result["rag_bull_argument"]
    bear = bear_result["rag_bear_argument"]

    print("="*70)
    print(f"BULL: {bull.strength} strength, {bull.conviction}/10 conviction")
    print(f"Citations: {len(bull.literature_citations)} literature references")

    print(f"\nBEAR: {bear.strength} strength, {bear.conviction}/10 conviction")
    print(f"Citations: {len(bear.literature_citations)} literature references")

    print("="*70)
    print(f"WINNER: {synthesis.winning_side} (Confidence: {synthesis.confidence}/10)")
    print(f"Literature Quality: {synthesis.literature_quality}")
    print(f"\nKey Thesis: {synthesis.key_thesis}")

    print(f"\nDecisive Factors:")
    for i, factor in enumerate(synthesis.decisive_factors, 1):
        print(f"  {i}. {factor}")

    print(f"\nTrade Recommendation: {synthesis.trade_recommendation}")

    print(f"\nRisk Considerations:")
    for i, risk in enumerate(synthesis.risk_considerations, 1):
        print(f"  {i}. {risk}")

    return synthesis


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"

    result = run_complete_literature_debate(symbol)

