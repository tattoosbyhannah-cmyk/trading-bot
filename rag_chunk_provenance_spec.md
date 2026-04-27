# RAG Chunk Provenance — Implementation Spec

## Problem
Bull/bear researchers cite author names but hallucinate page numbers, editions, and sometimes entire claims. No way to verify whether a citation actually came from RAG or from LLM training data.

## Solution
Tag each RAG chunk with a unique ID at injection time. Require the LLM to reference chunk IDs in its citations. Post-hoc, validate that cited IDs exist and that the claim matches the chunk content.

## Files to modify
- `rag_bull_researcher.py`
- `rag_bear_researcher.py`
- Both files have identical structure (confirmed via diff), apply same changes to both.

## Change 1: Modify `retrieve_relevant_knowledge()` / `retrieve_bear_knowledge()`

Currently returns `List[str]` with format: `[author - title]: doc[:300]...`

Change to return `List[dict]` with structure:
```python
{
    "chunk_id": "METH-001",      # collection prefix + sequential number
    "author": "Clenow",
    "title": "Following the Trend",
    "text": "first 400 chars of chunk...",  # increase from 300 to 400
    "collection": "methodology"
}
```

Collection prefixes: `METH`, `RISK`, `COMM`, `PAPER`

Sequential numbering per call (not global). Example: if methodology returns 2 chunks and commodities returns 3, you get METH-001, METH-002, COMM-001, COMM-002, COMM-003.

Implementation sketch:
```python
def retrieve_relevant_knowledge(query: str, n_results: int = 3) -> List[dict]:
    results = []
    
    collections = [
        ("METH", methodology_collection, n_results),
        ("RISK", risk_mgmt_collection, 2),
        ("COMM", commodities_collection, n_results),
        ("PAPER", papers_collection, 2),
    ]
    
    for prefix, collection, n in collections:
        coll_results = collection.query(
            query_texts=[query],
            n_results=n
        )
        for i, (doc, metadata) in enumerate(
            zip(coll_results['documents'][0], coll_results['metadatas'][0]), 1
        ):
            results.append({
                "chunk_id": f"{prefix}-{i:03d}",
                "author": metadata.get('author', 'Unknown'),
                "title": metadata.get('title', 'Unknown'),
                "text": doc[:400],
                "collection": collection.name,
            })
    
    return results
```

## Change 2: Format chunks with IDs for prompt injection

In `generate_rag_bull_case()` / `generate_rag_bear_case()`, replace:
```python
knowledge_str = "\n\n".join(knowledge_context[:6])
```

With:
```python
# Deduplicate and limit
seen_texts = set()
unique_chunks = []
for chunk in knowledge_context:
    key = chunk["text"][:100]
    if key not in seen_texts:
        seen_texts.add(key)
        unique_chunks.append(chunk)

chunks_for_prompt = unique_chunks[:8]  # up from 6

knowledge_str = "\n\n".join(
    f'[{c["chunk_id"]}] {c["author"]} - {c["title"]}:\n{c["text"]}'
    for c in chunks_for_prompt
)

# Build lookup for validation
chunk_ids_provided = [c["chunk_id"] for c in chunks_for_prompt]
```

## Change 3: Update schema — `RagBullArgument` / `RagBearArgument`

Add `rag_chunk_ids` to each citation. Replace the `literature_citations` field:

```python
class LiteratureCitation(BaseModel):
    claim: str = Field(description="The specific claim being made")
    chunk_ids: List[str] = Field(description="List of chunk IDs (e.g. METH-001, COMM-003) from the provided literature that support this claim. Use NONE if claim is from general knowledge.")
    author: str = Field(description="Author name from the cited chunk")

class RagBullArgument(BaseModel):
    symbol: str = Field(description="Ticker symbol")
    strength: Literal["compelling", "moderate", "weak"] = Field(description="Strength of the bull case")
    thesis: str = Field(description="2-3 sentence bull thesis")
    key_supports: List[str] = Field(description="3-5 specific supporting points from analyst data")
    literature_citations: List[LiteratureCitation] = Field(description="2-4 citations with chunk ID references")
    rebuttals: List[str] = Field(description="2-3 counter-arguments to likely bear points")
    conviction: int = Field(description="Bull conviction score 1-10", ge=1, le=10)
```

