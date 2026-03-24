"""
Telegram Service
================
Cliente para interagir com a Telegram Bot API via httpx.
Responsável por enviar mensagens, configurar o webhook e publicar narrativas no canal.
"""

import logging
import httpx
from app.core.settings import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def _url(method: str) -> str:
    token = get_settings().TELEGRAM_TOKEN
    return TELEGRAM_API.format(token=token, method=method)


async def send_message(chat_id: int | str, text: str, parse_mode: str = "HTML") -> dict:
    """Envia uma mensagem de texto para um chat do Telegram."""
    # Telegram tem limite de 4096 caracteres por mensagem
    if len(text) > 4096:
        text = text[:4090] + "…"

    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_url("sendMessage"), json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_chat_action(chat_id: int | str, action: str = "typing") -> None:
    """Envia indicador de atividade (ex: 'typing') ao usuário."""
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post(_url("sendChatAction"), json={"chat_id": chat_id, "action": action})


async def set_webhook(webhook_url: str) -> dict:
    """Registra a URL do webhook no Telegram."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_url("setWebhook"), json={"url": webhook_url})
        resp.raise_for_status()
        return resp.json()


async def delete_webhook() -> dict:
    """Remove o webhook registrado."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(_url("deleteWebhook"))
        resp.raise_for_status()
        return resp.json()


async def get_webhook_info() -> dict:
    """Retorna informações do webhook atual."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_url("getWebhookInfo"))
        resp.raise_for_status()
        return resp.json()


def _format_narrative(home: str, away: str, headline: str, analysis: str, prediction: str, momentum_signal: str | None, confidence_label: str) -> str:
    """Formata uma narrativa para publicação no canal."""
    lines = [
        f"⚽ <b>{home} x {away}</b>",
        "",
        f"<b>{headline}</b>",
        "",
        analysis,
        "",
        f"📊 <b>Previsão:</b> {prediction}",
    ]
    if momentum_signal:
        lines += ["", f"📈 <b>Momentum:</b> {momentum_signal}"]
    lines += ["", f"🎯 Confiança: <i>{confidence_label}</i>"]
    return "\n".join(lines)


async def publish_narrative_to_channel(
    home: str,
    away: str,
    headline: str,
    analysis: str,
    prediction: str,
    confidence_label: str,
    momentum_signal: str | None = None,
) -> None:
    """
    Publica uma narrativa de partida no canal @goat_tips_32.
    Falha silenciosamente para não impactar o endpoint principal.
    """
    channel = get_settings().TELEGRAM_CHANNEL_ID
    if not get_settings().TELEGRAM_TOKEN or not channel:
        return

    text = _format_narrative(home, away, headline, analysis, prediction, momentum_signal, confidence_label)

    try:
        await send_message(channel, text)
    except Exception as exc:
        logger.warning("Falha ao publicar narrativa no canal Telegram: %s", exc)
