import asyncio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.services.betsapi import (
    get_live_matches, get_upcoming_matches, get_match_by_id,
    get_h2h, get_stats_trend, get_lineup, get_league_toplist,
    PREMIER_LEAGUE_ID,
)
from app.services.narrative import generate_narrative, answer_question
from app.models import (
    MatchContext, NarrativeResponse, StatsTrend, H2HRecord,
    LineupInfo, FullMatchAnalysis, LeagueToplist, ScorePredictionResponse,
)

router = APIRouter(prefix="/matches", tags=["matches"])


def _to_prediction_response(raw) -> ScorePredictionResponse:
    return ScorePredictionResponse(
        home_team=raw.home_team,
        away_team=raw.away_team,
        lambda_home=raw.lambda_home,
        lambda_away=raw.lambda_away,
        home_win_prob=raw.home_win_prob,
        draw_prob=raw.draw_prob,
        away_win_prob=raw.away_win_prob,
        over_2_5_prob=raw.over_2_5_prob,
        btts_prob=raw.btts_prob,
        most_likely_score=raw.most_likely_score,
        most_likely_score_prob=raw.most_likely_score_prob,
        top_scores=raw.top_scores,
        score_matrix=raw.score_matrix,
        confidence=raw.confidence,
        model_note=raw.model_note,
    )


# ── Static routes MUST come before /{event_id} ────────────────────────────────

@router.get("/live", response_model=list[MatchContext])
async def list_live():
    """Retorna todas as partidas da Premier League ao vivo com probabilidades calculadas."""
    return await get_live_matches()


@router.get("/upcoming", response_model=list[MatchContext])
async def list_upcoming():
    """Retorna próximos jogos da Premier League com odds pré-jogo."""
    return await get_upcoming_matches()


@router.get("/toplist", response_model=LeagueToplist)
async def get_toplist(league_id: int = Query(default=PREMIER_LEAGUE_ID)):
    """
    Artilheiros e garçons da liga.
    Use para contexto narrativo: "o artilheiro está em campo hoje".
    """
    toplist = await get_league_toplist(league_id)
    if not toplist:
        raise HTTPException(status_code=503, detail="Não foi possível buscar toplist da liga")
    return toplist


@router.get("/predict", response_model=ScorePredictionResponse)
async def predict_by_name(
    home: str = Query(..., description="Home team name"),
    away: str = Query(..., description="Away team name"),
):
    """
    **Previsão Poisson por nome dos times** (sem precisar de event_id).
    Útil para prever qualquer partida, incluindo futuras sem ID registrado.
    Ex: `/matches/predict?home=Arsenal&away=Chelsea`
    """
    from app.services.predictor import predict_match
    raw = await asyncio.to_thread(predict_match, home, away)
    return _to_prediction_response(raw)


# ── Dynamic /{event_id} routes ────────────────────────────────────────────────

@router.get("/{event_id}", response_model=MatchContext)
async def get_match(event_id: str):
    """Retorna contexto completo de uma partida específica com odds e kick-off time."""
    match = await get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return match


@router.get("/{event_id}/h2h", response_model=H2HRecord)
async def get_match_h2h(event_id: str):
    """
    Histórico H2H dos dois times via BetsAPI.
    Retorna partidas anteriores, vitórias, empates e médias de gols.
    """
    record = await get_h2h(event_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail="Histórico H2H não disponível para esta partida"
        )
    return record


@router.get("/{event_id}/stats-trend", response_model=StatsTrend)
async def get_match_stats_trend(event_id: str):
    """
    Estatísticas por período com momentum calculado.
    Retorna chutes, escanteios, posse e ataques perigosos — base para o momentum widget.
    """
    trend = await get_stats_trend(event_id)
    if not trend:
        raise HTTPException(
            status_code=404,
            detail="Stats trend não disponível para esta partida"
        )
    return trend


@router.get("/{event_id}/lineup", response_model=LineupInfo)
async def get_match_lineup(event_id: str):
    """
    Escalações confirmadas dos dois times.
    Inclui formação, XI inicial e banco de reservas.
    """
    lineup = await get_lineup(event_id)
    if not lineup:
        raise HTTPException(
            status_code=404,
            detail="Escalação não disponível para esta partida"
        )
    return lineup


@router.get("/{event_id}/prediction", response_model=ScorePredictionResponse)
async def get_prediction(event_id: str):
    """
    **Previsão estatística via Modelo Poisson.**

    Treinado em 4,585 jogos da Premier League (2014–2026).
    Retorna: gols esperados, placar mais provável, top 5 placares,
    win/draw/loss, over 2.5, BTTS e matriz completa de probabilidades.
    """
    from app.services.predictor import predict_match
    match = await get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    raw = await asyncio.to_thread(predict_match, match.home.name, match.away.name)
    return _to_prediction_response(raw)


@router.get("/{event_id}/full-analysis", response_model=FullMatchAnalysis)
async def get_full_analysis(event_id: str):
    """
    **Análise completa via agente LangGraph.**

    Orquestra em paralelo:
    - Contexto ao vivo + odds
    - H2H da BetsAPI
    - Stats trend (momentum tático)
    - Escalações
    - Forma recente dos times (histórico CSV)
    - Previsão Poisson (gols esperados, placar mais provável)
    - Scores de risco: gol e cartão nos próximos 15 min
    - Narrativa LLM enriquecida com todo o contexto

    Este é o endpoint principal para a experiência completa do produto.
    """
    from app.agents.match_agent import run_full_analysis

    try:
        analysis = await run_full_analysis(event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no agente: {e}")

    return analysis


# ── Narrative & chat ──────────────────────────────────────────────────────────

@router.post("/narrative/{event_id}", response_model=NarrativeResponse)
async def get_narrative(event_id: str):
    """
    Gera análise narrativa básica (sem enriquecimento do agente).
    Use /full-analysis para a versão completa.
    """
    match = await get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return await generate_narrative(match)


class QuestionRequest(BaseModel):
    question: str


@router.post("/{event_id}/ask", response_model=NarrativeResponse)
async def ask_about_match(event_id: str, body: QuestionRequest):
    """
    Responde uma pergunta livre sobre a partida.
    Ex: 'Por que estamos perdendo?' / 'O que pode mudar nos próximos 15 minutos?'
    """
    match = await get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return await answer_question(match, body.question)