Same change to `RagBearArgument` in `rag_bear_researcher.py`.

## Change 4: Update prompt instructions

Replace the citation instructions block (lines ~130-140 in both files) with:

```
CITATION RULES (CRITICAL):
Each piece of literature above has a chunk ID in brackets like [METH-001].
When citing literature, you MUST reference the specific chunk ID(s) that support your claim.
- If your claim comes from the provided literature chunks, list the chunk IDs
- If your claim comes from general knowledge (not in the chunks above), use ["NONE"]
- Do NOT fabricate page numbers, edition years, or specific quotes not in the chunks
- Do NOT cite chunks that don't support your specific claim

Example citation:
  claim: "Momentum decay accelerates when volume declines in oversold conditions"
  chunk_ids: ["COMM-002", "METH-001"]
  author: "Kaufman"
```

## Change 5: Post-hoc validation in the caller

After `llm_deep.invoke(prompt)` returns, validate chunk IDs:

```python
argument = llm_deep.invoke(prompt)

# Validate chunk references
for citation in argument.literature_citations:
    invalid_ids = [
        cid for cid in citation.chunk_ids 
        if cid != "NONE" and cid not in chunk_ids_provided
    ]
    if invalid_ids:
        # Log hallucinated chunk references
        import logging
        logging.warning(
            f"Hallucinated chunk IDs in {symbol} bull citation: {invalid_ids}"
        )
        # Optionally strip invalid IDs
        citation.chunk_ids = [
            cid for cid in citation.chunk_ids 
            if cid == "NONE" or cid in chunk_ids_provided
        ]
```

## Change 6: Update agent_logger decision_fields

The `@log_agent_call` decorator should capture the new citation structure. In the JSONL, citations should appear as:
```json
{
  "literature_citations": [
    {
      "claim": "...",
      "chunk_ids": ["COMM-002"],
      "author": "Johnson"
    }
  ]
}
```

No changes needed to `agent_logger.py` if the decorator already serializes the full Pydantic model to `decision_fields`. Verify this — if it only extracts specific fields, add `literature_citations` to the extraction.

## Change 7: Update downstream consumers

`literature_judge.py` consumes bull/bear citations. Check how it currently reads `literature_citations` — if it expects `List[str]`, update to handle `List[LiteratureCitation]`. The judge prompt should receive citations formatted as:
```
[COMM-002] Johnson: "Momentum decay accelerates when volume declines..."
[NONE] General knowledge: "RSI below 30 is typically considered oversold"
```

This lets the judge weight RAG-grounded citations higher than general-knowledge ones.

`master_orchestrator.py` also displays citations in its output. Update the agent_consensus formatting (around line 219) to handle the new structure.

## Testing

After implementation, run:
```bash
python majority_vote_orchestrator.py USO 2>&1 | tee logs/uso_provenance_test.log
```

Check the JSONL logs:
```python
from agent_logger import query_logs
import json
logs = query_logs(agent='bull_researcher', symbol='USO', since_minutes=30)
for e in logs:
    for c in e.get('decision_fields', {}).get('literature_citations', []):
        print(f"  IDs: {c.get('chunk_ids')} | Author: {c.get('author')} | Claim: {c.get('claim', '')[:80]}")
```

Success criteria:
- Most citations reference valid chunk IDs (not NONE)
- No hallucinated chunk IDs in logs (warning log stays silent)
- NONE citations are clearly general-knowledge claims, not fake-attributed
- Judge can distinguish RAG-grounded vs general-knowledge citations

## Estimated time: 1.5-2 hours
