"""
Agent Node Definitions
======================
Contains AgentState schema and the three LangGraph node functions.
Imported by match_agent.py which owns the graph wiring.
"""

import asyncio
import logging
from typing import Any, Optional, TypedDict

from app.schemas.agent import FullMatchAnalysis  # noqa: F401 – re-export convenience
from app.schemas.analytics import H2HRecord, TeamForm
from app.schemas.match import LineupInfo, MatchContext, NarrativeResponse, StatsTrend
from app.schemas.prediction import ScorePredictionResponse

logger = logging.getLogger(__name__)


# ── State schema ──────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    event_id:       str
    match:          Optional[MatchContext]
    h2h:            Optional[H2HRecord]
    stats_trend:    Optional[StatsTrend]
    lineup:         Optional[LineupInfo]
    home_form:      Optional[TeamForm]
    away_form:      Optional[TeamForm]
    prediction:     Optional[ScorePredictionResponse]
    narrative:      Optional[NarrativeResponse]
    goal_risk_score: Optional[float]
    card_risk_score: Optional[float]
    agent_steps:    list[str]
    errors:         list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _step(state: AgentState, name: str) -> list[str]:
    return state["agent_steps"] + [name]


def _errors(state: AgentState) -> list[str]:
    return list(state.get("errors", []))


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def fetch_context_node(state: AgentState) -> dict[str, Any]:
    """Fetch base match context + H2H + stats trend + lineup in parallel."""
    from app.services.betsapi import get_h2h, get_lineup, get_match_by_id, get_stats_trend

    event_id = state["event_id"]
    steps  = _step(state, "fetch_context")
    errors = _errors(state)

    match, h2h, stats, lineup = await asyncio.gather(
        get_match_by_id(event_id),
        get_h2h(event_id),
        get_stats_trend(event_id),
        get_lineup(event_id),
        return_exceptions=True,
    )

    result: dict[str, Any] = {"agent_steps": steps, "errors": errors}

    for key, value, label in [
        ("match",       match,  "fetch_match"),
        ("h2h",         h2h,    "fetch_h2h"),
        ("stats_trend", stats,  "fetch_stats_trend"),
        ("lineup",      lineup, "fetch_lineup"),
    ]:
        if isinstance(value, Exception):
            errors.append(f"{label} error: {value}")
            result[key] = None
        else:
            result[key] = value

    result["errors"] = errors
    return result


async def fetch_historical_node(state: AgentState) -> dict[str, Any]:
    """Load team form, risk scores, and Poisson prediction from historical data."""
    from app.services import analytics

    steps  = _step(state, "fetch_historical")
    errors = _errors(state)

    match: Optional[MatchContext] = state.get("match")
    if not match:
        return {"agent_steps": steps, "errors": errors,
                "home_form": None, "away_form": None}

    home_form, away_form = await asyncio.gather(
        asyncio.to_thread(analytics.get_team_form, match.home.name, 10),
        asyncio.to_thread(analytics.get_team_form, match.away.name, 10),
    )

    stats_dict = None
    if state.get("stats_trend") and state["stats_trend"].periods:
        last = state["stats_trend"].periods[-1]
        stats_dict = {
            "home_dangerous_attacks": last.home_dangerous_attacks,
            "away_dangerous_attacks": last.away_dangerous_attacks,
        }

    goal_risk, card_risk = await asyncio.gather(
        asyncio.to_thread(
            analytics.calculate_goal_risk_score,
            match.minute, match.score_home - match.score_away, stats_dict,
        ),
        asyncio.to_thread(analytics.calculate_card_risk_score, match.minute),
    )

    prediction = None
    try:
        from app.services.predictor import ScorePrediction, predict_from_match_context

        raw: ScorePrediction = await asyncio.to_thread(predict_from_match_context, match)
        prediction = ScorePredictionResponse(
            home_team=raw.home_team, away_team=raw.away_team,
            lambda_home=raw.lambda_home, lambda_away=raw.lambda_away,
            home_win_prob=raw.home_win_prob, draw_prob=raw.draw_prob,
            away_win_prob=raw.away_win_prob, over_2_5_prob=raw.over_2_5_prob,
            btts_prob=raw.btts_prob, most_likely_score=raw.most_likely_score,
            most_likely_score_prob=raw.most_likely_score_prob,
            top_scores=raw.top_scores, score_matrix=raw.score_matrix,
            confidence=raw.confidence, model_note=raw.model_note,
        )
    except Exception as exc:
        errors.append(f"predictor error: {exc}")

    return {
        "agent_steps": steps, "errors": errors,
        "home_form": home_form, "away_form": away_form,
        "goal_risk_score": goal_risk, "card_risk_score": card_risk,
        "prediction": prediction,
    }


async def generate_narrative_node(state: AgentState) -> dict[str, Any]:
    """Build enriched context and call LLM for Portuguese narrative."""
    from app.services.narrative import generate_narrative_enriched

    steps  = _step(state, "generate_narrative")
    errors = _errors(state)

    match: Optional[MatchContext] = state.get("match")
    if not match:
        return {"agent_steps": steps,
                "errors": errors + ["No match context to narrate"],
                "narrative": None}

    try:
        narrative = await generate_narrative_enriched(
            match=match,
            h2h=state.get("h2h"),
            stats_trend=state.get("stats_trend"),
            home_form=state.get("home_form"),
            away_form=state.get("away_form"),
            goal_risk=state.get("goal_risk_score"),
            card_risk=state.get("card_risk_score"),
            prediction=state.get("prediction"),
        )
    except Exception as exc:
        errors.append(f"narrative error: {exc}")
        narrative = None

    return {"agent_steps": steps, "errors": errors, "narrative": narrative}
