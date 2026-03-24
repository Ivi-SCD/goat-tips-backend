"""
Telegram Service
================
Cliente para interagir com a Telegram Bot API via httpx.
Responsável por enviar mensagens e configurar o webhook.
"""

import httpx
from app.core.settings import get_settings

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
