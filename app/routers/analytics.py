"""
Analytics Router
================
Routes: historical team stats, H2H, goal/card patterns, risk scores.
All data comes from local CSV dataset — no external API calls.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Query

from app.services import analytics
from app.schemas.analytics import TeamForm, H2HRecord, GoalPatterns, CardPatterns

router = APIRouter(prefix="/analytics", tags=["Analytics Histórico"])


@router.get("/teams", summary="Listar todos os times")
async def list_teams():
    """Lista os 35 times presentes no dataset histórico (Premier League 2014–2026)."""
    teams = await asyncio.to_thread(analytics.get_teams)
    return {"teams": teams, "total": len(teams)}


@router.get("/teams/{team_name}/form", response_model=TeamForm, summary="Forma recente")
async def get_team_form(
    team_name: str,
    n: int = Query(default=10, ge=1, le=50, description="Número de últimas partidas"),
):
    """
    Retorna os últimos N jogos do time com resultado, gols marcados/sofridos e forma.
    Nome é case-insensitive e suporta correspondência parcial.
    """
    form = await asyncio.to_thread(analytics.get_team_form, team_name, n)
    if not form:
        raise HTTPException(404, detail=f"Time '{team_name}' não encontrado no dataset")
    return form


@router.get("/teams/{team_name}/stats", summary="Estatísticas históricas do time")
async def get_team_stats(team_name: str):
    """
    Estatísticas agregadas (últimas 50 partidas):
    win rate, média de gols, clean sheet rate, BTTS rate.
    """
    stats = await asyncio.to_thread(analytics.get_team_historical_stats, team_name)
    if not stats:
        raise HTTPException(404, detail=f"Time '{team_name}' não encontrado no dataset")
    return stats


@router.get("/h2h", response_model=H2HRecord, summary="H2H histórico (CSV)")
async def get_h2h(
    home: str = Query(..., description="Time mandante"),
    away: str = Query(..., description="Time visitante"),
    n: int = Query(default=10, ge=1, le=30),
):
    """
    H2H histórico entre dois times extraído do dataset local (4,495 jogos).
    Complementa o H2H da BetsAPI com dados de temporadas anteriores.
    """
    record = await asyncio.to_thread(analytics.get_h2h_history, home, away, n)
    if not record:
        raise HTTPException(404, detail=f"Sem H2H histórico entre '{home}' e '{away}'")
    return record


@router.get("/goal-patterns", response_model=GoalPatterns, summary="Padrão de gols por minuto")
async def get_goal_patterns():
    """
    Distribuição de 9,508 gols por intervalos de 15 minutos.
    Mostra em qual período do jogo os gols são mais frequentes.
    Resultado cacheado — resposta instantânea após o primeiro acesso.
    """
    return await asyncio.to_thread(analytics.get_goal_patterns)


@router.get("/card-patterns", response_model=CardPatterns, summary="Padrão de cartões por minuto")
async def get_card_patterns():
    """
    Distribuição de 11,391 cartões (11,041 amarelos + 350 vermelhos) por intervalo de 15 min.
    Resultado cacheado — resposta instantânea após o primeiro acesso.
    """
    return await asyncio.to_thread(analytics.get_card_patterns)


@router.get("/risk-scores", summary="Scores de risco ao vivo")
async def get_risk_scores(
    minute: int = Query(..., ge=0, le=120, description="Minuto atual da partida"),
    score_diff: int = Query(default=0, description="Gols mandante − gols visitante"),
):
    """
    Calcula scores de risco (0–10) para os próximos 15 minutos:
    - **goal_risk**: probabilidade de gol baseada em padrões históricos + contexto
    - **card_risk**: risco de cartão baseado em padrões históricos + minuto

    Use para alimentar medidores visuais de risco no frontend.
    """
    goal_risk = await asyncio.to_thread(analytics.calculate_goal_risk_score, minute, score_diff)
    card_risk = await asyncio.to_thread(analytics.calculate_card_risk_score, minute)

    def label(s: float) -> str:
        return "Alto" if s >= 7 else ("Médio" if s >= 4 else "Baixo")

    return {
        "minute": minute,
        "score_diff": score_diff,
        "goal_risk": {"score": goal_risk, "label": label(goal_risk)},
        "card_risk": {"score": card_risk, "label": label(card_risk)},
    }
