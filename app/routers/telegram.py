"""
Telegram Router
===============
Recebe updates do Telegram via webhook e responde usando o pipeline /ask.

Fluxo:
  Telegram → POST /telegram/webhook → ask_general_question → sendMessage → Usuário

Configuração do webhook:
  POST /telegram/set-webhook?url=https://seu-dominio.com/telegram/webhook
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.services import conversation
from app.services.narrative import answer_general_question
from app.services import telegram as tg_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["Telegram"])


# ---------------------------------------------------------------------------
# Modelos internos (sem Pydantic extra — usamos dict direto do JSON do Telegram)
# ---------------------------------------------------------------------------

def _extract_message(update: dict[str, Any]) -> tuple[int | None, str | None, str | None]:
    """
    Extrai (chat_id, user_id_str, text) de um update do Telegram.
    Suporta mensagens normais e edições.
    Retorna (None, None, None) se o update não tiver mensagem de texto.
    """
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None, None, None

    chat_id = msg.get("chat", {}).get("id")
    user_id = str(msg.get("from", {}).get("id", "unknown"))
    text = msg.get("text", "").strip()

    return chat_id, user_id, text


def _format_response(headline: str, analysis: str) -> str:
    """Formata a resposta do agente para envio via Telegram (HTML)."""
    headline_clean = headline.strip()
    analysis_clean = analysis.strip()

    lines = [f"<b>{headline_clean}</b>", "", analysis_clean]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/webhook", summary="Webhook do Telegram (recebe updates)")
async def telegram_webhook(request: Request) -> Response:
    """
    Endpoint chamado pelo Telegram a cada nova mensagem.

    O Telegram exige resposta HTTP 200 em até ~5s; o processamento pesado
    é disparado em background para não bloquear o retorno.
    """
    update: dict[str, Any] = await request.json()

    # Dispara processamento em background e retorna 200 imediatamente
    asyncio.create_task(_handle_update(update))

    return Response(status_code=200)


async def _handle_update(update: dict[str, Any]) -> None:
    """Processa um update do Telegram de forma assíncrona."""
    chat_id, user_id, text = _extract_message(update)

    if not chat_id or not text:
        return  # Ignora updates sem mensagem de texto (fotos, stickers, etc.)

    # Ignora comandos do sistema (ex: /start, /help)
    if text.startswith("/"):
        await _handle_command(chat_id, text)
        return

    # Indica que está digitando
    await tg_service.send_chat_action(chat_id, "typing")

    # Usa o user_id como session_id para manter histórico por usuário
    session_id = f"tg_{user_id}"

    try:
        history = await conversation.load_history(session_id, "general")
        response = await answer_general_question(text, history=history)

        await conversation.save_turn(
            session_id=session_id,
            event_id="general",
            question=text,
            response_headline=response.headline,
            response_analysis=response.analysis,
        )

        reply = _format_response(response.headline, response.analysis)
        await tg_service.send_message(chat_id, reply)

    except Exception as exc:
        logger.exception("Erro ao processar mensagem do Telegram: %s", exc)
        await tg_service.send_message(
            chat_id,
            "⚠️ Não consegui processar sua pergunta agora. Tente novamente em instantes.",
        )


async def _handle_command(chat_id: int, command: str) -> None:
    """Responde aos comandos básicos do bot."""
    cmd = command.split()[0].lower()

    if cmd == "/start":
        await tg_service.send_message(
            chat_id,
            (
                "<b>Goat Tips ⚽</b>\n\n"
                "Olá! Sou seu assistente de análise da Premier League.\n\n"
                "Pergunte qualquer coisa sobre partidas, times, estatísticas ou previsões.\n\n"
                "Exemplos:\n"
                "• Qual é a forma recente do Arsenal?\n"
                "• Quem são os artilheiros da Premier League?\n"
                "• Qual a probabilidade de gols no próximo Manchester City?"
            ),
        )
    elif cmd == "/help":
        await tg_service.send_message(
            chat_id,
            (
                "<b>Como usar o Goat Tips</b>\n\n"
                "Basta enviar qualquer pergunta em linguagem natural sobre a Premier League.\n\n"
                "Comandos disponíveis:\n"
                "/start — Mensagem de boas-vindas\n"
                "/help — Esta ajuda\n"
                "/clear — Limpa o histórico de conversa"
            ),
        )
    elif cmd == "/clear":
        # Não temos user_id aqui; usamos chat_id como fallback para /clear via comando
        session_id = f"tg_{chat_id}"
        try:
            await conversation.clear_session(session_id, "general")
            await tg_service.send_message(chat_id, "✅ Histórico limpo com sucesso.")
        except Exception:
            await tg_service.send_message(chat_id, "⚠️ Não foi possível limpar o histórico.")


@router.post(
    "/set-webhook",
    summary="Registra o webhook no Telegram",
    description="Chame este endpoint uma vez após o deploy para que o Telegram saiba para onde enviar os updates.",
)
async def set_webhook(
    url: str = Query(..., description="URL pública do webhook (ex: https://meu-app.com/telegram/webhook)"),
):
    result = await tg_service.set_webhook(url)
    return result


@router.delete("/webhook", summary="Remove o webhook do Telegram")
async def delete_webhook():
    result = await tg_service.delete_webhook()
    return result


@router.get("/webhook/info", summary="Informações do webhook atual")
async def webhook_info():
    result = await tg_service.get_webhook_info()
    return result
