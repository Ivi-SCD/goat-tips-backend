"""
Matches Router
==============
Routes: live listing, upcoming listing, match context, H2H, stats trend, lineup, toplist.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Query

from app.services import betsapi
from app.schemas.match import (
    MatchContext, StatsTrend, LineupInfo, LeagueToplist,
)
from app.schemas.analytics import H2HRecord

router = APIRouter(prefix="/matches", tags=["Partidas"])


@router.get("/live", response_model=list[MatchContext], summary="Partidas ao vivo")
async def list_live():
    """Retorna todas as partidas da Premier League ao vivo com odds e probabilidades calculadas."""
    return await betsapi.get_live_matches()


@router.get("/upcoming", response_model=list[MatchContext], summary="Próximas partidas")
async def list_upcoming():
    """
    Retorna os próximos jogos da Premier League com odds pré-jogo.
    Inclui `kick_off_time` (ISO 8601 UTC), rodada, árbitro e estádio.
    """
    return await betsapi.get_upcoming_matches()


@router.get("/toplist", response_model=LeagueToplist, summary="Artilheiros e assistências")
async def get_toplist(league_id: int = Query(default=betsapi.PREMIER_LEAGUE_ID)):
    """Artilheiros e garçons da liga. Útil para contextualizar se o artilheiro está em campo."""
    toplist = await betsapi.get_league_toplist(league_id)
    if not toplist:
        raise HTTPException(status_code=503, detail="Toplist indisponível no momento")
    return toplist


@router.get("/{event_id}", response_model=MatchContext, summary="Contexto de uma partida")
async def get_match(event_id: str):
    """Retorna contexto completo de uma partida: placar, odds, kick-off, rodada e árbitro."""
    match = await betsapi.get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return match


@router.get("/{event_id}/h2h", response_model=H2HRecord, summary="Histórico H2H")
async def get_match_h2h(event_id: str):
    """H2H dos dois times via BetsAPI: últimas partidas, vitórias, empates e médias de gols."""
    record = await betsapi.get_h2h(event_id)
    if not record:
        raise HTTPException(status_code=404, detail="H2H não disponível para esta partida")
    return record


@router.get("/{event_id}/stats-trend", response_model=StatsTrend, summary="Momentum tático")
async def get_match_stats_trend(event_id: str):
    """
    Estatísticas por período (1º tempo, 2º tempo).
    Retorna chutes, escanteios, posse e ataques perigosos.
    Inclui `momentum_score` (-1 = domínio do visitante, +1 = domínio do mandante).
    """
    trend = await betsapi.get_stats_trend(event_id)
    if not trend:
        raise HTTPException(status_code=404, detail="Stats trend não disponível")
    return trend


@router.get("/{event_id}/lineup", response_model=LineupInfo, summary="Escalações")
async def get_match_lineup(event_id: str):
    """Escalações confirmadas: XI inicial, banco de reservas e formação tática."""
    lineup = await betsapi.get_lineup(event_id)
    if not lineup:
        raise HTTPException(status_code=404, detail="Escalação não disponível")
    return lineup
