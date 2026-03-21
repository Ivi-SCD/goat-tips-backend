from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.services.betsapi import get_live_matches, get_upcoming_matches, get_match_by_id
from app.services.narrative import generate_narrative, answer_question
from app.models import MatchContext, NarrativeResponse

router = APIRouter(prefix="/matches", tags=["matches"])


# ── Listagem ─────────────────────────────────────────────────────────────────

@router.get("/live", response_model=list[MatchContext])
async def list_live():
    """Retorna todas as partidas da Premier League ao vivo com probabilidades calculadas."""
    return await get_live_matches()


@router.get("/upcoming", response_model=list[MatchContext])
async def list_upcoming():
    """Retorna próximos jogos da Premier League com odds pré-jogo."""
    return await get_upcoming_matches()


# ── Análise narrativa ─────────────────────────────────────────────────────────

@router.post("/narrative/{event_id}", response_model=NarrativeResponse)
async def get_narrative(event_id: str):
    """
    Gera análise narrativa completa de uma partida.
    Busca o contexto ao vivo e interpreta via LLM.
    """
    match = await get_match_by_id(event_id)
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return await generate_narrative(match)


# ── Chat contextual ───────────────────────────────────────────────────────────

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