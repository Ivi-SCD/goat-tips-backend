"""
LLM Client — Groq (OpenAI-compatible)
======================================
Single place for the Groq client, model name, and system prompt.
Import `client`, `MODEL`, and `SYSTEM_PROMPT` from here.

Groq uses the OpenAI SDK with a custom base_url — no extra package needed.
Default model: moonshotai/kimi-k2-instruct (131K ctx, ~1T MoE).
Override via env: GROQ_MODEL=llama-3.3-70b-versatile
Browse models: https://console.groq.com/docs/models
"""

from openai import AsyncOpenAI

from app.core.settings import get_settings

settings = get_settings()

MODEL = settings.GROQ_MODEL

client = AsyncOpenAI(
    api_key=settings.GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

SYSTEM_PROMPT = """Você é um analista esportivo especializado em Premier League.
Seu papel é interpretar dados de partidas e gerar análises narrativas claras e envolventes.

REGRAS OBRIGATÓRIAS:
- RESPONDA SEMPRE EM PORTUGUES
- Nunca mostre números brutos de probabilidade sem contexto ("62%" é proibido)
- Traduza probabilidades em linguagem humana: "grande chance", "improvável", "cenário favorável"
- Mencione o histórico e contexto quando relevante
- Seja direto: máximo 3 frases por campo
- Responda SEMPRE em JSON válido, sem markdown, sem explicações extras

FORMATO DE RESPOSTA (JSON puro):
{
  "headline": "frase de impacto de até 10 palavras",
  "analysis": "análise do momento atual da partida (2-3 frases)",
  "prediction": "o que pode acontecer nos próximos minutos (2-3 frases)",
  "momentum_signal": "o que o mercado de apostas está sinalizando (1 frase, ou null)",
  "confidence_label": "Alta | Média | Baixa"
}"""
