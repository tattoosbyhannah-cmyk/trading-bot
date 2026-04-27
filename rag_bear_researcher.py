"""
RAG-Enhanced Bear Researcher — cites trading literature to build pessimistic cases.
Counterpart to RAG Bull Researcher with literature-backed bearish arguments.
"""

import logging
from typing import TypedDict, Optional, Literal, List
from pydantic import BaseModel, Field
import chromadb

from technical_analyst import TechnicalReport
from fundamentals_analyst import FundamentalsReport
from agent_logger import log_agent_call

# Initialize ChromaDB client
client = chromadb.PersistentClient(path="./chromadb-data")
methodology_collection = client.get_collection("methodology")
risk_mgmt_collection = client.get_collection("risk_mgmt")
papers_collection = client.get_collection("papers")
commodities_collection = client.get_collection("commodities")


class LiteratureCitation(BaseModel):
    claim: str = Field(description="The specific claim being made")
    chunk_ids: List[str] = Field(description="List of chunk IDs (e.g. METH-001, COMM-003) from the provided literature that support this claim. Use NONE if claim is from general knowledge.")
    author: str = Field(description="Author name from the cited chunk")


class RagBearArgument(BaseModel):
    symbol: str = Field(description="Ticker symbol")
    strength: Literal["compelling", "moderate", "weak"] = Field(description="Strength of the bear case")
    thesis: str = Field(description="2-3 sentence bear thesis")
    key_risks: List[str] = Field(description="3-5 specific risk factors from analyst data")
    literature_citations: List[LiteratureCitation] = Field(description="2-4 citations with chunk ID references")
    bull_rebuttals: List[str] = Field(description="2-3 counter-arguments to likely bull points")
    conviction: int = Field(description="Bear conviction score 1-10", ge=1, le=10)


def retrieve_bear_knowledge(query: str, n_results: int = 3,
                            as_of_date: str = None) -> List[dict]:
    """Retrieve relevant knowledge chunks focusing on risk and downside patterns.

    Args:
        as_of_date: If set (YYYY-MM-DD), only return chunks published on or before
                    this date. Prevents look-ahead bias in backtests.
    """
    results = []

    collections = [
        ("METH", methodology_collection, n_results),
        ("RISK", risk_mgmt_collection, 3),
        ("COMM", commodities_collection, n_results),
        ("PAPER", papers_collection, 2),
    ]

    where_filter = None
    if as_of_date:
        date_int = int(as_of_date.replace("-", ""))
        where_filter = {"published_date_int": {"$lte": date_int}}

    for prefix, collection, n in collections:
        query_kwargs = {"query_texts": [query], "n_results": n}
        if where_filter:
            query_kwargs["where"] = where_filter
        try:
            coll_results = collection.query(**query_kwargs)
        except Exception:
            coll_results = collection.query(query_texts=[query], n_results=n)
        distances = coll_results.get('distances', [[]])[0]
        for i, (doc, metadata) in enumerate(
            zip(coll_results['documents'][0], coll_results['metadatas'][0]), 1
        ):
            dist = distances[i - 1] if i - 1 < len(distances) else None
            results.append({
                "chunk_id": f"{prefix}-{i:03d}",
                "author": metadata.get('author', 'Unknown'),
                "title": metadata.get('title', 'Unknown'),
                "text": doc[:400],
                "collection": collection.name,
                "distance": round(dist, 4) if dist is not None else None,
                "authority_tier": metadata.get('authority_tier', 3),
            })

    # Sort by authority tier (lower = more authoritative), then by distance
    results.sort(key=lambda r: (r.get("authority_tier", 3),
                                 r.get("distance") or 999))
    return results


from config.llm_factory import create_llm
llm_deep = create_llm("rag_bear_researcher", output_schema=RagBearArgument, max_tokens_override=8000)


