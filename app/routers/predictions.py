"""
Predictions Router
==================
Routes: Poisson match prediction, full LangGraph agent analysis, narrative, Q&A.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import betsapi
from app.services.narrative import generate_narrative, answer_question
from app.services.predictor import predict_match, predict_from_match_context, ScorePrediction
from app.schemas.match import MatchContext, NarrativeResponse
from app.schemas.prediction import ScorePredictionResponse
from app.schemas.agent import FullMatchAnalysis

router = APIRouter(prefix="/predictions", tags=["Previsões"])


def _pred_to_response(raw: ScorePrediction) -> ScorePredictionResponse:
    return ScorePredictionResponse(
        home_team=raw.home_team, away_team=raw.away_team,
        lambda_home=raw.lambda_home, lambda_away=raw.lambda_away,
        home_win_prob=raw.home_win_prob, draw_prob=raw.draw_prob, away_win_prob=raw.away_win_prob,
        over_2_5_prob=raw.over_2_5_prob, btts_prob=raw.btts_prob,
        most_likely_score=raw.most_likely_score,
        most_likely_score_prob=raw.most_likely_score_prob,
        top_scores=raw.top_scores, score_matrix=raw.score_matrix,
        confidence=raw.confidence, model_note=raw.model_note,
    )


@router.get("/", response_model=ScorePredictionResponse, summary="Prever por nome dos times")
async def predict_by_name(
    home: str = Query(..., description="Nome do time mandante (ex: Arsenal)"),
    away: str = Query(..., description="Nome do time visitante (ex: Chelsea)"),
):
    """
    **Previsão Poisson por nome dos times** — não requer event_id.

    Útil para prever qualquer confronto, incluindo jogos futuros ainda sem ID registrado.
    Modelo treinado em 4,495 jogos da Premier League (2014–2026).

    Exemplo: `GET /predictions/?home=Arsenal&away=Chelsea`
    """
    raw = await asyncio.to_thread(predict_match, home, away)
    return _pred_to_response(raw)


@router.get("/{event_id}", response_model=ScorePredictionResponse,
            summary="Prever por ID de evento")
async def predict_by_event(event_id: str):
    """
    Previsão Poisson usando o event_id da BetsAPI.
    Resolve os nomes dos times automaticamente via `/event/view`.
    """
    match = await betsapi.get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    raw = await asyncio.to_thread(predict_from_match_context, match)
    return _pred_to_response(raw)


@router.get("/{event_id}/full-analysis", response_model=FullMatchAnalysis,
            summary="Análise completa (agente LangGraph)")
async def get_full_analysis(event_id: str):
    """
    **Análise completa via agente LangGraph** — endpoint flagship do produto.

    Orquestra em paralelo:
    - Contexto ao vivo + odds (BetsAPI)
    - Histórico H2H (BetsAPI)
    - Stats trend / momentum (BetsAPI)
    - Escalações (BetsAPI)
    - Forma recente dos times (CSV histórico — 4,495 jogos)
    - Previsão Poisson (gols esperados, placar mais provável)
    - Scores de risco: gol e cartão nos próximos 15 min
    - Narrativa LLM em Português (Azure OpenAI GPT-4.1) com todo o contexto acima

    Tempo médio: 5–15 s dependendo da latência da BetsAPI e Azure OpenAI.
    """
    from app.agents.match_agent import run_full_analysis

    try:
        analysis = await run_full_analysis(event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no agente: {e}")
    return analysis


@router.post("/{event_id}/narrative", response_model=NarrativeResponse,
             summary="Narrativa simples (sem agente)")
async def get_narrative(event_id: str):
    """Narrativa básica sem o pipeline do agente. Mais rápido, menos contexto."""
    match = await betsapi.get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return await generate_narrative(match)


class QuestionRequest(BaseModel):
    question: str


@router.post("/{event_id}/ask", response_model=NarrativeResponse,
             summary="Pergunta livre sobre a partida")
async def ask_about_match(event_id: str, body: QuestionRequest):
    """
    Responde uma pergunta em linguagem natural sobre a partida.

    Exemplos:
    - "Por que o time da casa está perdendo?"
    - "O que pode mudar nos próximos 15 minutos?"
    - "Qual é a chance de empate agora?"
    """
    match = await betsapi.get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return await answer_question(match, body.question)
