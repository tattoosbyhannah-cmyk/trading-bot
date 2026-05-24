"""
Dual Analyst Orchestrator — Technical + Fundamentals running in parallel.
Demonstrates the parallel-analysts pattern from the trading architecture.
"""

import asyncio
from typing import TypedDict, Optional
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, END

# Import both analysts
from technical_analyst import graph as technical_graph, TechnicalReport
from fundamentals_analyst import graph as fundamentals_graph, FundamentalsReport


class DualAnalysisState(TypedDict):
    symbol: str
    calculation_run_id: Optional[str]
    # Backtest support — propagated to technical_graph (and future
    # fundamentals_graph) so all data fetches respect the historical cutoff.
    as_of_date: Optional[str]
    technical_report: Optional[TechnicalReport]
    fundamentals_report: Optional[FundamentalsReport]
    summary: Optional[str]


def run_technical(state: DualAnalysisState) -> DualAnalysisState:
    """Run Technical Analyst and extract just the report."""
    calc_id = state.get("calculation_run_id", "")
    if calc_id:
        from agent_logger import set_calculation_run_id
        set_calculation_run_id(calc_id)
    result = technical_graph.invoke({
        "symbol": state["symbol"],
        "calculation_run_id": calc_id,
        "as_of_date": state.get("as_of_date"),
    })
    return {"technical_report": result["technical_report"]}


def run_fundamentals(state: DualAnalysisState) -> DualAnalysisState:
    """Run Fundamentals Analyst and extract just the report."""
    calc_id = state.get("calculation_run_id", "")
    if calc_id:
        from agent_logger import set_calculation_run_id
        set_calculation_run_id(calc_id)
    result = fundamentals_graph.invoke({
        "symbol": state["symbol"],
        "calculation_run_id": calc_id,
        "as_of_date": state.get("as_of_date"),
    })
    return {"fundamentals_report": result["fundamentals_report"]}


def synthesize_reports(state: DualAnalysisState) -> DualAnalysisState:
    """Combine both analyst reports into a summary."""
    tech = state["technical_report"]
    fund = state["fundamentals_report"]
    
    summary = f"""=== DUAL ANALYST SYNTHESIS for {state['symbol']} ===

TECHNICAL ANALYSIS:
  Trend: {tech.trend} / Strength: {tech.strength}
  Key: {tech.key_observation}

FUNDAMENTALS ANALYSIS:  
  Bias: {fund.bias} / Conviction: {fund.conviction}
  Key: {fund.key_driver}

SIGNAL ALIGNMENT: {'✓ ALIGNED' if tech.trend == fund.bias else '⚠ DIVERGENT'}
  Technical={tech.trend}, Fundamentals={fund.bias}

CONVICTION DIFFERENTIAL: Tech={tech.strength} vs Fund={fund.conviction}
"""
    
    return {"summary": summary}


# Build the dual-analyst graph
workflow = StateGraph(DualAnalysisState)
workflow.add_node("technical", run_technical)
workflow.add_node("fundamentals", run_fundamentals) 
workflow.add_node("synthesize", synthesize_reports)

workflow.set_entry_point("technical")
workflow.set_entry_point("fundamentals")  # Both start in parallel

workflow.add_edge("technical", "synthesize")
workflow.add_edge("fundamentals", "synthesize")
workflow.add_edge("synthesize", END)

dual_graph = workflow.compile()


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USO"
    print(f"\n=== Running Dual Analyst for {symbol} ===")
    print("Starting Technical and Fundamentals analysts in parallel...")
    
    result = dual_graph.invoke({"symbol": symbol})
    print(result["summary"])
    
    # Also show the raw structured reports for debugging
    print("\n--- Raw Technical Report ---")
    tech = result["technical_report"]
    print(f"trend={tech.trend}, strength={tech.strength}")
    print(f"rationale: {tech.rationale}")
    
    print("\n--- Raw Fundamentals Report ---")
    fund = result["fundamentals_report"] 
    print(f"bias={fund.bias}, conviction={fund.conviction}")
    print(f"rationale: {fund.rationale}")