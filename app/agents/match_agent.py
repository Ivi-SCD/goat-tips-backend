"""
LangGraph Match Analysis Agent — Graph Orchestration
=====================================================
Wires the three nodes (fetch_context → fetch_historical → generate_narrative)
into a compiled StateGraph and exposes `run_full_analysis()` as the public API.

Conditional edges:
  - fetch_context  → "no_match" → END  (match not found — abort early)
  - fetch_context  → "ok"       → fetch_historical
  - fetch_historical → "skip_narrative" → END  (all predictions failed + no match context)
  - fetch_historical → "ok"             → generate_narrative

Node implementations live in app.agents.nodes.
"""

import logging
from typing import Literal, Optional

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


# ── Conditional edge functions ────────────────────────────────────────────────

def _after_fetch_context(state: AgentState) -> Literal["ok", "no_match"]:
    """Abort pipeline if the base match context could not be fetched."""
    if state.get("match") is None:
        logger.warning("Agent: match context unavailable — aborting pipeline (event_id=%s)",
                       state.get("event_id"))
        return "no_match"
    return "ok"


def _after_fetch_historical(state: AgentState) -> Literal["ok", "skip_narrative"]:
    """Skip narrative generation only if both prediction and match are missing."""
    has_prediction = state.get("prediction") is not None
    has_match = state.get("match") is not None
    if not has_match and not has_prediction:
        logger.warning("Agent: no match or prediction data — skipping narrative")
        return "skip_narrative"
    return "ok"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_match_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("fetch_context",      fetch_context_node)
    workflow.add_node("fetch_historical",   fetch_historical_node)
    workflow.add_node("generate_narrative", generate_narrative_node)

    workflow.set_entry_point("fetch_context")

    # Conditional: only proceed to historical data if match was fetched
    workflow.add_conditional_edges(
        "fetch_context",
        _after_fetch_context,
        {"ok": "fetch_historical", "no_match": END},
    )

    # Conditional: only generate narrative if there is something to narrate
    workflow.add_conditional_edges(
        "fetch_historical",
        _after_fetch_historical,
        {"ok": "generate_narrative", "skip_narrative": END},
    )

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
