"""
Analytics endpoints — powered by historical Premier League dataset (4,585 matches).

All analytics run against local CSVs, no external API calls.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Query
from app.services import analytics
from app.models import TeamForm, GoalPatterns, CardPatterns, H2HRecord

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def list_teams():
    """
    Returns all Premier League teams in the historical dataset.
    Useful for autocomplete and team selection in the frontend.
    """
    teams = await asyncio.to_thread(analytics.get_all_teams)
    return {"teams": teams, "total": len(teams)}


@router.get("/teams/{team_name}/form", response_model=TeamForm)
async def get_team_form(team_name: str, n: int = Query(default=10, ge=1, le=50)):
    """
    Returns last N match results for a team with win/loss/draw breakdown.
    Team name is case-insensitive and supports partial matching.
    """
    form = await asyncio.to_thread(analytics.get_team_form, team_name, n)
    if not form:
        raise HTTPException(
            status_code=404,
            detail=f"Team '{team_name}' not found in historical data"
        )
    return form


@router.get("/teams/{team_name}/stats")
async def get_team_stats(team_name: str):
    """
    Returns aggregated historical stats for a team:
    win rate, goals averages, clean sheet rate, BTTS rate.
    """
    stats = await asyncio.to_thread(analytics.get_team_historical_stats, team_name)
    if not stats:
        raise HTTPException(
            status_code=404,
            detail=f"Team '{team_name}' not found in historical data"
        )
    return stats


# ── Head-to-head ──────────────────────────────────────────────────────────────

@router.get("/h2h", response_model=H2HRecord)
async def get_h2h(
    home: str = Query(..., description="Home team name"),
    away: str = Query(..., description="Away team name"),
    n: int = Query(default=10, ge=1, le=30),
):
    """
    Returns historical H2H record between two teams from our dataset.
    Use for pre-match context and head-to-head comparison widgets.
    """
    record = await asyncio.to_thread(analytics.get_h2h_history, home, away, n)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No H2H history found between '{home}' and '{away}'"
        )
    return record


# ── Pattern analysis ──────────────────────────────────────────────────────────

@router.get("/goal-patterns", response_model=GoalPatterns)
async def get_goal_patterns():
    """
    Returns goal timing distribution across all 4,585 historical Premier League matches.
    Shows which 15-minute intervals are most dangerous for goals.

    This is cached after first call — fast on subsequent requests.
    """
    patterns = await asyncio.to_thread(analytics.get_goal_patterns)
    return patterns


@router.get("/card-patterns", response_model=CardPatterns)
async def get_card_patterns():
    """
    Returns yellow/red card timing distribution across all historical matches.
    Useful for live card risk visualization.

    This is cached after first call — fast on subsequent requests.
    """
    patterns = await asyncio.to_thread(analytics.get_card_patterns)
    return patterns


# ── Risk scores ───────────────────────────────────────────────────────────────

@router.get("/risk-scores")
async def get_risk_scores(
    minute: int = Query(..., ge=0, le=120, description="Current match minute"),
    score_diff: int = Query(default=0, description="Home score - away score (negative = home losing)"),
):
    """
    Returns goal and card risk scores (0-10) for the current match state.
    Based on historical timing patterns + game context heuristics.

    Use for live match risk meters in the UI.
    """
    goal_risk = await asyncio.to_thread(
        analytics.calculate_goal_risk_score, minute, score_diff
    )
    card_risk = await asyncio.to_thread(
        analytics.calculate_card_risk_score, minute
    )

    def _risk_label(score: float) -> str:
        if score >= 7:
            return "Alto"
        elif score >= 4:
            return "Médio"
        return "Baixo"

    return {
        "minute": minute,
        "score_diff": score_diff,
        "goal_risk": {
            "score": goal_risk,
            "label": _risk_label(goal_risk),
        },
        "card_risk": {
            "score": card_risk,
            "label": _risk_label(card_risk),
        },
    }
