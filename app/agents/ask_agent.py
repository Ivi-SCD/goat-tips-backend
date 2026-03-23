"""
Ask Agent — LangGraph Multi-Agent Supervisor
=============================================
Replaces the single tool-calling loop in narrative.answer_question /
answer_general_question with a 6-agent orchestration:

  1. IntentRouterAgent   (intent classification — keyword heuristic + fallback)
  2. LiveContextAgent    (BetsAPI live/upcoming/odds)
  3. HistoricalStatsAgent (Supabase form + H2H)
  4. PlayerIntelAgent    (Supabase snapshots from retrain)
  5. QuantAgent          (Poisson model output)
  6. NarrativeVerifierAgent (final LLM synthesis)

Graph:
  intent_router → parallel_gather → quant_agent → narrative_verifier → END

SLA targets (from spec):
  • Per-agent timeout: 1.8 s
  • Total graph budget: 6.5 s (enforced by asyncio.wait_for around ainvoke)
  • Degraded mode: partial_context=True when any agent fails/times out

Public API:
  run_ask_agent(question, event_id, match_context_text, history) → NarrativeResponse
"""

import json
import logging
import uuid
from typing import Literal, Optional

from langgraph.graph import END, StateGraph



from app.agents.ask_nodes import (
    AskState,
    intent_router_node,
    narrative_verifier_node,
    parallel_gather_node,
    quant_agent_node,
)
from app.schemas.match import NarrativeResponse

logger = logging.getLogger(__name__)

_EMPTY_FINAL = json.dumps({
    "headline": "Sem resposta disponível",
    "analysis": "Não foi possível gerar a análise no momento.",
    "prediction": "",
    "momentum_signal": None,
    "confidence_label": "Baixa",
})


# ── Conditional edges ─────────────────────────────────────────────────────────

def _after_intent(state: AskState) -> Literal["parallel_gather"]:
    # Always proceed to parallel gather after routing
    return "parallel_gather"


def _after_gather(state: AskState) -> Literal["quant_agent"]:
    return "quant_agent"


def _after_quant(state: AskState) -> Literal["narrative_verifier"]:
    return "narrative_verifier"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_ask_graph() -> StateGraph:
    workflow = StateGraph(AskState)

    workflow.add_node("intent_router",      intent_router_node)
    workflow.add_node("parallel_gather",    parallel_gather_node)
    workflow.add_node("quant_agent",        quant_agent_node)
    workflow.add_node("narrative_verifier", narrative_verifier_node)

    workflow.set_entry_point("intent_router")

    workflow.add_conditional_edges(
        "intent_router", _after_intent, {"parallel_gather": "parallel_gather"},
    )
    workflow.add_conditional_edges(
        "parallel_gather", _after_gather, {"quant_agent": "quant_agent"},
    )
    workflow.add_conditional_edges(
        "quant_agent", _after_quant, {"narrative_verifier": "narrative_verifier"},
    )
    workflow.add_edge("narrative_verifier", END)

    return workflow.compile()


_graph = None


def _get_ask_graph():
    global _graph
    if _graph is None:
        _graph = build_ask_graph()
    return _graph


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_final_answer(event_id: str, raw: str) -> dict:
    """Parse the LLM's raw text (possibly fenced JSON) into a dict."""
    text = raw.strip()

    # Strip markdown fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            candidate = part.lstrip("json").strip()
            if candidate.startswith("{"):
                text = candidate
                break

    # Try full parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first JSON object in the text (handles LLM preamble)
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    return {
        "headline": "Resposta fora do formato esperado",
        "analysis": text,
        "prediction": "",
        "momentum_signal": None,
        "confidence_label": "Baixa",
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def run_ask_agent(
    question: str,
    event_id: str = "",
    match_context_text: str = "",
    history: Optional[list[dict]] = None,
) -> NarrativeResponse:
    """
    Run the 6-agent LangGraph ask pipeline and return a NarrativeResponse.

    Args:
        question:           User's free-form question.
        event_id:           BetsAPI event ID, or "" for general questions.
        match_context_text: Pre-formatted match context block (from narrative._build_context_prompt).
        history:            Prior conversation turns [{role, content}, ...].

    Returns:
        NarrativeResponse with optional multi-agent observability fields populated.
    """
    graph = _get_ask_graph()
    trace_id = str(uuid.uuid4())

    initial_state: AskState = {
        "question":           question,
        "event_id":           event_id,
        "history":            history or [],
        "intent":             "GENERAL",
        "match_context_text": match_context_text,
        "artifacts":          [],
        "quant_output":       None,
        "final_answer":       None,
        "quality_flags":      [],
        "partial_context":    False,
        "confidence_score":   0.5,
        "data_sources":       [],
        "agent_trace_id":     trace_id,
    }

    final_state = await graph.ainvoke(initial_state)

    raw = final_state.get("final_answer") or _EMPTY_FINAL
    parsed = _parse_final_answer(event_id or "general", raw)

    return NarrativeResponse(
        match_id=event_id or "general",
        headline=parsed.get("headline", ""),
        analysis=parsed.get("analysis", ""),
        prediction=parsed.get("prediction", ""),
        momentum_signal=parsed.get("momentum_signal"),
        confidence_label=parsed.get("confidence_label", "Baixa"),
        # Multi-agent observability
        confidence_score=final_state.get("confidence_score"),
        data_sources=final_state.get("data_sources") or [],
        partial_context=final_state.get("partial_context", False),
        agent_trace_id=trace_id,
    )
