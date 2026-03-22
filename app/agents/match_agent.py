"""
LangGraph Match Analysis Agent — Graph Orchestration
=====================================================
Wires the three nodes (fetch_context → fetch_historical → generate_narrative)
into a compiled StateGraph and exposes `run_full_analysis()` as the public API.

Node implementations live in app.agents.nodes.
"""

import logging
from typing import Optional

from langgraph.graph import END, StateGraph

from app.agents.nodes import (
    AgentState,
    fetch_context_node,
    fetch_historical_node,
    generate_narrative_node,
)
from app.schemas.agent import FullMatchAnalysis
from app.schemas.match import MatchContext, NarrativeResponse

logger = logging.getLogger(__name__)


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_match_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("fetch_context",     fetch_context_node)
    workflow.add_node("fetch_historical",  fetch_historical_node)
    workflow.add_node("generate_narrative", generate_narrative_node)

    workflow.set_entry_point("fetch_context")
    workflow.add_edge("fetch_context",    "fetch_historical")
    workflow.add_edge("fetch_historical", "generate_narrative")
    workflow.add_edge("generate_narrative", END)

    return workflow.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_match_graph()
    return _graph


# ── Public API ────────────────────────────────────────────────────────────────

async def run_full_analysis(event_id: str) -> FullMatchAnalysis:
    """
    Runs the full multi-step LangGraph agent for a match.
    Returns a FullMatchAnalysis with all enriched data.
    """
    graph = _get_graph()

    initial_state: AgentState = {
        "event_id":       event_id,
        "match":          None,
        "h2h":            None,
        "stats_trend":    None,
        "lineup":         None,
        "home_form":      None,
        "away_form":      None,
        "prediction":     None,
        "narrative":      None,
        "goal_risk_score": None,
        "card_risk_score": None,
        "agent_steps":    [],
        "errors":         [],
    }

    final_state = await graph.ainvoke(initial_state)

    match: Optional[MatchContext] = final_state.get("match")
    if not match:
        raise ValueError(f"Could not fetch match data for event_id={event_id}")

    narrative: Optional[NarrativeResponse] = final_state.get("narrative")
    if not narrative:
        from app.services.narrative import generate_narrative
        narrative = await generate_narrative(match)

    return FullMatchAnalysis(
        match=match,
        narrative=narrative,
        prediction=final_state.get("prediction"),
        h2h=final_state.get("h2h"),
        stats_trend=final_state.get("stats_trend"),
        lineup=final_state.get("lineup"),
        home_form=final_state.get("home_form"),
        away_form=final_state.get("away_form"),
        goal_risk_score=final_state.get("goal_risk_score"),
        card_risk_score=final_state.get("card_risk_score"),
        agent_steps=final_state.get("agent_steps", []),
    )