@log_agent_call(agent_name="bear_researcher", model_lane="deep")
def generate_rag_bear_case(state) -> dict:
    """Generate bear argument enhanced with RAG knowledge retrieval."""
    tech = state["technical_report"]
    fund = state["fundamentals_report"]
    symbol = state["symbol"]
    
    # Retrieve relevant bearish knowledge
    rag_queries = [
        f"bearish {symbol} inventory oversupply fundamental analysis commodity",
        "technical analysis fails fundamental pressure mean reversion",
        "volume divergence signal weakness momentum breakdown",
        "risk management position sizing drawdown control"
    ]
    
    rag_n = state.get("rag_query_n", 2)
    rag_limit = state.get("rag_chunks_limit", 8)
    as_of_date = state.get("as_of_date")

    knowledge_context = []
    for query in rag_queries:
        knowledge_context.extend(retrieve_bear_knowledge(query, rag_n,
                                                          as_of_date=as_of_date))

    # Deduplicate and limit
    seen_texts = set()
    unique_chunks = []
    for chunk in knowledge_context:
        key = chunk["text"][:100]
        if key not in seen_texts:
            seen_texts.add(key)
            unique_chunks.append(chunk)

    chunks_for_prompt = unique_chunks[:rag_limit]

    knowledge_str = "\n\n".join(
        f'[{c["chunk_id"]}] {c["author"]} - {c["title"]}:\n{c["text"]}'
        for c in chunks_for_prompt
    )

    chunk_ids_provided = [c["chunk_id"] for c in chunks_for_prompt]

    # Build price context for prompt grounding
    if tech.latest_close:
        price_context = (
            f"CURRENT MARKET PRICE: ${tech.latest_close:.2f} "
            f"(20-SMA: ${tech.sma_20:.2f}, 5-SMA: ${tech.sma_5:.2f}, "
            f"RSI: {tech.rsi_14:.1f})\n"
            f"REQUIRED: Any price levels, stops, targets, or trade examples MUST be "
            f"within reasonable proximity to ${tech.latest_close:.2f}. Do NOT invent "
            f"price levels far from current. If discussing historical examples with "
            f"different price ranges, label them clearly as historical.\n\n"
        )
    else:
        price_context = "CURRENT MARKET PRICE: UNAVAILABLE\n\n"
    prompt = f"""You are a bear researcher with access to trading literature. Build the strongest bear case for {symbol} using both analyst data AND relevant academic/professional knowledge focused on risk and downside patterns.

{price_context}ANALYST REPORTS:
Technical: {tech.trend}/{tech.strength} - {tech.rationale}
Fundamentals: {fund.bias}/{fund.conviction} - {fund.rationale}

RELEVANT TRADING LITERATURE:
{knowledge_str}

CITATION RULES (CRITICAL):
Each piece of literature above has a chunk ID in brackets like [METH-001].
When citing literature, you MUST reference the specific chunk ID(s) that support your claim.
- If your claim comes from the provided literature chunks, list the chunk IDs
- If your claim comes from general knowledge (not in the chunks above), use ["NONE"]
- Do NOT fabricate page numbers, edition years, or specific quotes not in the chunks
- Do NOT cite chunks that don't support your specific claim
- Only chunks with IDs in the format PREFIX-NNN (e.g., METH-001, COMM-003) are valid sources. The ANALYST REPORTS section above is prompt context, NOT a citable chunk. Never use 'ANALYST_REPORTS' or any other invented ID.

Example citation:
  claim: "Momentum decay accelerates when volume declines in oversold conditions"
  chunk_ids: ["COMM-002", "METH-001"]
  author: "Kaufman"

Build a literature-backed bear case focusing on documented downside patterns and risk factors."""

    argument = llm_deep.invoke(prompt)
    if not argument.symbol:
        argument.symbol = symbol

    # Validate chunk references
    for citation in argument.literature_citations:
        invalid_ids = [
            cid for cid in citation.chunk_ids
            if cid != "NONE" and cid not in chunk_ids_provided
        ]
        if invalid_ids:
            logging.warning(
                f"Hallucinated chunk IDs in {symbol} bear citation: {invalid_ids}"
            )
            citation.chunk_ids = [
                cid for cid in citation.chunk_ids
                if cid == "NONE" or cid in chunk_ids_provided
            ]

    return {"rag_bear_argument": argument}


if __name__ == "__main__":
    # Test with real analyst data
    from dual_analyst import dual_graph
    
    symbol = "USO"
    print(f"=== RAG-Enhanced Bear Case for {symbol} ===\n")
    
    # Get fresh analyst reports
    analyst_result = dual_graph.invoke({"symbol": symbol})
    
    # Generate RAG-enhanced bear case
    result = generate_rag_bear_case({
        "symbol": symbol,
        "technical_report": analyst_result["technical_report"],
        "fundamentals_report": analyst_result["fundamentals_report"]
    })
    
    bear = result["rag_bear_argument"]
    print(f"Strength: {bear.strength} (Conviction: {bear.conviction}/10)")
    print(f"\nThesis: {bear.thesis}")
    
    print(f"\nKey Risks:")
    for i, risk in enumerate(bear.key_risks, 1):
        print(f"  {i}. {risk}")
    
    print(f"\nLiterature Citations:")
    for i, citation in enumerate(bear.literature_citations, 1):
        print(f"  {i}. [{', '.join(citation.chunk_ids)}] {citation.author}: {citation.claim}")
    
    print(f"\nRebuttals to Bull Case:")
    for i, rebuttal in enumerate(bear.bull_rebuttals, 1):
        print(f"  {i}. {rebuttal}")